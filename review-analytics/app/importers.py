"""Import reviews from the existing 顾客说 (明道云) tool.

Two inputs are supported:

* ``*.ndjson`` produced by ``scripts/pull_portal.js`` (one raw row per line, keyed by
  control id) together with the ``*.schema.json`` saved beside it;
* ``*.xlsx`` exported from the 明道云 worksheet (header row = field names).

Both are reduced to a dict keyed by field *name*, then mapped onto the ``reviews`` table.

    python -m app.importers data/reviews.ndjson
    python -m app.importers export.xlsx
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from .db import connect
from .normalize import resolve_store

# 明道云 control types whose values are JSON arrays of option keys
_OPTION_TYPES = {9, 10, 11}
_RELATION_TYPES = {29, 35}
_ATTACH_TYPES = {14}


def _load_schema(ndjson_path: Path) -> dict:
    schema_path = ndjson_path.with_suffix("").with_suffix(".schema.json")
    if not schema_path.exists():
        schema_path = ndjson_path.parent / (ndjson_path.stem + ".schema.json")
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def _decode(control: dict, value):
    """Turn a raw 明道云 cell into plain text / number."""
    if value in (None, "", "[]"):
        return None
    t = control.get("type")
    if t in _OPTION_TYPES:
        opts = {o["key"]: o["value"] for o in control.get("options") or []}
        try:
            keys = json.loads(value)
        except (TypeError, ValueError):
            return value
        return ",".join(opts.get(k, k) for k in keys) or None
    if t in _RELATION_TYPES:
        try:
            items = json.loads(value)
            return ",".join(i.get("name", "") for i in items if isinstance(i, dict)) or None
        except (TypeError, ValueError):
            return value
    if t in _ATTACH_TYPES:
        try:
            return len(json.loads(value))
        except (TypeError, ValueError):
            return None
    return value


def rows_from_ndjson(path: Path):
    schema = _load_schema(path)
    controls = {c["controlId"]: c for c in schema["controls"]}
    seen: dict[str, dict] = {}
    for c in schema["controls"]:
        # several controls share a name (two 情绪原因, two 是否高V评价); keep the first, most-filled one
        seen.setdefault(c["controlName"], c)
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            rec: dict = {}
            for cid, value in raw.items():
                c = controls.get(cid)
                if not c:
                    continue
                name = c["controlName"]
                v = _decode(c, value)
                if v is None:
                    continue
                if name in rec and seen[name]["controlId"] != cid:
                    continue
                rec[name] = v
            rec["记录ID"] = raw.get("rowid")
            rec["创建时间"] = raw.get("ctime")
            yield rec


def rows_from_xlsx(path: Path):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(it)]
    for row in it:
        rec = {h: v for h, v in zip(header, row) if h and v not in (None, "")}
        if rec:
            yield rec


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _dt(v) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)[:19]


def upsert_review(conn, rec: dict, source: str) -> bool:
    rid = rec.get("记录ID") or rec.get("rowid")
    if not rid:
        return False
    review_time = _dt(rec.get("评价时间"))
    if review_time and review_time < "2000":  # a handful of rows carry an unset (1970) timestamp
        review_time = None
    store_raw = rec.get("平台店铺名") or rec.get("门店")
    store_id = resolve_store(conn, store_raw)
    has_reply = rec.get("是否已回复")
    conn.execute(
        """
        INSERT INTO reviews (id, source, platform, store_raw, store_id, review_time, review_date, rating, star,
            content, photo_count, customer_id, nickname, customer_level, high_v, review_count, entry_type,
            package_name, package_price, verify_date, has_reply, reply_text,
            legacy_emotion, legacy_reasons, legacy_dishes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            platform=excluded.platform, store_raw=excluded.store_raw, store_id=excluded.store_id,
            review_time=excluded.review_time, review_date=excluded.review_date, rating=excluded.rating,
            star=excluded.star, content=excluded.content, photo_count=excluded.photo_count,
            customer_level=excluded.customer_level, high_v=excluded.high_v, review_count=excluded.review_count,
            entry_type=excluded.entry_type, package_name=excluded.package_name, package_price=excluded.package_price,
            verify_date=excluded.verify_date, has_reply=excluded.has_reply, reply_text=excluded.reply_text,
            legacy_emotion=excluded.legacy_emotion, legacy_reasons=excluded.legacy_reasons,
            legacy_dishes=excluded.legacy_dishes
        """,
        (
            str(rid), source, rec.get("评价渠道"), store_raw, store_id, review_time,
            review_time[:10] if review_time else None,
            _num(rec.get("打分")), _int(rec.get("星级")), rec.get("评价内容"),
            _int(rec.get("点评照片")) or _int(rec.get("附件数量")) or 0,
            rec.get("顾客ID"), rec.get("顾客昵称"), _int(rec.get("顾客级别")), rec.get("是否高V评价"),
            _int(rec.get("评价次数")), rec.get("评价入口"), rec.get("套餐名称"), _num(rec.get("套餐价格")),
            _dt(rec.get("核销日期")),
            1 if has_reply == "已回复" else (0 if has_reply == "未回复" else None),
            rec.get("商家回复"), rec.get("情绪"), rec.get("情绪原因"), rec.get("菜品名称"),
            _dt(rec.get("创建时间")),
        ),
    )
    return True


def import_file(path: Path, conn=None) -> int:
    conn = conn or connect()
    rows = rows_from_xlsx(path) if path.suffix.lower() in (".xlsx", ".xlsm") else rows_from_ndjson(path)
    source = "xlsx" if path.suffix.lower().startswith(".xls") else "portal"
    n = 0
    with conn:
        for rec in rows:
            if upsert_review(conn, rec, source):
                n += 1
    return n


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    total = 0
    for arg in sys.argv[1:]:
        n = import_file(Path(arg))
        print(f"{arg}: {n} rows")
        total += n
    print("imported", total)
