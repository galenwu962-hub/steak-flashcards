"""Pull item status from the 钉钉 AI 表格「门店改善事项跟进」back into action_items.

    python -m app.sync_dingtalk

Stores update the table (状态 / 完成日期); the weekly board reads action_items.status,
so run this before `python -m app.weekly`. Needs a logged-in `dws` (see HANDOFF.md §4).
"""
from __future__ import annotations

import json
import subprocess

from .db import connect

BASE_ID = "Amq4vjg890Dmk3B3sxYMD45nJ3kdP0wQ"
TABLE_ID = "l1kq9Fe"
F_ITEM_ID = "VoRs4Mb"     # 事项编号 = action_items.id
F_STATUS = "ofRgdfn"      # 状态: 待处理 / 处理中 / 已完成 / 暂缓
F_DONE_AT = "soNZuEP"     # 完成日期


def fetch_records() -> list[dict]:
    out = subprocess.run(
        ["dws", "aitable", "record", "query", "--base-id", BASE_ID, "--table-id", TABLE_ID,
         "--all", "--page-limit", "0", "--format", "json"],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out[out.index("{"):])["data"]
    if not data.get("complete"):
        raise RuntimeError("AI 表格记录没有取全，本次不同步")
    return data["records"]


def main():
    conn = connect()
    changed = 0
    for r in fetch_records():
        c = r["cells"]
        if F_ITEM_ID not in c:
            continue
        status = "done" if (c.get(F_STATUS) or {}).get("name") == "已完成" else "open"
        done_at = (c.get(F_DONE_AT) or "")[:10] or None
        cur = conn.execute("SELECT status, done_at FROM action_items WHERE id = ?", [int(c[F_ITEM_ID])]).fetchone()
        if cur and (cur["status"], cur["done_at"]) != (status, done_at if status == "done" else None):
            conn.execute("UPDATE action_items SET status = ?, done_at = ? WHERE id = ?",
                         [status, done_at if status == "done" else None, int(c[F_ITEM_ID])])
            changed += 1
    conn.commit()
    done = conn.execute("SELECT count(*) FROM action_items WHERE status = 'done'").fetchone()[0]
    print(f"更新 {changed} 条；目前已完成 {done} 条")


if __name__ == "__main__":
    main()
