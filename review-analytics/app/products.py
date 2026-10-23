"""Product-level view of the reviews: which dishes customers talk about most, and how often they
are clearly unhappy with each (the same 明确不满率 basis as the 腹心肉 KPI, app/dish_kpi.py).

The per-review analysis extracts dish names as customers write them ("提拉米苏", "瀑布提拉米苏",
"🌶️椒麻炙烤澳洲谷饲腹心肉"...). RULES merges them into products; generic words ("牛排", "意面",
"沙拉") match nothing and stay out of the ranking.

    python -m app.products batch  --from 2026-07-20 --to 2026-10-18 --top 20   # judge, half price
    python -m app.products export --from 2026-07-20 --to 2026-10-18 --top 20 --out data/reports/products.json

Judgments go to dish_judgments with dish = product name (腹心肉 keeps its own key '腹心肉').
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyze import make_client
from .db import connect

DEFAULT_MODEL = "claude-sonnet-5"
EAST = {"杭州", "南京"}                      # 华东; every other city is 华南
DISH_KEY = {"椒麻炙烤腹心肉": "腹心肉"}        # products that already have their own KPI judgments

# first match wins: (product, regex on the extracted dish name)
RULES = [
 ("提拉米苏法式吐司", r"提拉米苏.*(吐司|多士)"),
 ("香草瀑布提拉米苏", r"提拉米苏"),
 ("椒麻炙烤腹心肉", r"腹心"),
 ("牛油果照烧鸡边腿杂粮饭", r"照烧鸡"),
 ("火龙果酸奶", r"火龙果"),
 ("沙茶牛肉三明治", r"沙茶牛肉"),
 ("伊比利亚黑猪肋排", r"肋排|伊比利亚"),
 ("黑松露奶油培根蘑菇意面", r"黑松露.*(意面|意粉)|培根蘑菇"),
 ("阿芙佳朵", r"阿芙佳朵"),
 ("慢炖嫩肩牛肉意面", r"嫩肩|慢炖"),
 ("超浓抹茶莓莓", r"抹茶莓莓|抹茶草莓"),
 ("熔岩抹茶红豆松饼", r"红豆"),
 ("抹茶松饼", r"抹茶.*(松饼|热香饼)"),
 ("芒芒爆珠熔岩松饼", r"芒芒|爆珠"),
 ("wow明星松饼拼盘", r"松饼拼盘|明星松饼"),
 ("火山千层牛肉饭", r"火山"),
 ("韩式牛肉汉堡杂粮饭", r"韩式|汉堡杂粮"),
 ("罗望子烤鸡翅", r"罗望子|鸡翅|鸡中翅"),
 ("双葱番茄肉酱意面", r"双葱|肉酱意面|番茄肉酱"),
 ("红绯意面", r"红绯"),
 ("奶油蘑菇意面", r"奶油蘑菇意面"),
 ("酸汤牛肉菌菇烩饭", r"酸汤"),
 ("罗勒照烧杂菌小锅", r"杂菌小锅|罗勒照烧"),
 ("玉子沙拉", r"玉子"),
 ("泰泰虾多士", r"虾多士"),
 ("味噌酱烤杂蔬", r"味[噌增]|烤杂蔬|烤蔬菜|烤时蔬"),
 ("三文鱼毛豆紫甘蓝谷物碗", r"谷物碗"),
 ("滋滋墨鱼锅", r"墨鱼"),
 ("冬阴功大虾意面", r"冬阴功"),
 ("碱水海盐卷", r"碱水"),
 ("地中海三文鱼佐鹰嘴豆泥", r"鹰嘴豆"),
 ("法式脆皮烤鸡黑松露饭", r"脆皮烤鸡|黑松露.*饭"),
 ("椰香咖喱牛肉杂粮饭", r"咖喱牛肉"),
 ("沙朗/肉眼牛排", r"肉眼|沙朗|眼肉|安格斯"),
 ("薯条（含配菜）", r"薯条"),
 ("泰泰香茅脆炸鸡", r"香茅|脆炸鸡"),
 ("黑松露火腿巴斯克", r"巴斯克"),
 ("蒜香黄油黑虎虾", r"黑虎虾"),
 ("玉米片", r"玉米片|玉米脆片"),
]
_COMPILED = [(n, re.compile(p)) for n, p in RULES]


def canon(dish: str | None) -> str | None:
    if not dish:
        return None
    for name, rx in _COMPILED:
        if rx.search(dish):
            return name
    return None


def key(product: str) -> str:
    return DISH_KEY.get(product, product)


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["不满", "不算"]
    reason: str = Field(description="判断依据，引用顾客原话的关键词，不超过 30 字")


SYSTEM_PROMPT = """你在帮 pLeace 必乐时统计每道菜的「顾客明确不满率」，标准和招牌菜腹心肉的考核口径一致（老板和品控逐条审定）。

你会收到一条提到某道菜或饮品的顾客评价全文。只判断：这位顾客对这道菜本身是否明确不满。

算「不满」：
- 口味有毛病：太咸、太甜、太淡、太腻、味道怪、腥、不新鲜、没有该有的味道（如抹茶不浓、没椒麻味）。
- 口感或做法有毛病：老、柴、硬、生、不熟、软塌、太干、太稀、炸过头、冷了、熟度跟点的不一致。
- 分量少、切得薄、料少；觉得这道菜不值这个价。
- 明确的负面整体评价：不好吃、难吃、失望、踩雷、差点意思、反倒一般般。

算「不算」：
- 只嫌这道菜的配菜或配酱（例如牛排配的薯条），对主体没意见。
- 好评里的建议语气，没说现在不好：「如果再…就好了」「可以再…一点」。
- 夸贬参半、整体正面：「看起来一般，吃起来还可以」「挺好吃的，稍微有一点点甜」。
- 只有不温不火的词，没有具体毛病、也没有失望的意思：「中规中矩」「还行」「没什么感觉」。
- 只是个人口味或身体原因，没说店里做得不好：「我不太能吃辣」「我不爱吃甜的」。
- 只是提到或夸这道菜；或者不满其实是针对别的菜。
"""


def _message(product: str, r) -> list[dict]:
    return [{"role": "user", "content": f"这道菜：{product}\n评价：{r['star']}★ {r['review_date']}\n{(r['content'] or '').strip()}"}]


def request_params(model: str, messages: list[dict]) -> dict:
    return dict(
        model=model, max_tokens=500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": Judgment.model_json_schema()}},
    )


def mentions(conn, d0: str, d1: str) -> dict[str, dict[str, set]]:
    """product -> {"all": review ids, "doubt": ids with a negative/neutral point on it}"""
    out: dict[str, dict[str, set]] = collections.defaultdict(lambda: {"all": set(), "doubt": set()})
    for rid, dish, raw, sent in conn.execute(
            """SELECT x.review_id, x.dish, x.dish_raw, x.sentiment FROM review_aspects x JOIN reviews r ON r.id = x.review_id
               WHERE r.review_date BETWEEN ? AND ? AND coalesce(x.dish, x.dish_raw, '') <> ''""", (d0, d1)):
        p = canon(dish) or canon(raw)
        if p:
            out[p]["all"].add(rid)
            if sent in ("negative", "neutral"):
                out[p]["doubt"].add(rid)
    return out


def top_products(m: dict, n: int) -> list[str]:
    return sorted(m, key=lambda p: -len(m[p]["all"]))[:n]


def _save(conn, rid: str, dish: str, verdict: str, reason: str, model: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO dish_judgments (review_id, dish, verdict, reason, model) VALUES (?,?,?,?,?)",
                     (rid, dish, verdict, reason, model))


def pending(conn, d0: str, d1: str, n: int) -> list[tuple[str, str]]:
    """(product, review id) pairs still needing a model call; settles all-positive mentions by rule."""
    m = mentions(conn, d0, d1)
    done = {(d, r) for r, d in conn.execute("SELECT review_id, dish FROM dish_judgments")}
    todo = []
    for p in top_products(m, n):
        k = key(p)
        for rid in m[p]["all"]:
            if (k, rid) in done:
                continue
            if rid in m[p]["doubt"]:
                todo.append((p, rid))
            else:
                _save(conn, rid, k, "不算", "对这道菜只有正面评价", "rule")
    return todo


def submit_batch(conn, client, d0: str, d1: str, n: int, model: str) -> None:
    from .batchjobs import submit
    todo = pending(conn, d0, d1, n)
    if not todo:
        print("没有需要判断的评价")
        return
    reviews = {r["id"]: r for r in conn.execute(
        f"SELECT id, star, review_date, content FROM reviews WHERE id IN ({','.join('?' * len({r for _, r in todo}))})",
        list({r for _, r in todo}))}
    submit(conn, client, "products", {"model": model, "pairs": todo},
           [(f"p-{i}", request_params(model, _message(p, reviews[rid]))) for i, (p, rid) in enumerate(todo)])


def collect_batch(conn, client, meta: dict, results: dict) -> None:
    n = 0
    for cid, text in results.items():
        if text is None:
            continue
        p, rid = meta["pairs"][int(cid.split("-")[1])]
        j = Judgment.model_validate_json(text)
        _save(conn, rid, key(p), j.verdict, j.reason, meta["model"])
        n += 1
    print(f"  产品不满判定：写入 {n} 条")


def export(conn, d0: str, d1: str, n: int) -> dict:
    m = mentions(conn, d0, d1)
    store_of = {}
    for rid, name, city in conn.execute(
            """SELECT r.id, s.name, s.city FROM reviews r JOIN stores s ON s.id = r.store_id WHERE r.review_date BETWEEN ? AND ?""", (d0, d1)):
        store_of[rid] = (name, "华东" if city in EAST else "华南")
    verdict = {}
    for rid, d, v, why in conn.execute("SELECT review_id, dish, verdict, reason FROM dish_judgments"):
        verdict[(d, rid)] = (v, why)
    products = []
    for p in top_products(m, n):
        k = key(p)
        ids = m[p]["all"]
        bad = [r for r in ids if verdict.get((k, r), ("",))[0] == "不满"]
        by = collections.defaultdict(lambda: [0, 0])
        for r in ids:
            name, region = store_of.get(r, ("?", "?"))
            for g in (name, region):
                by[g][0] += 1
            if r in bad:
                by[name][1] += 1
                by[region][1] += 1
        products.append({"name": p, "mentions": len(ids), "bad": len(bad),
                         "unjudged": sum((k, r) not in verdict for r in ids),
                         "by": {g: {"n": a, "bad": b} for g, (a, b) in by.items()},
                         "reasons": [verdict[(k, r)][1] for r in bad]})
    return {"from": d0, "to": d1, "products": products}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["batch", "export"])
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    conn = connect()
    if args.command == "batch":
        submit_batch(conn, make_client(), args.d0, args.d1, args.top, args.model)
    else:
        data = export(conn, args.d0, args.d1, args.top)
        text = json.dumps(data, ensure_ascii=False, indent=1)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text)
        print(f"{len(data['products'])} 个产品 -> {args.out or 'stdout'}")


if __name__ == "__main__":
    sys.exit(main())
