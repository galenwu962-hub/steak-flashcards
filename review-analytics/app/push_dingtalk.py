"""Append one period's improvement items to the 钉钉 AI 表格「门店改善事项跟进」.

    python -m app.push_dingtalk --from 2026-09-26 --to 2026-10-25 [--issued 2026-10-26] [--dry-run]

Same table and fields as the first batch (HANDOFF.md §6): one record per action_items row, 事项编号 =
action_items.id, 状态 待处理, 下发日期 = --issued (default today), 截止日期 = 下发 + 1/3/7/14 days for
红线/高/中/低. Items judged 仅知晓 (kind 知晓) get no deadline, so the reminders skip them.
Items whose 事项编号 is already in the table are skipped, so a rerun never duplicates; if the table
cannot be read the script stops rather than risk writing duplicates.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone

from .db import connect
from .sync_dingtalk import BASE_ID, F_ITEM_ID, TABLE_ID, fetch_records
from .weekly import short

DUE_DAYS = {"红线": 1, "高": 3, "中": 7, "低": 14}
CST = timezone(timedelta(hours=8))
F = {  # field ids in the table
    "title": "IIrTB7i", "store": "0XjnONA", "status": "ofRgdfn", "priority": "1i1XXww", "redline": "3pCKIBI",
    "category": "7Qv5e3b", "owner": "HpJkQZ4", "action": "ZnAnSQ4", "quotes": "satF8VY", "reviews": "Jzs930T",
    "issued": "iLKHhYB", "period": "3ZZhbMm", "item_id": F_ITEM_ID, "kind": "Rxzh58D", "due": "JhkRP59",
    "note": "WlgwxOy",
}


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=CST).timestamp() * 1000)


def build(conn, d0: str, d1: str, issued: date) -> list[dict]:
    rows = conn.execute(
        """SELECT i.*, s.name AS store FROM action_items i JOIN stores s ON s.id = i.store_id
           WHERE i.period_from = ? AND i.period_to = ? ORDER BY s.name, i.id""", (d0, d1)).fetchall()
    out = []
    for it in rows:
        ev = json.loads(it["evidence_json"] or "[]")
        quotes = "\n".join(f"「{e.get('quote', '')}」{e.get('star') if e.get('star') is not None else '-'}星 {e.get('review_date', '')}"
                           for e in ev if e.get("quote"))
        aware = it["kind"] == "知晓"
        level = "红线" if it["escalate"] else it["priority"]
        cells = {
            F["title"]: it["title"], F["store"]: short(it["store"]), F["status"]: "待处理",
            F["priority"]: it["priority"], F["redline"]: "是" if it["escalate"] else "否",
            F["category"]: it["category"], F["owner"]: it["owner"], F["action"]: it["action"],
            F["quotes"]: quotes, F["reviews"]: it["review_count"] or len({e.get("review_id") for e in ev}),
            F["issued"]: _ms(issued), F["period"]: f"{d0} 至 {d1}", F["item_id"]: it["id"],
            F["kind"]: it["kind"] or "整改",
        }
        if aware:
            cells[F["note"]] = it["kind_reason"] or "仅知晓，留意即可"
        else:
            cells[F["due"]] = _ms(issued + timedelta(days=DUE_DAYS.get(level, 7)))
        out.append({"cells": {k: v for k, v in cells.items() if v not in (None, "")}})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--issued", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    conn = connect()
    records = build(conn, args.d0, args.d1, date.fromisoformat(args.issued))
    if not records:
        raise SystemExit(f"{args.d0}–{args.d1} 没有改善事项，先跑 app.actions")
    existing = {int(float(r["cells"][F_ITEM_ID])) for r in fetch_records() if r["cells"].get(F_ITEM_ID)}
    new = [r for r in records if r["cells"][F["item_id"]] not in existing]
    print(f"本期 {len(records)} 条，表里已有 {len(records) - len(new)} 条，待追加 {len(new)} 条")
    if args.dry_run:
        print(json.dumps(new[:2], ensure_ascii=False, indent=1))
        return
    for i in range(0, len(new), 50):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(new[i:i + 50], f, ensure_ascii=False)
        subprocess.run(["dws", "aitable", "record", "create", "--base-id", BASE_ID, "--table-id", TABLE_ID,
                        "--records-file", f.name, "--yes", "--format", "json"], check=True, capture_output=True)
    after = {int(float(r["cells"][F_ITEM_ID])) for r in fetch_records() if r["cells"].get(F_ITEM_ID)}
    missing = [r["cells"][F["item_id"]] for r in new if r["cells"][F["item_id"]] not in after]
    print(f"已追加 {len(new) - len(missing)} 条" + (f"；没写进去的事项编号：{missing}" if missing else ""))


if __name__ == "__main__":
    main()
