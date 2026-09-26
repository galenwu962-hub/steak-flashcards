"""Check each improvement item for whether a store manager can actually act on it.

A complaint like "部分餐点上菜慢" with no dish, time slot or step named gives the store
nothing to change. For every item this re-reads the full text of the reviews behind it:

* 可执行 - the item already names what to change;
* 改写后可执行 - the reviews do name the dish / time slot / step, so the item is rewritten
  around those specifics;
* 仅知晓 - nothing concrete anywhere; kept for awareness only (no deadline, no reminders).

    python -m app.actionability run [--from 2026-09-12 --to 2026-09-25] [--model M]
    python -m app.actionability show

Writes action_items.kind (整改 / 知晓), kind_reason, and the rewritten title/action (the
originals are kept in title_orig / action_orig).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect

DEFAULT_MODEL = "claude-sonnet-5"


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["可执行", "改写后可执行", "仅知晓"]
    title: str = Field(description="最终的问题描述，一句话，不超过 30 字；仅知晓时保留原意")
    action: str = Field(description="最终的建议动作，1 到 2 句；仅知晓时写店长需要留意什么，不要编造具体动作")
    reason: str = Field(description="判断理由，不超过 40 字；仅知晓时写清缺了什么信息（例如没说是哪道菜、哪个时段）")


SYSTEM_PROMPT = """你是 pLeace 必乐时的营运负责人，在审核发给店长的改善事项。标准只有一条：店长拿到这条事项，能不能马上知道改哪里、改完怎么验证。

你会收到一条事项（问题、建议动作、负责角色）和它背后每一条顾客评价的完整原文。

判断方法：
1. 可执行：事项已经点名具体对象——某道菜、某个时段（如周末午市）、某个岗位或环节（如叫号、结账、撤盘）、某个设备或区域——店长能据此安排动作并核对结果。
2. 改写后可执行：事项本身写得笼统，但评价原文里其实提到了具体的菜、时段、人数、环节或细节。把这些具体信息写进问题和动作，让店长能直接落地。只能用原文里真实出现的信息，不要编造。
3. 仅知晓：事项和评价原文都只有笼统感受（"上菜慢""贵""一般""味道不好""服务不热情"），找不到可以动手的具体对象。这类只让店长知晓、留意，不派任务。

注意：
- 食品安全、异物、虫害、卫生类即使描述不够具体也不能判仅知晓，至少是"可执行"（品控排查）。
- 负责角色是区域经理的（定价、菜单结构、硬件改造），按区域经理能否据此决策来判断。
- 不要因为动作写得好听就判可执行；动作指向的对象必须来自顾客原话。
- 改写时保持简洁，问题不超过 30 字，动作 1 到 2 句。
"""


def build_message(conn, it: dict) -> list[dict]:
    ids = list(dict.fromkeys(e["review_id"] for e in json.loads(it["evidence_json"])))
    reviews = conn.execute(
        f"SELECT id, star, review_date, content FROM reviews WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    lines = [f"门店：{it['store']}", f"问题：{it['title_orig'] or it['title']}", f"建议动作：{it['action_orig'] or it['action']}",
             f"负责角色：{it['owner']}", f"分类：{it['category']}", "", f"相关评价 {len(reviews)} 条（完整原文）："]
    for i, r in enumerate(reviews, 1):
        lines.append(f"#{i} {r['star']}★ {r['review_date']}：{(r['content'] or '').strip()}")
    return [{"role": "user", "content": "\n".join(lines)}]


def judge(client, model: str, messages: list[dict]) -> Verdict:
    msg = client.messages.create(
        model=model, max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": Verdict.model_json_schema()}},
    )
    if msg.stop_reason == "refusal":
        raise RuntimeError("refused")
    return Verdict.model_validate_json(next((b.text for b in msg.content if b.type == "text"), ""))


def cmd_run(args):
    conn, client = connect(), make_client()
    d1 = args.to or conn.execute("SELECT max(period_to) FROM action_items").fetchone()[0]
    d0 = args.__dict__["from"] or conn.execute("SELECT max(period_from) FROM action_items WHERE period_to = ?", [d1]).fetchone()[0]
    items = [dict(r) for r in conn.execute(
        """SELECT i.*, s.name AS store FROM action_items i JOIN stores s ON s.id = i.store_id
           WHERE i.period_from = ? AND i.period_to = ? ORDER BY i.id""", (d0, d1))]
    counts = {}
    for it in items:
        try:
            v = judge(client, args.model, build_message(conn, it))
        except Exception as e:  # noqa: BLE001
            print(f"[{it['id']}] 失败 {e}")
            continue
        with conn:
            conn.execute(
                """UPDATE action_items SET kind = ?, kind_reason = ?, title_orig = coalesce(title_orig, title),
                   action_orig = coalesce(action_orig, action), title = ?, action = ? WHERE id = ?""",
                ("知晓" if v.verdict == "仅知晓" else "整改", v.reason,
                 v.title if v.verdict == "改写后可执行" else (it["title_orig"] or it["title"]),
                 v.action if v.verdict != "可执行" else (it["action_orig"] or it["action"]), it["id"]))
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        print(f"[{it['id']}] {v.verdict} · {v.title} —— {v.reason}")
    print(counts)


def cmd_show(args):
    conn = connect()
    for r in conn.execute("""SELECT i.*, s.name AS store FROM action_items i JOIN stores s ON s.id = i.store_id
                             WHERE kind IS NOT NULL ORDER BY kind, s.name"""):
        changed = r["title_orig"] and r["title_orig"] != r["title"]
        print(f"[{r['id']}] {r['kind']} · {r['store']} · {r['title']}" + (f"（原：{r['title_orig']}）" if changed else ""))
        print(f"     {r['kind_reason']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run"); run.add_argument("--from"); run.add_argument("--to")
    run.add_argument("--model", default=DEFAULT_MODEL); run.set_defaults(fn=cmd_run)
    show = sub.add_parser("show"); show.set_defaults(fn=cmd_show)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
