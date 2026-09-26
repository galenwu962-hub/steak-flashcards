"""Per-store improvement items distilled from customer feedback.

Every review, good or bad, already has its negative points broken out (review_aspects with
the customer's own words). This module groups a store's negative points for a period into a
short, prioritised list of things the store can act on, each with an owner role, a concrete
action and the customer quotes as evidence. Food-safety and pest issues are flagged for
escalation.

    python -m app.actions run --from 2026-09-12 --to 2026-09-25 [--stores 1,5] [--model M]
    python -m app.actions show [--store 5]
    python -m app.actions done <item_id>

Items are stored in action_items and shown on the dashboard under 改善事项.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect
from .knowledge import knowledge_text

DEFAULT_MODEL = "claude-sonnet-5"
MAX_POINTS = 150  # negative points fed per store; the most recent and lowest-star ones first


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(description="问题，一句话，具体到菜品或场景，不超过 30 字")
    aspect: str = Field(description="所属细项，从输入里的细项名称中选一个")
    owner: Literal["店长", "品控", "区域经理"]
    priority: Literal["高", "中", "低"] = Field(description="高：食品安全、卫生、虫害、多次重复出现或导致顾客明确不再来；中：反复出现的出品或服务问题；低：偶发或建议类")
    escalate: bool = Field(description="食品安全、虫害、卫生严重问题、投诉升级为 true")
    action: str = Field(description="门店本周就能执行的具体动作，1 到 2 句，营运视角，可以用内部术语")
    evidence: list[int] = Field(description="支持这一条的顾客意见编号（输入里的 # 编号），至少 1 个")


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ActionItem]


SYSTEM_PROMPT = f"""你是 pLeace 必乐时的营运分析师。你会收到一家门店在一段时间内顾客评价里所有的负面意见（包括好评里夹带的小抱怨），每条带有细项、菜品、顾客原话、星级和日期。

请把它们归纳成这家门店的改善事项清单，给店长、品控和区域经理看。

要求：
1. 合并同类：同一道菜、同一个问题多次出现要合成一条，evidence 里列出全部编号。评价越多的问题越靠前。
2. 具体：标题要点名菜品或场景（"椒麻腹心肉偏老、调味过重"，不要写"菜品口味问题"）。动作要写门店这周能做的事（谁去查什么、改什么），可以用内部术语。
3. 数量：3 到 8 条。只出现一次、也不涉及安全的意见可以合并进"其他"或不列。
4. 食品安全（异物、变质、身体不适）、虫害、卫生严重问题：一律单列，priority 高，escalate true，owner 品控，并需区域经理知悉。虫害写消杀要求；食材变质写批次核查和暂停销售；异物写操作规范排查。
5. 顾客提到预制感、冻品味、不新鲜、干硬发柴：owner 品控，动作包含核查备货量与效期管理。普通口味问题（偏咸、偏油、没惊喜）：owner 店长，核对做法和调味。
6. 服务、等位、环境、结账类：owner 店长。涉及定价、菜单结构、硬件改造：owner 区域经理。

公司知识库（用于判断哪些是真问题、责任归谁）：
{knowledge_text()}
"""


def negative_points(conn: sqlite3.Connection, store_id: int, d0: str, d1: str) -> list[dict]:
    rows = conn.execute("""
        SELECT x.aspect, x.category, x.quote, x.dish_raw AS dish, r.star, r.review_date, r.id AS review_id,
               a.sentiment AS review_sentiment, a.risk
        FROM review_aspects x JOIN reviews r ON r.id = x.review_id LEFT JOIN review_analysis a ON a.review_id = r.id
        WHERE x.sentiment = 'negative' AND r.store_id = ? AND r.review_date BETWEEN ? AND ?
        ORDER BY (a.risk = 'high') DESC, r.review_date DESC, r.star ASC""", (store_id, d0, d1)).fetchall()
    return [dict(r) for r in rows[:MAX_POINTS]]


def build_messages(store: str, d0: str, d1: str, points: list[dict]) -> list[dict]:
    lines = [f"门店：{store}", f"时间：{d0} 至 {d1}", f"负面意见 {len(points)} 条：", ""]
    for i, p in enumerate(points, 1):
        tag = f"{p['category']}·{p['aspect']}" + (f"·{p['dish']}" if p["dish"] else "")
        flag = " ⚠高风险" if p["risk"] == "high" else ""
        whole = "整条中差评" if p["review_sentiment"] in ("negative", "neutral") else "好评里的抱怨"
        lines.append(f"#{i} [{tag}] {p['star']}★ {p['review_date']} {whole}{flag}：{p['quote'] or '（无原话）'}")
    return [{"role": "user", "content": "\n".join(lines)}]


def generate(client, model: str, messages: list[dict]) -> tuple[ActionPlan, str]:
    msg = client.messages.create(
        model=model, max_tokens=6000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ActionPlan.model_json_schema()}},
    )
    if msg.stop_reason == "refusal":
        raise RuntimeError("refused")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return ActionPlan.model_validate_json(text), text


def run_store(conn, client, model: str, store_id: int, store: str, d0: str, d1: str) -> int:
    points = negative_points(conn, store_id, d0, d1)
    if not points:
        return 0
    plan, raw = generate(client, model, build_messages(store, d0, d1, points))
    with conn:
        conn.execute("DELETE FROM action_items WHERE store_id=? AND period_from=? AND period_to=?", (store_id, d0, d1))
        for it in plan.items:
            ev = [points[i - 1] for i in dict.fromkeys(it.evidence) if 1 <= i <= len(points)]
            conn.execute(
                """INSERT INTO action_items (store_id, period_from, period_to, model, title, aspect, owner, priority,
                   escalate, action, evidence_count, evidence_json, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
                (store_id, d0, d1, model, it.title, it.aspect, it.owner, it.priority, int(it.escalate), it.action,
                 len(ev), json.dumps([{k: e[k] for k in ("review_id", "quote", "dish", "star", "review_date")} for e in ev], ensure_ascii=False)))
    return len(plan.items)


def cmd_run(args):
    conn, client = connect(), make_client()
    d1 = args.to or conn.execute("SELECT max(review_date) FROM reviews").fetchone()[0]
    d0 = args.__dict__["from"] or (date.fromisoformat(d1) - timedelta(days=13)).isoformat()
    stores = conn.execute("SELECT id, name FROM stores ORDER BY id").fetchall()
    if args.stores:
        keep = {int(x) for x in args.stores.split(",")}
        stores = [s for s in stores if s["id"] in keep]
    for s in stores:
        try:
            n = run_store(conn, client, args.model, s["id"], s["name"], d0, d1)
            print(f"{s['name']}: {n} 条改善事项")
        except Exception as e:  # noqa: BLE001
            print(f"{s['name']}: 失败 {e}")


def cmd_show(args):
    conn = connect()
    sql = """SELECT i.*, s.name AS store FROM action_items i JOIN stores s ON s.id = i.store_id"""
    params: list = []
    if args.store:
        sql += " WHERE i.store_id = ?"; params.append(args.store)
    sql += " ORDER BY s.name, i.escalate DESC, CASE i.priority WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END, i.evidence_count DESC"
    last = None
    for r in conn.execute(sql, params):
        if r["store"] != last:
            print(f"\n=== {r['store']}  {r['period_from']} 至 {r['period_to']}")
            last = r["store"]
        mark = "⚠ " if r["escalate"] else ""
        print(f"[{r['id']}] {mark}{r['priority']} · {r['owner']} · {r['title']}（{r['evidence_count']} 条）{' ✓已完成' if r['status'] == 'done' else ''}")
        print(f"     动作：{r['action']}")
        for e in json.loads(r["evidence_json"])[:3]:
            print(f"     「{e['quote'] or '无原话'}」{e['star']}★ {e['review_date']}")


def cmd_done(args):
    conn = connect()
    with conn:
        conn.execute("UPDATE action_items SET status='done', done_at=datetime('now') WHERE id=?", (args.item_id,))
    print("ok")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run"); run.add_argument("--from"); run.add_argument("--to"); run.add_argument("--stores")
    run.add_argument("--model", default=DEFAULT_MODEL); run.set_defaults(fn=cmd_run)
    show = sub.add_parser("show"); show.add_argument("--store", type=int); show.set_defaults(fn=cmd_show)
    done = sub.add_parser("done"); done.add_argument("item_id", type=int); done.set_defaults(fn=cmd_done)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
