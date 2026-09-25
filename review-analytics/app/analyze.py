"""Per-review analysis with Claude.

Every review is reduced to one fixed schema:

* an overall sentiment on a closed 3-value scale (no "中性偏正面" drift);
* one entry per *aspect* the reviewer actually mentioned, each with its own
  sentiment — so the "肉酱偏咸" inside a 4-star review is recorded as a negative
  菜品/口味 aspect instead of disappearing into an overall "积极";
* dish mentions with their own sentiment;
* scenario, daypart, revisit intent, a risk flag, a one-line summary and a reply suggestion.

Commands (run from the review-analytics directory):

    python -m app.analyze estimate            # token + cost estimate for everything pending
    python -m app.analyze run --limit 20      # analyse a few reviews synchronously (good for spot checks)
    python -m app.analyze batch [--limit N]   # submit pending reviews as a Message Batch (50% cheaper)
    python -m app.analyze collect             # fetch finished batches and store the results
    python -m app.analyze status

Set REVIEW_ANALYTICS_API_KEY (or ANTHROPIC_API_KEY) in the environment. Model defaults to
claude-opus-5; override with --model (e.g. claude-sonnet-5 for a cheaper pass).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Literal

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import BaseModel, ConfigDict, Field

from .db import connect
from .normalize import resolve_dish

DEFAULT_MODEL = "claude-opus-5"
BATCH_CHUNK = 10_000  # requests per batch


def make_client() -> anthropic.Anthropic:
    """Build the API client.

    Claude Code cloud environments reserve the name ANTHROPIC_API_KEY (and point
    ANTHROPIC_BASE_URL at their own proxy), so we accept a second variable name and
    always talk to api.anthropic.com directly.
    """
    key = os.environ.get("REVIEW_ANALYTICS_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("缺少 API 密钥：请设置环境变量 REVIEW_ANALYTICS_API_KEY")
    return anthropic.Anthropic(api_key=key, base_url=os.environ.get("REVIEW_ANALYTICS_API_URL", "https://api.anthropic.com"))

# ---------------------------------------------------------------------------
# Taxonomy — closed sets so every chart aggregates on the same labels
# ---------------------------------------------------------------------------
Category = Literal["菜品", "服务", "环境", "价格", "等待", "卫生", "整体"]
Aspect = Literal[
    # 菜品
    "口味", "分量", "食材新鲜度", "菜品温度", "摆盘卖相", "出品稳定性", "菜单选择", "饮品", "甜品",
    # 服务
    "服务态度", "服务效率", "专业度与推荐", "结账与优惠",
    # 环境
    "装修氛围", "噪音", "座位空间", "空调温度", "位置交通", "停车",
    # 价格
    "性价比", "套餐价值", "隐性消费",
    # 等待
    "排队等位", "上菜速度",
    # 卫生
    "餐具卫生", "环境卫生", "食品安全",
    # 整体
    "整体体验", "适合场景", "复购推荐",
]
Sentiment = Literal["positive", "negative", "neutral"]

CATEGORY_OF_ASPECT: dict[str, str] = {
    **dict.fromkeys(["口味", "分量", "食材新鲜度", "菜品温度", "摆盘卖相", "出品稳定性", "菜单选择", "饮品", "甜品"], "菜品"),
    **dict.fromkeys(["服务态度", "服务效率", "专业度与推荐", "结账与优惠"], "服务"),
    **dict.fromkeys(["装修氛围", "噪音", "座位空间", "空调温度", "位置交通", "停车"], "环境"),
    **dict.fromkeys(["性价比", "套餐价值", "隐性消费"], "价格"),
    **dict.fromkeys(["排队等位", "上菜速度"], "等待"),
    **dict.fromkeys(["餐具卫生", "环境卫生", "食品安全"], "卫生"),
    **dict.fromkeys(["整体体验", "适合场景", "复购推荐"], "整体"),
}


class AspectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aspect: Aspect
    sentiment: Sentiment
    quote: str = Field(description="评价原文中支持这一判断的短语，不超过 30 字")
    dish: str = Field(default="", description="该评价点针对的具体菜品名，照原文写；不针对具体菜品时留空")


class DishMention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="菜品名，照原文写，去掉书名号和引号")
    sentiment: Sentiment


class ReviewAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentiment: Sentiment = Field(description="整条评价的总体倾向")
    score: int = Field(description="-2 很不满意, -1 不满意, 0 一般, 1 满意, 2 非常满意")
    revisit: Literal["yes", "maybe", "no", "unknown"] = Field(description="复购或推荐意愿")
    scenario: Literal["聚会", "约会", "亲子", "独自", "商务", "未提及"]
    daypart: Literal["午市", "下午茶", "晚市", "未提及"]
    risk: Literal["none", "watch", "high"] = Field(
        description="high: 食品安全、异物、身体不适、投诉升级或明确要求退款；watch: 强烈不满或威胁差评；其余 none"
    )
    summary: str = Field(description="一句话摘要，不超过 40 字")
    reply_suggestion: str = Field(description="商家回复建议，不超过 120 字；总体积极且无负面点时留空")
    aspects: list[AspectItem]
    dishes: list[DishMention]


SYSTEM_PROMPT = f"""你是一家连锁西餐厅（pLeace 必乐时）的顾客评价分析师。你会收到一条来自大众点评或美团的顾客评价，请按固定结构输出分析结果。

分析要求：
1. 总体倾向只用 positive / neutral / negative 三个值。星级只是参考：4 星但通篇抱怨记 negative，3 星但满意记 positive。
2. 评价里每一个被提到的点都要单独记录为一个 aspect，并各自判断倾向。好评里的小抱怨（"肉酱有点咸""芝士太多有点腻"）必须记成 negative 的 aspect，不能被整体好评掩盖。
3. aspect 只能从下面的固定列表里选，选最贴近的一项；确实无法归类的记为"整体体验"。
   菜品：口味、分量、食材新鲜度、菜品温度、摆盘卖相、出品稳定性、菜单选择、饮品、甜品
   服务：服务态度、服务效率、专业度与推荐、结账与优惠
   环境：装修氛围、噪音、座位空间、空调温度、位置交通、停车
   价格：性价比、套餐价值、隐性消费
   等待：排队等位、上菜速度
   卫生：餐具卫生、环境卫生、食品安全
   整体：整体体验、适合场景、复购推荐
4. 提到具体菜品时，aspect 的 dish 字段写该菜名，同时在 dishes 里列出每道菜及其倾向。菜名照原文抄写，去掉「」“”等符号，不要自行改写或翻译。
5. 没有提到的内容不要猜：场景、时段、复购意愿没有依据时用"未提及"或 unknown。
6. 泛泛的"很好吃""不错"只记一个 aspect（口味或整体体验），不要拆成多条。
7. 摘要和回复建议用中文。回复建议要具体回应评价里的问题，语气真诚，不要模板化客套。
"""


def build_params(review: sqlite3.Row, model: str) -> MessageCreateParamsNonStreaming:
    parts = [
        f"门店：{review['store_raw'] or '未知'}",
        f"平台：{review['platform'] or '未知'}",
        f"星级：{review['star'] if review['star'] is not None else '无'}",
    ]
    if review["package_name"]:
        parts.append(f"购买套餐：{review['package_name']}")
    if review["entry_type"]:
        parts.append(f"评价入口：{review['entry_type']}")
    parts.append(f"评价内容：\n{review['content']}")
    return MessageCreateParamsNonStreaming(
        model=model,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "\n".join(parts)}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": ReviewAnalysis.model_json_schema()},
        },
    )


def pending_reviews(conn: sqlite3.Connection, limit: int | None = None, since: str | None = None,
                    skip_short_positive: bool = True) -> list[sqlite3.Row]:
    """Reviews without an analysis yet.

    ``since`` limits to reviews on/after that date. ``skip_short_positive`` drops 5-star reviews
    under 40 characters ("好吃""不错"), which carry almost no information and are ~a fifth of the data.
    """
    sql = """
        SELECT r.* FROM reviews r
        LEFT JOIN review_analysis a ON a.review_id = r.id
        WHERE a.review_id IS NULL AND r.content IS NOT NULL AND length(trim(r.content)) >= 2
    """
    params: list = []
    if since:
        sql += " AND r.review_date >= ?"
        params.append(since)
    if skip_short_positive:
        sql += " AND (r.star IS NULL OR r.star <= 4 OR length(r.content) >= 40)"
    sql += " ORDER BY r.review_time DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def store_result(conn: sqlite3.Connection, review_id: str, model: str, text: str) -> None:
    data = ReviewAnalysis.model_validate_json(text)
    with conn:
        conn.execute("DELETE FROM review_aspects WHERE review_id=?", (review_id,))
        conn.execute(
            """INSERT OR REPLACE INTO review_analysis
               (review_id, model, sentiment, score, revisit, scenario, daypart, summary, reply_suggestion, risk, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id, model, data.sentiment, data.score, data.revisit, data.scenario, data.daypart,
             data.summary, data.reply_suggestion, data.risk, text),
        )
        for a in data.aspects:
            conn.execute(
                "INSERT INTO review_aspects (review_id, category, aspect, sentiment, quote, dish_raw, dish) VALUES (?,?,?,?,?,?,?)",
                (review_id, CATEGORY_OF_ASPECT.get(a.aspect, "整体"), a.aspect, a.sentiment, a.quote,
                 a.dish or None, resolve_dish(conn, a.dish) if a.dish else None),
            )
        for d in data.dishes:
            if not any(x.dish == d.name for x in data.aspects):
                conn.execute(
                    "INSERT INTO review_aspects (review_id, category, aspect, sentiment, quote, dish_raw, dish) VALUES (?,?,?,?,?,?,?)",
                    (review_id, "菜品", "口味", d.sentiment, None, d.name, resolve_dish(conn, d.name)),
                )


def _text_of(message) -> str:
    return next((b.text for b in message.content if b.type == "text"), "")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
PRICES = {  # USD per 1M tokens: (input, output)
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def cmd_estimate(conn, client, model, limit, since, skip_short):
    rows = pending_reviews(conn, limit, since, skip_short)
    if not rows:
        print("没有待分析的评价")
        return
    sample = rows[:: max(1, len(rows) // 25)][:25]
    tok = 0
    for r in sample:
        p = build_params(r, model)
        tok += client.messages.count_tokens(model=model, system=p["system"], messages=p["messages"]).input_tokens
    per_review_in = tok / len(sample)
    sys_tokens = client.messages.count_tokens(model=model, system=p["system"], messages=[{"role": "user", "content": "x"}]).input_tokens
    uncached_in = max(per_review_in - sys_tokens, 50)
    out_est = 350  # measured on Chinese reviews: JSON result ≈ 250-450 tokens at low effort
    pin, pout = PRICES.get(model, PRICES[DEFAULT_MODEL])
    n = len(rows)
    cost_sync = n * (uncached_in * pin + sys_tokens * pin * 0.1 + out_est * pout) / 1e6
    print(f"待分析 {n} 条；系统提示 {sys_tokens} tokens（缓存），每条评价约 {uncached_in:.0f} 输入 + {out_est} 输出 tokens")
    print(f"模型 {model}：同步调用约 ${cost_sync:.2f}，Batch 模式约 ${cost_sync / 2:.2f}（≈ ¥{cost_sync / 2 * 7.2:.0f}）")


def cmd_run(conn, client, model, limit, since, skip_short):
    rows = pending_reviews(conn, limit or 20, since, skip_short)
    ok = 0
    for r in rows:
        p = build_params(r, model)
        if model.startswith("claude-opus-5"):
            # server-side fallback is only accepted for Opus 5 models
            msg = client.beta.messages.create(
                betas=["server-side-fallback-2026-06-01"],
                fallbacks=[{"model": "claude-opus-4-8"}],
                **p,
            )
        else:
            msg = client.messages.create(**p)
        if msg.stop_reason == "refusal":
            print(f"[{r['id']}] refused: {msg.stop_details.explanation if msg.stop_details else ''}")
            continue
        text = _text_of(msg)
        try:
            store_result(conn, r["id"], msg.model, text)
            ok += 1
            print(f"[{r['id'][:8]}] {r['star']}★ -> {json.loads(text)['sentiment']}  {json.loads(text)['summary']}")
        except Exception as e:  # noqa: BLE001 - keep going, report the row
            print(f"[{r['id']}] parse error: {e}")
    print(f"done {ok}/{len(rows)}")


def cmd_batch(conn, client, model, limit, since, skip_short):
    rows = pending_reviews(conn, limit, since, skip_short)
    if not rows:
        print("没有待分析的评价")
        return
    for i in range(0, len(rows), BATCH_CHUNK):
        chunk = rows[i:i + BATCH_CHUNK]
        batch = client.messages.batches.create(
            requests=[Request(custom_id=r["id"], params=build_params(r, model)) for r in chunk]
        )
        with conn:
            conn.execute(
                "INSERT INTO analysis_jobs (batch_id, model, status, n_requests) VALUES (?,?,?,?)",
                (batch.id, model, batch.processing_status, len(chunk)),
            )
        print(f"submitted {batch.id}: {len(chunk)} requests ({batch.processing_status})")


def cmd_collect(conn, client, model, limit, since, skip_short):
    jobs = conn.execute("SELECT * FROM analysis_jobs WHERE status != 'collected'").fetchall()
    if not jobs:
        print("没有待收取的批次")
    for job in jobs:
        batch = client.messages.batches.retrieve(job["batch_id"])
        print(f"{job['batch_id']}: {batch.processing_status} "
              f"(ok {batch.request_counts.succeeded}, err {batch.request_counts.errored}, "
              f"processing {batch.request_counts.processing})")
        if batch.processing_status != "ended":
            continue
        ok = err = 0
        for res in client.messages.batches.results(job["batch_id"]):
            if res.result.type == "succeeded":
                m = res.result.message
                if m.stop_reason == "refusal":
                    err += 1
                    continue
                try:
                    store_result(conn, res.custom_id, m.model, _text_of(m))
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    err += 1
                    print(f"  [{res.custom_id}] {e}")
            else:
                err += 1
        with conn:
            conn.execute("UPDATE analysis_jobs SET status='collected' WHERE batch_id=?", (job["batch_id"],))
        print(f"  stored {ok}, failed {err}")


def cmd_status(conn, client, model, limit, since, skip_short):
    total = conn.execute("SELECT count(*) FROM reviews WHERE content IS NOT NULL").fetchone()[0]
    done = conn.execute("SELECT count(*) FROM review_analysis").fetchone()[0]
    print(f"评价 {total} 条，已分析 {done} 条，待分析 {total - done} 条")
    for j in conn.execute("SELECT * FROM analysis_jobs ORDER BY created_at"):
        print(f"  batch {j['batch_id']} {j['model']} {j['n_requests']} req  {j['status']}  {j['created_at']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["estimate", "run", "batch", "collect", "status"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--since", help="only reviews on/after this date, e.g. 2026-06-28")
    ap.add_argument("--all", action="store_true", help="include short 5-star reviews (skipped by default)")
    args = ap.parse_args(argv)
    conn = connect()
    client = make_client()
    {"estimate": cmd_estimate, "run": cmd_run, "batch": cmd_batch, "collect": cmd_collect, "status": cmd_status}[
        args.command
    ](conn, client, args.model, args.limit, args.since, not args.all)


if __name__ == "__main__":
    sys.exit(main())
