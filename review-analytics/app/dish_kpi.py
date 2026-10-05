"""Clear-dissatisfaction rate for a tracked dish: the Q4 KPI for store managers and head chefs.

Rate = reviews where the customer is clearly unhappy with the dish itself ÷ reviews that mention it.
This is the definition the owner and 品控 (张异香) settled on in October 2026 after reading every
September review by hand. Complaints only about the fries, the sauce or the doneness options, mild
suggestions inside a good review, and mixed-but-positive remarks do not count; a doneness that does
not match the order, or "not worth the price", does.

Reviews whose analysed points about the dish are all positive are settled by rule (不算) without a
model call; the rest are judged by the model against the criteria below.

    python -m app.dish_kpi batch --from 2026-06-28 --to 2026-10-04   # half price, collect with app.batchjobs
    python -m app.dish_kpi run   --from 2026-09-28 --to 2026-10-04   # synchronous
    python -m app.dish_kpi show  --from 2026-09-01 --to 2026-09-30   # rate, plus every 不满 review for audit
"""
from __future__ import annotations

import argparse
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect

DEFAULT_MODEL = "claude-sonnet-5"

DISHES = {
    "腹心肉": {"name": "椒麻炙烤澳洲谷饲腹心肉", "like": "%腹心%"},
}


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["不满", "不算"]
    reason: str = Field(description="判断依据，引用顾客原话的关键词，不超过 30 字")


SYSTEM_PROMPT = """你在帮 pLeace 必乐时统计一道菜的「顾客明确不满率」。这个数字是门店店总和总厨 Q4 的考核指标，判定标准由老板和品控负责人逐条审过九月份评价后定下，请严格照下面的标准判断，不要自己放宽或收紧。

你会收到一条提到这道菜的顾客评价全文。只判断：这位顾客对这道菜本身是否明确不满。

算「不满」：
- 熟度或口感差：太老、柴、硬、咬不动、太生、中间不熟。
- 熟度跟点的不一致，即使同时说肉还嫩也算。例：「选了七分熟，实际感觉有九分熟了，肉质还比较嫩」→ 不满。
- 肉质差：太软烂没有韧劲、肉散、汁水少。
- 调味问题：偏咸、调料味太重盖住肉味、没有椒麻味。
- 切得太薄、分量少。
- 整体评价负面、带失望：差点意思、反倒一般般、不惊艳又挑出毛病、体验差、失望。
- 觉得不值这个价、性价比差。例：「性价比普通，搭配的薯条又油又干」→ 不满。

算「不算」：
- 只嫌配菜、酱汁或选项：薯条、黄芥末、蘸酱、熟度选项少、套餐小食搭配。同时还对肉本身或价格不满的，按上面算不满。
- 好评里的建议语气，没有说现在不好。例：「口感如果更嫩一些就好了」「稍微口感上可以再嫩一点更好」→ 不算。
- 夸贬参半、整体是正面的。例：「看起来干柴，吃起来还可以」「肉质很入味……加了烤肉粉反而掩盖了肉味」「也挺好吃的，味儿稍微有一点点淡」→ 不算。
- 熟度只是轻微偏差、同时夸肉。例：「肉很嫩，我们选了5成熟的，稍稍有些生」→ 不算。
- 只说辣、没说不好吃，或因自身原因受不了辣。例：「不错就是那天嗓子疼差点没辣晕」→ 不算。
- 只有不温不火的词，没有具体毛病、也没有失望的意思：「腹心肉中规中矩」「牛肉我倒没觉得什么感觉」「肉一般吧，但性价比不错了」→ 不算。
- 只是提到或夸这道菜。
- 评价里的不满其实针对别的菜（例如别的牛肉饭、意面），不是这道菜。
"""


def _message(dish_key: str, r) -> list[dict]:
    d = DISHES[dish_key]
    text = (f"这道菜：{d['name']}（顾客也会写成「{dish_key}」等简称）\n"
            f"评价：{r['star']}★ {r['review_date']}\n{(r['content'] or '').strip()}")
    return [{"role": "user", "content": text}]


def request_params(model: str, messages: list[dict]) -> dict:
    return dict(
        model=model, max_tokens=500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": Judgment.model_json_schema()}},
    )


def mentions(conn, dish_key: str, d0: str, d1: str) -> list:
    """Reviews in [d0, d1] that mention the dish in their text or in an analysed point."""
    like = DISHES[dish_key]["like"]
    return conn.execute(
        """SELECT r.id, r.star, r.review_date, r.content, r.store_id, r.store_raw FROM reviews r
           WHERE r.review_date BETWEEN ? AND ?
             AND (r.content LIKE ? OR EXISTS (SELECT 1 FROM review_aspects x WHERE x.review_id = r.id
                                               AND (coalesce(x.dish, '') LIKE ? OR coalesce(x.dish_raw, '') LIKE ?)))
           ORDER BY r.review_date""", (d0, d1, like, like, like)).fetchall()


def _all_points_positive(conn, dish_key: str, review_id: str) -> bool:
    like = DISHES[dish_key]["like"]
    rows = conn.execute(
        """SELECT x.sentiment FROM review_aspects x WHERE x.review_id = ?
           AND (coalesce(x.dish, '') LIKE ? OR coalesce(x.dish_raw, '') LIKE ?)""", (review_id, like, like)).fetchall()
    return bool(rows) and all(s[0] == "positive" for s in rows)


def save(conn, review_id: str, dish_key: str, verdict: str, reason: str, model: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO dish_judgments (review_id, dish, verdict, reason, model) VALUES (?,?,?,?,?)",
                     (review_id, dish_key, verdict, reason, model))


def pending(conn, dish_key: str, d0: str, d1: str, redo: bool = False) -> list:
    """Mentions still needing a model call; settles the all-positive ones by rule on the way."""
    done = {r[0] for r in conn.execute("SELECT review_id FROM dish_judgments WHERE dish = ?", (dish_key,))}
    out = []
    for r in mentions(conn, dish_key, d0, d1):
        if r["id"] in done and not redo:
            continue
        if _all_points_positive(conn, dish_key, r["id"]):
            save(conn, r["id"], dish_key, "不算", "对这道菜只有正面评价", "rule")
        else:
            out.append(r)
    return out


def rate(conn, dish_key: str, d0: str, d1: str, store_id=None) -> dict:
    """n mentions, n clearly unhappy, n not yet judged."""
    rows = [r for r in mentions(conn, dish_key, d0, d1) if store_id is None or r["store_id"] == store_id]
    verdicts = dict(conn.execute("SELECT review_id, verdict FROM dish_judgments WHERE dish = ?", (dish_key,)).fetchall())
    bad = sum(verdicts.get(r["id"]) == "不满" for r in rows)
    unjudged = sum(r["id"] not in verdicts for r in rows)
    return {"n": len(rows), "bad": bad, "unjudged": unjudged, "rate": bad / len(rows) if rows else None}


# ---------------------------------------------------------------- commands

def cmd_run(args):
    conn, client = connect(), make_client()
    for r in pending(conn, args.dish, args.__dict__["from"], args.to, args.redo):
        msg = client.messages.create(**request_params(args.model, _message(args.dish, r)))
        j = Judgment.model_validate_json(next((b.text for b in msg.content if b.type == "text"), ""))
        save(conn, r["id"], args.dish, j.verdict, j.reason, args.model)
        print(f"{r['review_date']} {j.verdict} · {j.reason}")
    print(rate(conn, args.dish, args.__dict__["from"], args.to))


def submit_batch(conn, client, dish_key: str, d0: str, d1: str, model: str, redo: bool = False) -> None:
    from .batchjobs import submit
    rows = pending(conn, dish_key, d0, d1, redo)
    if not rows:
        print("没有需要判断的评价")
        return
    # custom ids must be short, so requests are numbered and the meta keeps the number -> review id map
    submit(conn, client, "dish_kpi", {"dish": dish_key, "from": d0, "to": d1, "model": model, "ids": [r["id"] for r in rows]},
           [(f"r-{i}", request_params(model, _message(dish_key, r))) for i, r in enumerate(rows)])


def collect_batch(conn, client, meta: dict, results: dict) -> None:
    ids, n = meta["ids"], 0
    for cid, text in results.items():
        if text is None:
            print(f"  [{cid}] 没有结果，可用 python -m app.dish_kpi run 补跑")
            continue
        j = Judgment.model_validate_json(text)
        save(conn, ids[int(cid.split("-")[1])], meta["dish"], j.verdict, j.reason, meta["model"])
        n += 1
    print(f"  {meta['dish']} 不满判定：写入 {n} 条；{rate(conn, meta['dish'], meta['from'], meta['to'])}")


def cmd_batch(args):
    conn = connect()
    submit_batch(conn, make_client(), args.dish, args.__dict__["from"], args.to, args.model, args.redo)


def cmd_show(args):
    conn = connect()
    d0, d1 = args.__dict__["from"], args.to
    print(rate(conn, args.dish, d0, d1))
    for r in conn.execute(
            """SELECT r.review_date, r.star, r.store_raw, j.reason, r.content FROM dish_judgments j JOIN reviews r ON r.id = j.review_id
               WHERE j.dish = ? AND j.verdict = '不满' AND r.review_date BETWEEN ? AND ? ORDER BY r.review_date""",
            (args.dish, d0, d1)):
        print(f"{r[0]} {r[1]}★ {r[2]} · {r[3]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, fn in (("run", cmd_run), ("batch", cmd_batch), ("show", cmd_show)):
        p = sub.add_parser(name)
        p.add_argument("--from", required=True); p.add_argument("--to", required=True)
        p.add_argument("--dish", default="腹心肉", choices=list(DISHES))
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--redo", action="store_true", help="judge again even if already judged")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
