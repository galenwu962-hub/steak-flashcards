"""Render the 改善事项清单 as Markdown, for overwriting the DingTalk doc in place.

    python -m app.report_md data/reports/items.json data/reports/改善事项清单.md

Same sections as scripts/report_docx.js (the Word version). The DingTalk doc is updated with
`dws doc +update --command overwrite`, never re-imported as a new doc (HANDOFF.md §4).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date

CAT_NOTE = {
    '食品安全与卫生': '异物、变质、虫害、身体不适、清洁剂污染、员工个人卫生',
    '出品品质': '新鲜度与预制感、火候过老过柴、上桌温度、分量缩水、出品不稳定',
    '口味与配方': '偏咸偏甜偏油、调味争议、口味平淡没惊喜',
    '等位与出餐速度': '排队、叫号、上菜慢、出餐顺序',
    '服务执行': '漏单、承诺未兑现、撤盘不及时、态度、叫不到人',
    '环境与硬件': '拥挤、噪音、空调、座位、异味、设备故障',
    '价格与套餐价值': '人均偏高、性价比、套餐含金量',
    '菜单与产品结构': '菜品下架、选择少、缺清爽菜、儿童餐',
    '结账与优惠': '团购核销、多扣款、优惠规则',
}
PRIO = '高中低'


def short(name: str) -> str:
    return re.sub(r"^(wow |P3 by )?pLeace2? ?(island )?(LOFT )?", "", name)


def cell(x) -> str:
    return str(x if x is not None else '').replace('|', '／').replace('\n', ' ')


def table(header: list[str], rows: list[list]) -> str:
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(cell(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def quote1(it: dict) -> str:
    e = next((e for e in it.get('evidence') or [] if e.get('quote')), None)
    return f"「{e['quote']}」{e.get('star') or ''}★" if e else ''


def md_date(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.year}年{x.month}月{x.day}日"


def render(D: dict) -> str:
    all_items, themes = D['items'], D['themes']
    items = [i for i in all_items if not i.get('aware')]
    aware = [i for i in all_items if i.get('aware')]
    by_id = {i['id']: i for i in all_items}
    reviews = len({e['review_id'] for i in all_items for e in i.get('evidence') or []})
    n_high = sum(i['priority'] == '高' for i in items)
    n_esc = sum(bool(i['escalate']) for i in items)

    L = [f"# pLeace 顾客评价改善事项清单", "",
         f"{md_date(D['from'])} 至 {md_date(D['to'])} · 13 家门店 · 由顾客评价里的负面意见（含好评中的抱怨）自动归纳，供营运与店总审阅", "",
         "## 一、概览", "",
         f"共归纳出 {len(all_items)} 条事项，来自 {reviews} 条顾客评价。其中 {len(items)} 条需要门店整改"
         f"（高优先级 {n_high} 条，{n_esc} 条属于食品安全或卫生类，需区域经理知悉并立即处理）；"
         f"另有 {len(aware)} 条评价里没有具体的菜、时段或环节，只需留意，见第五部分。", "",
         "### 按分类", "",
         table(['分类', '包含什么', '事项', '评价', '门店'],
               [[c['category'], CAT_NOTE.get(c['category'], ''), c['items'], c['reviews'], c['stores']] for c in D['categories']]), "",
         "### 按门店", "",
         table(['门店', '整改事项', '高优先级', '需升级', '仅知晓'],
               [[s['name'], s['n'], s['high'], s['esc'] or '', s.get('aware') or ''] for s in D['stores']]), "",
         "## 二、跨门店普遍问题", "",
         "单店看只是“我们店的问题”，放在一起才知道是公司层面的问题。以下每个主题至少覆盖 3 家门店，按覆盖门店数排序。", ""]
    for n, t in enumerate(themes, 1):
        L += [f"### {n}. {t['title']}", "",
              f"{t['category']} · {t['store_count']} 家门店 · {t.get('review_total', t.get('evidence_total'))} 条评价", "",
              f"**涉及门店：**{'、'.join(short(s) for s in t['stores'])}", "",
              f"**共同点：**{t['what_is_common']}", "",
              f"**公司层面建议：**{t['company_action']}", ""]
        ex = [by_id[i] for i in t['item_ids'] if i in by_id][:4]
        if ex:
            L += [table(['门店', '门店事项', '顾客原话'], [[short(i['store']), i['title'], quote1(i)] for i in ex]), ""]

    L += ["## 三、需立即处理：食品安全与卫生", "", "这些事项不看数量，一条就要处理。负责人品控，区域经理知悉。", "",
          table(['门店', '问题', '建议动作', '顾客原话'],
                [[short(i['store']), i['title'], i['action'], quote1(i)] for i in items if i['escalate']]), "",
          "## 四、需整改事项（按分类）", "",
          "优先级：高 = 安全卫生或导致顾客明确不再来；中 = 反复出现的出品或服务问题；低 = 偶发或建议类。负责人为角色：店长 / 品控 / 区域经理。", ""]
    for cat in D['order']:
        lst = sorted([i for i in items if i['category'] == cat],
                     key=lambda i: (-i['escalate'], PRIO.find(i['priority']), -(i.get('review_count') or 0)))
        if lst:
            L += [f"### {cat}（{len(lst)} 条）", "",
                  table(['门店', '优先', '负责', '问题', '建议动作', '评价数', '顾客原话'],
                        [[short(i['store']), ('⚠ ' if i['escalate'] else '') + i['priority'], i['owner'], i['title'], i['action'],
                          i.get('review_count') or '', quote1(i)] for i in lst]), ""]
    if aware:
        L += ["## 五、仅知晓（留意即可）", "",
              "这些意见只有笼统感受，评价里找不到具体的菜、时段或环节，门店无从下手，所以不派任务、不设截止日期。"
              "店长知晓并留意；同类意见再出现且带出具体信息时，会变成整改事项。", "",
              table(['门店', '顾客在说什么', '为什么只是知晓', '顾客原话'],
                    [[short(i['store']), i['title'], i.get('kind_reason') or '', quote1(i)] for i in aware]), ""]
    L += ["## 六、口径说明", "",
          "- 数据来源：大众点评与美团上 13 家门店的顾客评价，经 AI 逐条拆解出每一个被提到的点及其正负面。",
          "- 改善事项：把一家门店一段时间内的所有负面意见（包括好评里夹带的抱怨）合并同类后归纳而成，每条附顾客原话作为证据。",
          "- 可落地审核：每条事项都回到评价原文核对，笼统的按原文里的具体菜品、时段、环节改写；原文也没有具体信息的列为仅知晓。",
          "- 评价数：事项背后有几条顾客评价，一条评价里说了几句也只算一次。",
          "- 跨门店主题：把 13 家门店的事项放在一起，找出至少 3 家门店共同出现的问题。",
          "- 分类为固定 9 类，一条事项只归一类，按其最主要的问题决定。", ""]
    return '\n'.join(L)


def main():
    src, out = sys.argv[1:3]
    open(out, 'w', encoding='utf-8').write(render(json.load(open(src, encoding='utf-8'))))
    print('written', out)


if __name__ == '__main__':
    main()
