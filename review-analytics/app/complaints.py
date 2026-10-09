"""Does a 4-star-or-better review point at a specific problem?

The owner's rule for 中差评 (2026-10-09):
* a review rated below 4 stars (3.5 included) is always 中差评, however positive the text;
* a review rated 4 stars or more is 中差评 only when it names a specific problem — a dish or drink,
  a service step or staff behaviour, the wait, the temperature, noise, seating, hygiene, the price
  of something. Vague lukewarm remarks ("一般""中规中矩""没惊喜""还行吧") do not count.

Only 4-star-plus reviews that the per-review analysis called negative or neutral are judged here
(the ones the old rule counted); the result goes to review_judgments and queries.NEG_EXPR reads it.

    python -m app.complaints batch --from 2026-06-28 --to 2026-10-04   # half price, collect with app.batchjobs
    python -m app.complaints run   --from 2026-10-05 --to 2026-10-11   # synchronous
    python -m app.complaints show  --from 2026-09-01 --to 2026-09-30   # list verdicts for checking
"""
from __future__ import annotations

import argparse
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect

DEFAULT_MODEL = "claude-sonnet-5"


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["具体问题", "笼统意见"]
    reason: str = Field(description="依据：引用顾客原话里的问题，或说明为什么笼统，不超过 30 字")


SYSTEM_PROMPT = """你在帮 pLeace 必乐时统计门店的中差评率。老板定的规则：4 星及以上的好评，只有在指出了具体问题时才算中差评；只有笼统、模棱两可的意见不算。

你会收到一条 4 星或 5 星评价的全文。判断它是否至少指出了一个具体问题。

算「具体问题」——说出了哪里不好：
- 点名某道菜或饮品的毛病：牛排太老、意面偏咸、薯条干、咖啡太淡、分量少。
- 服务的某个环节或员工行为：上菜慢、等了很久没人理、服务员态度差、漏单、催了几次。
- 排队等位太久、座位太挤、空调太冷或太热、太吵、桌子不干净、餐具没洗净、有虫。
- 某样东西的价格或性价比：套餐不值、某道菜太贵、团购分量缩水。

算「笼统意见」——没有说出具体哪里不好：
- 只有不温不火的整体感受：一般、中规中矩、还行吧、没有惊喜、期待更好、没有以前好吃了（没说哪道菜）。
- 客套的改进期望：希望越来越好、下次再试试别的。
- 只是提醒或个人口味说明，没有说店里做得不好：比如「我不太能吃辣」「建议早点来排队」。
- 夸完之后的一句模糊保留：「总体不错，个别菜一般」（没说哪道）。

只要有一处具体问题就判「具体问题」，不管评价其余部分多正面。
"""


def _message(r) -> list[dict]:
    return [{"role": "user", "content": f"评价：{r['rating']} 星 {r['review_date']}\n{(r['content'] or '').strip()}"}]


def request_params(model: str, messages: list[dict]) -> dict:
    return dict(
        model=model, max_tokens=400,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": Judgment.model_json_schema()}},
    )


def pending(conn, d0: str, d1: str, redo: bool = False) -> list:
    sql = """SELECT r.id, r.rating, r.review_date, r.content FROM reviews r JOIN review_analysis a ON a.review_id = r.id
             WHERE r.review_date BETWEEN ? AND ? AND r.rating >= 4 AND a.sentiment IN ('negative', 'neutral')"""
    if not redo:
        sql += " AND r.id NOT IN (SELECT review_id FROM review_judgments)"
    return conn.execute(sql + " ORDER BY r.review_date", (d0, d1)).fetchall()


def save(conn, review_id: str, j: Judgment, model: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO review_judgments (review_id, specific, reason, model) VALUES (?,?,?,?)",
                     (review_id, 1 if j.verdict == "具体问题" else 0, j.reason, model))


def cmd_run(args):
    conn, client = connect(), make_client()
    for r in pending(conn, args.__dict__["from"], args.to, args.redo):
        msg = client.messages.create(**request_params(args.model, _message(r)))
        j = Judgment.model_validate_json(next((b.text for b in msg.content if b.type == "text"), ""))
        save(conn, r["id"], j, args.model)
        print(f"{r['review_date']} {r['rating']}★ {j.verdict} · {j.reason}")


def submit_batch(conn, client, d0: str, d1: str, model: str, redo: bool = False) -> None:
    from .batchjobs import submit
    rows = pending(conn, d0, d1, redo)
    if not rows:
        print("没有需要判断的评价")
        return
    submit(conn, client, "complaints", {"from": d0, "to": d1, "model": model, "ids": [r["id"] for r in rows]},
           [(f"r-{i}", request_params(model, _message(r))) for i, r in enumerate(rows)])


def collect_batch(conn, client, meta: dict, results: dict) -> None:
    n = 0
    for cid, text in results.items():
        if text is None:
            print(f"  [{cid}] 没有结果，可用 python -m app.complaints run 补跑")
            continue
        save(conn, meta["ids"][int(cid.split("-")[1])], Judgment.model_validate_json(text), meta["model"])
        n += 1
    print(f"  好评里的具体问题判定：写入 {n} 条")


def cmd_batch(args):
    conn = connect()
    submit_batch(conn, make_client(), args.__dict__["from"], args.to, args.model, args.redo)


def cmd_show(args):
    conn = connect()
    for r in conn.execute(
            """SELECT r.review_date, r.rating, j.specific, j.reason, s.name FROM review_judgments j
               JOIN reviews r ON r.id = j.review_id JOIN stores s ON s.id = r.store_id
               WHERE r.review_date BETWEEN ? AND ? ORDER BY j.specific, r.review_date""", (args.__dict__["from"], args.to)):
        print(f"{r[0]} {r[1]}★ {'具体问题' if r[2] else '笼统意见'} · {r[4]} · {r[3]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, fn in (("run", cmd_run), ("batch", cmd_batch), ("show", cmd_show)):
        p = sub.add_parser(name)
        p.add_argument("--from", required=True); p.add_argument("--to", required=True)
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--redo", action="store_true")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
