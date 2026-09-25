"""Import per-store daily business metrics (开店宝每日数据) into ``daily_metrics``.

The source table has three rows per store and day, one per 平台 (全平台 / 美团 / 点评).
We keep the 全平台 row for traffic and sales, and take the star ratings from whichever
row carries them.

    python -m app.import_metrics data/traffic.ndjson
"""
from __future__ import annotations

import sys
from pathlib import Path

from .db import connect
from .importers import _num, _int, rows_from_ndjson, rows_from_xlsx
from .normalize import resolve_store


def import_metrics(path: Path, conn=None) -> int:
    conn = conn or connect()
    rows = rows_from_xlsx(path) if path.suffix.lower().startswith(".xls") else rows_from_ndjson(path)
    merged: dict[tuple[int, str], dict] = {}
    for rec in rows:
        store_id = resolve_store(conn, rec.get("门店名称"), create=False)
        day = str(rec.get("日期") or "")[:10]
        if not store_id or not day:
            continue
        m = merged.setdefault((store_id, day), {})
        platform = rec.get("平台") or "全平台"
        if platform in ("美团+点评", "全平台", "全部"):
            m.update({
                "exposure": _int(rec.get("曝光人数")),
                "visits": _int(rec.get("访问人数")) or _int(rec.get("访问次数")),
                "buyers": _int(rec.get("购买人数")) or _int(rec.get("消费人数")) or _int(rec.get("成交人数")),
                "orders": _int(rec.get("成交订单数")) or _int(rec.get("消费笔数")),
                "revenue": _num(rec.get("用户实付金额")) or _num(rec.get("消费金额")),
                "favorites": _int(rec.get("新增收藏人数")),
                "checkins": _int(rec.get("打卡人数")),
            })
        if rec.get("点评星级"):
            m["dp_rating"] = _num(rec.get("点评星级"))
        if rec.get("美团星级"):
            m["mt_rating"] = _num(rec.get("美团星级"))
    with conn:
        for (store_id, day), m in merged.items():
            conn.execute(
                """INSERT INTO daily_metrics (store_id, date, exposure, visits, buyers, orders, revenue, favorites, checkins, dp_rating, mt_rating)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(store_id, date) DO UPDATE SET
                     exposure=coalesce(excluded.exposure, exposure), visits=coalesce(excluded.visits, visits),
                     buyers=coalesce(excluded.buyers, buyers), orders=coalesce(excluded.orders, orders),
                     revenue=coalesce(excluded.revenue, revenue), favorites=coalesce(excluded.favorites, favorites),
                     checkins=coalesce(excluded.checkins, checkins), dp_rating=coalesce(excluded.dp_rating, dp_rating),
                     mt_rating=coalesce(excluded.mt_rating, mt_rating)""",
                (store_id, day, m.get("exposure"), m.get("visits"), m.get("buyers"), m.get("orders"), m.get("revenue"),
                 m.get("favorites"), m.get("checkins"), m.get("dp_rating"), m.get("mt_rating")),
            )
    return len(merged)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for arg in sys.argv[1:]:
        print(f"{arg}: {import_metrics(Path(arg))} store-days")
