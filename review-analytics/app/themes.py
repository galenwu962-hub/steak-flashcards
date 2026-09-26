"""Classify improvement items and find problems shared across stores.

Runs one Claude call over every action item of a period: assigns each item to a fixed
category, then groups items that describe the same problem in several stores into
cross-store themes with a company-level recommendation.

    python -m app.themes run --from 2026-09-12 --to 2026-09-25 [--model M]
    python -m app.themes show --from 2026-09-12 --to 2026-09-25
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

Category = Literal[
    "食品安全与卫生",      # 异物、变质、虫害、身体不适、清洁剂、员工卫生
    "出品品质",            # 新鲜度/预制感、火候过老过柴、温度、分量缩水、出品不稳定
    "口味与配方",          # 偏咸偏甜偏油、调味争议、没惊喜
    "等位与出餐速度",      # 排队、叫号、上菜慢、出餐顺序
    "服务执行",            # 漏单、承诺未兑现、撤盘、态度、叫不到人
    "环境与硬件",          # 拥挤、噪音、空调、座位、异味、设备
    "价格与套餐价值",      # 人均高、性价比、套餐含金量
    "菜单与产品结构",      # 下架、选择少、缺清爽菜、儿童餐
    "结账与优惠",          # 团购核销、多扣款、优惠规则
]
CATEGORY_ORDER = list(Category.__args__)  # type: ignore[attr-defined]


class ItemCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    category: Category


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(description="跨门店共同的问题，一句话，不超过 30 字")
    category: Category
    item_ids: list[int] = Field(description="属于这个主题的事项 id，必须来自至少 3 家不同门店")
    what_is_common: str = Field(description="这些门店的共同点是什么，1 到 2 句")
    company_action: str = Field(description="公司层面（而不是单店）应该做的事，1 到 2 句，营运视角")


class ThemeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[ItemCategory]
    themes: list[Theme]


SYSTEM_PROMPT = """你是 pLeace 必乐时的营运分析师。你会收到公司 13 家门店在同一时间段内各自的改善事项清单（已经由门店维度归纳过）。

任务一：给每一条事项分配一个分类，只能从固定分类里选：
食品安全与卫生 / 出品品质 / 口味与配方 / 等位与出餐速度 / 服务执行 / 环境与硬件 / 价格与套餐价值 / 菜单与产品结构 / 结账与优惠
- 新鲜度、预制感、过老过柴、上桌已凉、分量缩水归"出品品质"；单纯的偏咸偏甜偏油归"口味与配方"。
- 一条事项只能有一个分类，按它最主要的问题定。

任务二：找出跨门店的普遍问题。门店各自看只是"我们店的问题"，放在一起看才知道是公司层面的问题。
- 主题必须覆盖至少 3 家不同门店的事项，同一家店的多条事项只算一家。
- 同一道菜在多家店被投诉（比如某款提拉米苏、某款牛排、某款松饼）是最有价值的主题，优先找。
- 同一类运营问题在多家店重复（高峰等位、外卖不酥脆、撤盘不及时、团购核销）也算主题。
- 主题按覆盖门店数和意见总数排序，最多 10 个。不要为了凑数把不相关的事项硬拼在一起。
- company_action 写公司层面能做的事：改配方标准、统一培训、调整套餐、集中采购、品控巡检重点等，不要重复单店动作。
"""


def load_items(conn, d0: str, d1: str) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT i.id, i.store_id, s.name AS store, i.title, i.aspect, i.owner, i.priority, i.escalate, i.action,
               i.evidence_count, i.category, i.evidence_json
        FROM action_items i JOIN stores s ON s.id = i.store_id
        WHERE i.period_from = ? AND i.period_to = ? ORDER BY s.name, i.id""", (d0, d1))]


def build_messages(items: list[dict], d0: str, d1: str) -> list[dict]:
    lines = [f"时间：{d0} 至 {d1}，共 {len(items)} 条事项。格式：id | 门店 | 优先级 | 负责人 | 意见条数 | 问题 | 动作 | 顾客原话（最多 3 条）", ""]
    for it in items:
        quotes = "；".join(f"「{e['quote']}」" for e in json.loads(it["evidence_json"] or "[]")[:3] if e.get("quote"))
        lines.append(f"{it['id']} | {it['store']} | {it['priority']} | {it['owner']} | {it['evidence_count']} | {it['title']} | {it['action']} | {quotes}")
    return [{"role": "user", "content": "\n".join(lines)}]


def cmd_run(args):
    conn, client = connect(), make_client()
    d0, d1 = args.__dict__["from"], args.to
    items = load_items(conn, d0, d1)
    if not items:
        sys.exit("这个时间段没有改善事项，先运行 python -m app.actions run")
    # one long answer (thinking + ~100 classified items + themes): stream it so the request never times out
    with client.messages.stream(
        model=args.model, max_tokens=48000,
        system=SYSTEM_PROMPT, messages=build_messages(items, d0, d1),
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ThemeResult.model_json_schema()}},
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "max_tokens":
        sys.exit("输出被截断，请减少事项数量或提高 max_tokens")
    if msg.stop_reason == "refusal":
        sys.exit("refused")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    res = ThemeResult.model_validate_json(text)
    by_id = {it["id"]: it for it in items}
    with conn:
        for c in res.categories:
            conn.execute("UPDATE action_items SET category=? WHERE id=?", (c.category, c.id))
        conn.execute("DELETE FROM themes WHERE period_from=? AND period_to=?", (d0, d1))
        for t in res.themes:
            ids = [i for i in dict.fromkeys(t.item_ids) if i in by_id]
            stores = sorted({by_id[i]["store"] for i in ids})
            if len(stores) < 3:
                continue
            conn.execute("""INSERT INTO themes (period_from, period_to, model, title, category, store_count, evidence_total,
                            stores_json, item_ids_json, what_is_common, company_action) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (d0, d1, args.model, t.title, t.category, len(stores), sum(by_id[i]["evidence_count"] or 0 for i in ids),
                          json.dumps(stores, ensure_ascii=False), json.dumps(ids), t.what_is_common, t.company_action))
    print(f"分类 {len(res.categories)} 条，跨门店主题 {len(res.themes)} 个")


def cmd_show(args):
    conn = connect()
    d0, d1 = args.__dict__["from"], args.to
    for t in conn.execute("SELECT * FROM themes WHERE period_from=? AND period_to=? ORDER BY store_count DESC, evidence_total DESC", (d0, d1)):
        print(f"\n[{t['category']}] {t['title']}  · {t['store_count']} 家店 · {t['evidence_total']} 条意见")
        print("   门店：", "、".join(json.loads(t["stores_json"])))
        print("   共同点：", t["what_is_common"])
        print("   公司动作：", t["company_action"])
    print()
    for r in conn.execute("""SELECT category, count(*) n, sum(evidence_count) e, count(distinct store_id) s FROM action_items
                             WHERE period_from=? AND period_to=? GROUP BY category ORDER BY n DESC""", (d0, d1)):
        print(f"{r['category'] or '未分类':<10} 事项 {r['n']:>3}  意见 {r['e']:>4}  门店 {r['s']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, fn in (("run", cmd_run), ("show", cmd_show)):
        p = sub.add_parser(name); p.add_argument("--from", required=True); p.add_argument("--to", required=True)
        p.add_argument("--model", default=DEFAULT_MODEL); p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
