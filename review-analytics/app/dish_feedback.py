"""Feedback on one (new) dish: every review that talks about it, sorted into 好评 / 中评 / 差评 for the
dish itself, with what customers praised or complained about. Built for R&D (研发) one-page reviews.

    python -m app.dish_feedback run    --dish 皇后牛排 --from 2026-09-28 --to 2026-11-05
    python -m app.dish_feedback export --dish 皇后牛排 --from 2026-09-28 --to 2026-11-05 --out data/reports/皇后牛排.json

Candidates are reviews whose text names the dish, plus reviews entered from a package that contains it
(entry_type) whose text talks about the steak at all; the model then decides whether the review really
says something about this dish. 好评/中评/差评 is about the dish, not the review's star rating (both are kept):
差评 follows the 腹心肉 KPI rules (clear dissatisfaction; vague 「一般」 is not 差评 but 中评).
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect

DEFAULT_MODEL = "claude-sonnet-5"

DISHES = {
    "皇后牛排": {"name": "招牌炙烤皇后牛排配杂蔬", "text_like": ["%皇后%"], "entry_like": "%皇后%",
               "entry_text_like": ["%牛排%", "%牛扒%", "%皇后%", "%steak%"]},
}

Aspect = Literal["熟度", "肉质口感", "调味酱汁", "配菜", "分量", "性价比", "摆盘卖相", "温度", "整体"]


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aspect: Aspect
    sentiment: Literal["正面", "负面"]
    quote: str = Field(description="顾客原话，照抄，不超过 40 字")


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relevant: bool = Field(description="这条评价有没有评价到这道菜本身")
    verdict: Literal["好评", "中评", "差评", "无关"]
    points: list[Point]
    summary: str = Field(description="顾客对这道菜的看法，一句话，不超过 30 字；无关时留空")


SYSTEM_PROMPT = """你在帮 pLeace 必乐时的研发部看一道新菜的顾客反馈。你会收到一条评价（含顾客购买的套餐名，如果有）和这道菜的名字。

第一步，判断这条评价有没有评价到这道菜本身（relevant）：
- 正文点名这道菜（全名、简称如「皇后牛排」「皇后牛扒」），或顾客买的是含这道菜的套餐、正文在说「牛排」，都算评价到了。
- 只夸套餐整体、环境、服务、别的菜，没说到这块牛排的，relevant=false，verdict=无关，points 留空。
- 如果正文说的牛排明显是别的菜（如腹心肉、肋排），也算无关。

第二步，判断顾客对这道菜本身的态度（verdict），不看整条评价的星级：
- 好评：明确夸这道菜（嫩、好吃、香、推荐、惊艳），没有实质批评，或批评只是很小的建议。
- 中评：笼统的「一般」「普通」「中规中矩」「不惊艳」「差点意思」，或夸贬参半、好评里提了具体改进建议。
- 差评：明确不满。太老、柴、硬、嚼不动、太生、熟度与点的不一致、偏咸、调料盖住肉味、肉散、筋膜多、分量少、不值这个价、不好吃、失望、没达到预期。

第三步，把顾客对这道菜说的每一点拆进 points：
- aspect 只能是：熟度、肉质口感（嫩/柴/筋/韧/汁水）、调味酱汁、配菜（杂蔬、薯条等配这道菜的东西）、分量、性价比、摆盘卖相、温度、整体（只说好吃/一般/不好吃，没具体到哪方面）。
- quote 照抄顾客原话，不改写。只拆关于这道菜的，别的菜不要写进来。
"""


def candidates(conn, dish_key: str, d0: str, d1: str) -> list:
    d = DISHES[dish_key]
    text_cond = " OR ".join("r.content LIKE ?" for _ in d["text_like"])
    entry_text = " OR ".join("r.content LIKE ?" for _ in d["entry_text_like"])
    return conn.execute(
        f"""SELECT r.id, r.review_date, r.star, r.rating, r.content, r.entry_type, r.package_name, r.platform, s.name AS store
            FROM reviews r JOIN stores s ON s.id = r.store_id
            WHERE r.review_date BETWEEN ? AND ? AND r.content IS NOT NULL
              AND (({text_cond}) OR (r.entry_type LIKE ? AND ({entry_text})))
            ORDER BY r.review_date""",
        (d0, d1, *d["text_like"], d["entry_like"], *d["entry_text_like"])).fetchall()


def _message(dish_key: str, r) -> list[dict]:
    pkg = r["package_name"] or (r["entry_type"].split("】")[-1] if r["entry_type"] and "】" in r["entry_type"] else r["entry_type"])
    text = (f"这道菜：{DISHES[dish_key]['name']}（顾客常写成「皇后牛排」等简称）\n"
            f"购买套餐：{pkg or '无'}\n星级：{r['star']}★\n评价：\n{(r['content'] or '').strip()}")
    return [{"role": "user", "content": text}]


def judge(client, model: str, dish_key: str, r) -> Judgment:
    msg = client.messages.create(
        model=model, max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=_message(dish_key, r),
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": Judgment.model_json_schema()}})
    return Judgment.model_validate_json(next(b.text for b in msg.content if b.type == "text"))


def _ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS dish_feedback (
        review_id TEXT NOT NULL, dish TEXT NOT NULL, relevant INTEGER, verdict TEXT, points_json TEXT,
        summary TEXT, model TEXT, judged_at TEXT DEFAULT (datetime('now')), PRIMARY KEY (review_id, dish))""")


def cmd_run(args):
    conn, client = connect(), make_client()
    _ensure_table(conn)
    done = {r[0] for r in conn.execute("SELECT review_id FROM dish_feedback WHERE dish = ?", (args.dish,))}
    todo = [r for r in candidates(conn, args.dish, args.d0, args.d1) if args.redo or r["id"] not in done]
    print(f"待判断 {len(todo)} 条")
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda r: (r, judge(client, args.model, args.dish, r)), todo))
    with conn:
        for r, j in results:
            conn.execute("INSERT OR REPLACE INTO dish_feedback (review_id, dish, relevant, verdict, points_json, summary, model) "
                         "VALUES (?,?,?,?,?,?,?)", (r["id"], args.dish, int(j.relevant), j.verdict,
                                                    json.dumps([p.model_dump() for p in j.points], ensure_ascii=False), j.summary, args.model))
    for r, j in results:
        print(f"{r['review_date']} {r['star']}★ {j.verdict} · {j.summary}")


def export(conn, dish_key: str, d0: str, d1: str) -> list[dict]:
    fb = {r["review_id"]: r for r in conn.execute("SELECT * FROM dish_feedback WHERE dish = ?", (dish_key,))}
    out = []
    for r in candidates(conn, dish_key, d0, d1):
        f = fb.get(r["id"])
        if not f or not f["relevant"]:
            continue
        out.append({"id": r["id"], "date": r["review_date"], "store": r["store"], "star": r["star"], "platform": r["platform"],
                    "content": r["content"], "verdict": f["verdict"], "summary": f["summary"],
                    "points": json.loads(f["points_json"] or "[]")})
    return out


def cmd_export(args):
    rows = export(connect(), args.dish, args.d0, args.d1)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"{len(rows)} 条 -> {args.out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, fn in (("run", cmd_run), ("export", cmd_export)):
        p = sub.add_parser(name)
        p.add_argument("--dish", default="皇后牛排", choices=list(DISHES))
        p.add_argument("--from", dest="d0", required=True); p.add_argument("--to", dest="d1", required=True)
        p.add_argument("--model", default=DEFAULT_MODEL)
        if name == "run":
            p.add_argument("--redo", action="store_true")
        else:
            p.add_argument("--out", required=True)
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
