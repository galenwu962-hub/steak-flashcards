"""Rebuild action_items from the 钉钉 AI 表格 after the local database was lost (HANDOFF.md §1).

    python -m app.restore_items            # insert items whose 事项编号 is missing, keeping that id
    python -m app.restore_items --relink   # afterwards: point evidence at the real reviews by quote text

The table holds everything the reports need except each quote's review id; quotes come back as
「原话」N星 YYYY-MM-DD lines. --relink, run once the reviews are pulled again, finds each quote's
review by its text and date so the weekly counts distinct reviews correctly.
"""
from __future__ import annotations

import argparse
import json
import re

from .db import connect
from .push_dingtalk import F
from .sync_dingtalk import fetch_records
from .weekly import short

QUOTE = re.compile(r"「(.*?)」(\S+?)星 (\d{4}-\d{2}-\d{2})")


def _name(v):
    return v.get("name") if isinstance(v, dict) else v


def restore(conn) -> int:
    stores = {short(n): i for i, n in conn.execute("SELECT id, name FROM stores")}
    have = {r[0] for r in conn.execute("SELECT id FROM action_items")}
    n = 0
    for rec in fetch_records():
        c = rec["cells"]
        if not c.get(F["item_id"]):
            continue
        iid = int(float(c[F["item_id"]]))
        if iid in have:
            continue
        d0, d1 = [x.strip() for x in c[F["period"]].split("至")]
        ev = [{"review_id": f"restored-{iid}-{k}", "quote": q, "star": None if s == "-" else int(s), "review_date": d, "dish": None}
              for k, (q, s, d) in enumerate(QUOTE.findall(c.get(F["quotes"]) or ""))]
        issued = (c.get(F["issued"]) or "")[:10] or d1
        status = "done" if _name(c.get(F["status"])) == "已完成" else "open"
        conn.execute(
            """INSERT INTO action_items (id, store_id, period_from, period_to, created_at, model, title, owner, priority,
               escalate, action, evidence_count, evidence_json, status, done_at, category, review_count, kind, kind_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, stores[_name(c[F["store"]])], d0, d1, issued + " 09:00:00", "restored-from-dingtalk", c[F["title"]],
             _name(c.get(F["owner"])), _name(c.get(F["priority"])), 1 if _name(c.get(F["redline"])) == "是" else 0,
             c.get(F["action"]), len(ev), json.dumps(ev, ensure_ascii=False), status,
             (c.get("soNZuEP") or "")[:10] or None, _name(c.get(F["category"])),
             int(float(c.get(F["reviews"]) or len(ev))), "知晓" if "知晓" in (_name(c.get(F["kind"])) or "") else "整改", c.get(F["note"])))
        n += 1
    conn.commit()
    return n


def relink(conn) -> tuple[int, int]:
    found = total = 0
    for iid, ev_json in conn.execute("SELECT id, evidence_json FROM action_items WHERE model = 'restored-from-dingtalk'").fetchall():
        ev = json.loads(ev_json)
        for e in ev:
            if not e["review_id"].startswith("restored-"):
                continue
            total += 1
            key = e["quote"][:30]
            row = conn.execute("SELECT r.id FROM reviews r JOIN action_items i ON i.store_id = r.store_id WHERE i.id = ? "
                               "AND r.review_date = ? AND instr(r.content, ?) > 0 LIMIT 1", (iid, e["review_date"], key)).fetchone()
            if row:
                e["review_id"] = row[0]
                found += 1
        conn.execute("UPDATE action_items SET evidence_json = ? WHERE id = ?", (json.dumps(ev, ensure_ascii=False), iid))
    conn.commit()
    return found, total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relink", action="store_true")
    args = ap.parse_args()
    conn = connect()
    if args.relink:
        f, t = relink(conn)
        print(f"原话对上评价 {f}/{t} 条")
    else:
        print(f"从 AI 表格恢复 {restore(conn)} 条改善事项")


if __name__ == "__main__":
    main()
