"""Reply suggestions and internal follow-ups written with the pLeace knowledge base.

The first-pass analysis (app.analyze) classifies every review; this module writes the
merchant reply for the ones that need one, in the store manager's voice, using the facts
and standard answers in app/knowledge.py. It also produces an internal note: what the
store or QC should check, and whether the case must be handled by a person.

    python -m app.reply run [--limit N] [--since DATE] [--all] [--model M]   # 中差评（默认）或全部
    python -m app.reply one "评价原文" --store 深业上城店 --star 1           # 单条试写，不入库
    python -m app.reply show [--limit N]                                    # 对照旧建议看结果

Set REVIEW_ANALYTICS_API_KEY. Default model claude-sonnet-5.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect
from .knowledge import knowledge_text

DEFAULT_MODEL = "claude-sonnet-5"


class ReplyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(description="发给顾客的回复全文，店长口吻")
    internal_note: str = Field(description="给门店的内部跟进建议，1 到 3 条，每条一句话，说清查什么、改什么；无需跟进时留空")
    owner: Literal["店长", "品控", "区域经理", "无需跟进"] = Field(description="内部跟进的第一责任角色")
    escalate: bool = Field(description="是否属于必须人工处理的情况")
    escalate_reason: str = Field(default="", description="需要人工处理的原因，不超过 20 字；不需要时留空")


SYSTEM_PROMPT = f"""你是 pLeace 必乐时某家门店的店长，正在回复大众点评或美团上的顾客评价，同时给门店写内部跟进建议。

下面是公司的知识库，回复和跟进建议都必须以它为准：

{knowledge_text()}

写回复的要求：
1. 先看顾客到底说了什么，逐点回应具体的事（菜名、等位时间、桌子、卫生细节），不要笼统地说"给您带来不好的体验"。
2. 顾客说得对的地方，直接认；说得不准确的地方（比如预制菜），不争辩也不解释，按知识库的口径虚心接受。
3. 回复里只说顾客能听懂的话：不解释原因、不描述内部流程、不用内部术语、不做自我诊断。严格遵守知识库"对外表达的红线"，原因和整改动作只写进 internal_note。
4. 只承诺知识库里允许的事。不送券、不送菜、不提退款金额。退款和严重投诉引导顾客打门店电话找店长。
5. 好评里夹着的小问题也要回应，一句话即可。纯好评回复简短真诚，可以提一句顾客夸到的菜。
6. 不要每条都以道歉开头；不要连用两个以上的"抱歉""对不起"；不要用"秋风送爽"这类应景套话。

写内部跟进建议的要求：
- 站在营运的角度，写门店明天就能去查、去改的事。例如"品控到店核查冷藏原料的备货量和效期标签"、"店长查 9 月 17 日晚市的撤盘频次和桌面清洁"。
- 顾客提到预制感、冻品味、不新鲜、发黑、回锅味、干硬发柴：负责人写品控，建议里必须包含核查备货量与效期管理。
- 顾客只是说味道一般、偏咸、偏油、没惊喜：这是调味和做法的问题，负责人写店长，建议核对这道菜的做法和调味，不要套用备货效期那一条。
- 属于知识库"必须人工处理"的情况：escalate 为 true，负责人写店长，并在建议里注明需区域经理知悉。
- 纯好评、没有可改进的点：owner 写"无需跟进"，internal_note 留空。
"""


def build_messages(store: str, platform: str | None, star, nickname: str | None, content: str,
                   summary: str | None = None, aspects: list[dict] | None = None) -> list[dict]:
    parts = [f"门店：{store}", f"平台：{platform or '未知'}", f"星级：{star if star is not None else '无'}",
             f"顾客昵称：{nickname or '匿名用户'}"]
    if summary:
        parts.append(f"前一轮 AI 摘要：{summary}")
    if aspects:
        parts.append("前一轮 AI 拆出的评价点：" + "；".join(
            f"{a['aspect']}({'负面' if a['sentiment'] == 'negative' else '正面' if a['sentiment'] == 'positive' else '中性'})"
            + (f"·{a['dish']}" if a.get("dish") else "") for a in aspects))
    parts.append(f"评价原文：\n{content}")
    return [{"role": "user", "content": "\n".join(parts)}]


def generate(client, model: str, messages: list[dict]) -> tuple[ReplyPlan, str]:
    msg = client.messages.create(
        model=model,
        max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ReplyPlan.model_json_schema()}},
    )
    if msg.stop_reason == "refusal":
        raise RuntimeError("refused: " + (msg.stop_details.explanation if msg.stop_details else ""))
    text = next((b.text for b in msg.content if b.type == "text"), "")
    plan = ReplyPlan.model_validate_json(text)
    plan.reply = chinese_punctuation(plan.reply)
    plan.internal_note = chinese_punctuation(plan.internal_note)
    return plan, text


_PUNCT = str.maketrans({",": "，", ";": "；", ":": "：", "?": "？", "!": "！", "(": "（", ")": "）"})


def chinese_punctuation(s: str) -> str:
    """The model occasionally mixes ASCII punctuation into Chinese text; normalise it."""
    return s.translate(_PUNCT) if s else s


NEG_WHERE = "(a.sentiment IN ('negative','neutral') OR (r.star IS NOT NULL AND r.star <= 3))"


def pending(conn: sqlite3.Connection, limit: int | None, since: str | None, only_negative: bool) -> list[sqlite3.Row]:
    sql = f"""
        SELECT r.id, r.platform, r.star, r.nickname, r.content, s.name AS store, a.summary
        FROM reviews r JOIN review_analysis a ON a.review_id = r.id
        LEFT JOIN stores s ON s.id = r.store_id
        LEFT JOIN review_replies p ON p.review_id = r.id
        WHERE p.review_id IS NULL AND r.content IS NOT NULL AND length(trim(r.content)) >= 2
    """
    params: list = []
    if since:
        sql += " AND r.review_date >= ?"; params.append(since)
    if only_negative:
        sql += f" AND {NEG_WHERE}"
    sql += " ORDER BY r.review_time DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def aspects_of(conn, review_id: str) -> list[dict]:
    return [dict(x) for x in conn.execute(
        "SELECT aspect, sentiment, dish_raw AS dish FROM review_aspects WHERE review_id = ?", (review_id,))]


def cmd_run(args):
    conn, client = connect(), make_client()
    rows = pending(conn, args.limit, args.since, not args.all)
    ok = 0
    for r in rows:
        try:
            plan, raw = generate(client, args.model, build_messages(
                r["store"] or "未知门店", r["platform"], r["star"], r["nickname"], r["content"], r["summary"], aspects_of(conn, r["id"])))
        except Exception as e:  # noqa: BLE001
            print(f"[{r['id'][:8]}] {e}")
            continue
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO review_replies (review_id, model, reply, internal_note, owner, escalate, raw_json) VALUES (?,?,?,?,?,?,?)",
                (r["id"], args.model, plan.reply, plan.internal_note, plan.owner, int(plan.escalate), raw))
        ok += 1
        print(f"[{r['id'][:8]}] {r['star']}★ {r['store']} -> {plan.owner}{' 升级' if plan.escalate else ''}")
    print(f"done {ok}/{len(rows)}")


def cmd_one(args):
    plan, _ = generate(make_client(), args.model, build_messages(args.store, args.platform, args.star, args.nickname, args.text))
    print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=1))


def cmd_show(args):
    conn = connect()
    rows = conn.execute("""
        SELECT r.review_time, r.star, r.nickname, r.content, s.name AS store, a.reply_suggestion AS old, p.*
        FROM review_replies p JOIN reviews r ON r.id = p.review_id
        LEFT JOIN review_analysis a ON a.review_id = r.id LEFT JOIN stores s ON s.id = r.store_id
        ORDER BY r.review_time DESC LIMIT ?""", (args.limit or 20,)).fetchall()
    for r in rows:
        print("=" * 72)
        print(f"{r['store']} · {r['review_time']} · {r['star']}★ · {r['nickname']}")
        print("原文：", r["content"])
        print("\n旧回复：", r["old"])
        print("\n新回复：", r["reply"])
        print(f"\n内部跟进（{r['owner']}{'，需人工处理' if r['escalate'] else ''}）：{r['internal_note'] or '无'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run"); run.add_argument("--limit", type=int); run.add_argument("--since")
    run.add_argument("--all", action="store_true", help="也给好评写回复（默认只写中差评）")
    run.add_argument("--model", default=DEFAULT_MODEL); run.set_defaults(fn=cmd_run)
    one = sub.add_parser("one"); one.add_argument("text"); one.add_argument("--store", default="未知门店")
    one.add_argument("--platform", default="大众点评"); one.add_argument("--star", type=float); one.add_argument("--nickname")
    one.add_argument("--model", default=DEFAULT_MODEL); one.set_defaults(fn=cmd_one)
    show = sub.add_parser("show"); show.add_argument("--limit", type=int); show.set_defaults(fn=cmd_show)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
