"""菜品口碑看板: render data/reports/products.json (app.products export + store/region stats) to one page.

    python -m app.product_board data/reports/products.json data/reports/菜品口碑看板.html
"""
from __future__ import annotations

import json
import sys
from datetime import date
from html import escape

TOP_SHOWN = 20
HEAT_COLS = 10      # products in the store × product grid
HEAT_MIN = 10       # fewer mentions than this in a cell: show the count only
REGION_MIN = 20     # fewer mentions in a region: no rate, sample too small


def pct(x, d=1):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def md(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.year}年{x.month}月{x.day}日"


def grade(r):
    return "优秀" if r < 0.08 else "正常" if r <= 0.12 + 1e-9 else "问题"


def render(D: dict, notes: list[str]) -> str:
    from .weekly import short
    P = D["products"][:TOP_SHOWN]
    for p in P:
        p["rate"] = p["bad"] / p["mentions"]
        p["by"] = {short(k): v for k, v in p["by"].items()}   # store names as on the rest of the page
    T, R, S = D["total"], D["regions"], D["stores"]
    weeks = (date.fromisoformat(D["to"]) - date.fromisoformat(D["from"])).days // 7 + 1

    # per store: most-mentioned product
    for s in S:
        best = max(P, key=lambda p: p["by"].get(s["name"], {}).get("n", 0))
        s["top"] = (best["name"], best["by"].get(s["name"], {}).get("n", 0))

    # B. mentions, split by region
    mx = max(p["mentions"] for p in P)
    bars = "".join(f"""
      <div class="mrow">
        <span class="pname">{escape(p['name'])}</span>
        <span class="mbar" title="华南 {p['by'].get('华南', {}).get('n', 0)} · 华东 {p['by'].get('华东', {}).get('n', 0)}">
          <i class="s" style="width:{p['by'].get('华南', {}).get('n', 0) / mx * 100:.2f}%"></i><i class="e" style="width:{p['by'].get('华东', {}).get('n', 0) / mx * 100:.2f}%"></i>
        </span>
        <span class="num">{p['mentions']:,}</span>
      </div>""" for p in P)

    # C. ranking by clear-dissatisfaction rate
    rank = sorted(P, key=lambda p: -p["rate"])
    rmax = max(p["rate"] for p in rank)

    def reg_cell(p, g):
        b = p["by"].get(g)
        if not b or not b["n"]:
            return '<td class="num muted">—</td>'
        if b["n"] < REGION_MIN:
            return f'<td class="num muted" title="只有 {b["n"]} 条提及">样本少</td>'
        return f'<td class="num">{pct(b["bad"] / b["n"])}<small>{b["bad"]}/{b["n"]}</small></td>'

    rrows = "".join(f"""
      <tr class="{'hot' if i < 5 else ''}">
        <td class="num rk">{i + 1}</td>
        <td class="pname">{escape(p['name'])}</td>
        <td class="num">{p['mentions']:,}</td>
        <td class="rate"><span class="rbar"><i style="width:{p['rate'] / rmax * 100:.1f}%"></i></span><b>{pct(p['rate'])}</b><small>{p['bad']} 条</small></td>
        {reg_cell(p, '华南')}{reg_cell(p, '华东')}
        <td class="tags">{''.join(f'<span class="tag">{escape(t)}<em>{n}</em></span>' for t, n in p['tags']) or '<span class="muted">—</span>'}</td>
      </tr>""" for i, p in enumerate(rank))

    # A. stores
    smax = max(0.2, max(s["rate"] for s in S) * 1.08)
    srows = "".join(f"""
      <tr>
        <td class="store">{escape(s['name'])}</td>
        <td><span class="chip {'e' if s['region'] == '华东' else 's'}">{s['region']}</span></td>
        <td class="num">{s['reviews']:,}</td>
        <td class="srate"><span class="track"><i class="b1" style="left:{8 / (smax * 100) * 100:.1f}%;width:{4 / (smax * 100) * 100:.1f}%"></i><i class="b2" style="left:{12 / (smax * 100) * 100:.1f}%;right:0"></i><b class="dot g-{grade(s['rate'])}" style="left:{s['rate'] / smax * 100:.1f}%"></b></span><span class="num">{pct(s['rate'])}</span></td>
        <td class="muted top">{escape(s['top'][0])} <small>{s['top'][1]} 条</small></td>
      </tr>""" for s in sorted(S, key=lambda s: -s["rate"]))

    # D. store × product grid
    cols = P[:HEAT_COLS]
    hmax = max((p["by"].get(s["name"], {}).get("bad", 0) / p["by"][s["name"]]["n"])
               for p in cols for s in S if p["by"].get(s["name"], {}).get("n", 0) >= HEAT_MIN)

    def cell(p, s):
        b = p["by"].get(s["name"])
        if not b or not b["n"]:
            return '<td class="hc none"></td>'
        if b["n"] < HEAT_MIN:
            return f'<td class="hc few" title="{escape(s["name"])} · {escape(p["name"])}：只有 {b["n"]} 条提及">{b["n"]}</td>'
        r = b["bad"] / b["n"]
        return (f'<td class="hc" style="--a:{min(1, r / hmax):.2f}" title="{escape(s["name"])} · {escape(p["name"])}：'
                f'{b["bad"]}/{b["n"]} 条明确不满">{r * 100:.0f}%</td>')

    grid = "".join(f"<tr><th class='store'>{escape(s['name'])}</th>{''.join(cell(p, s) for p in cols)}</tr>"
                   for s in sorted(S, key=lambda s: (s["region"], -s["reviews"])))
    heads = "".join(f"<th class='vh'><span>{escape(p['name'])}</span></th>" for p in cols)
    note_html = "".join(f"<li>{escape(n)}</li>" for n in notes)

    return f"""<title>菜品口碑看板</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@700&display=swap">
<style>
/* Layout: one reading column, summary first (regions → stores), then products by attention, then by dissatisfaction, then where. */
:root {{
  --bg: #f4f5f3; --paper: #ffffff; --ink: #1c2024; --muted: #676e76; --line: #e2e4e0; --track: #eceee9;
  --accent: #23495a; --south: #1a6fb0; --east: #c27c1c;
  --good: #2e7a4f; --mid: #5d6975; --bad: #b3261e; --bad-bg: #fbe7e4; --band1: #f3eddc; --band2: #f8e2de;
  --heat: 179, 38, 30;
  --f-display: "Noto Serif SC", "Songti SC", serif;
  --f-body: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
    --accent: #8cc0d4; --south: #3d9ccf; --east: #c4801f;
    --good: #6cc592; --mid: #aab4be; --bad: #ff8a7e; --bad-bg: #3d2320; --band1: #3a3524; --band2: #43282a;
    --heat: 255, 120, 105; color-scheme: dark;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
  --accent: #8cc0d4; --south: #3d9ccf; --east: #c4801f;
  --good: #6cc592; --mid: #aab4be; --bad: #ff8a7e; --bad-bg: #3d2320; --band1: #3a3524; --band2: #43282a;
  --heat: 255, 120, 105; color-scheme: dark;
}}
body {{ background: var(--bg); color: var(--ink); font: 14px/1.55 var(--f-body); }}
.page {{ max-width: 960px; margin: 0 auto; padding-block: 28px 44px; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); display: grid; gap: 22px; }}
header {{ display: flex; flex-wrap: wrap; gap: 16px 32px; align-items: end; justify-content: space-between; }}
h1 {{ font: 700 28px/1.2 var(--f-display); margin: 0; letter-spacing: .02em; text-wrap: balance; }}
.sub {{ color: var(--muted); margin: 4px 0 0; }}
.kpi {{ display: flex; gap: 28px; flex-wrap: wrap; }}
.kpi div {{ display: grid; }}
.kpi .v {{ font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.15; }}
.kpi .l {{ font-size: 12px; color: var(--muted); letter-spacing: .04em; }}
section {{ background: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 16px 18px; min-width: 0; }}
h2 {{ font-size: 16px; margin: 0 0 4px; }}
.cap {{ font-size: 12px; color: var(--muted); margin: 0 0 12px; }}
.notes {{ margin: 0; padding-left: 1.2em; display: grid; gap: 6px; }}
.regions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }}
@media (max-width: 640px) {{ .regions {{ grid-template-columns: 1fr; }} }}
.region {{ border-top: 3px solid var(--south); padding-top: 8px; display: grid; gap: 2px; }}
.region.e {{ border-color: var(--east); }}
.region b {{ font-size: 15px; }}
.region .v {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.region .l {{ font-size: 12px; color: var(--muted); }}
.scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; font-weight: 500; font-size: 12px; color: var(--muted); padding: 4px 8px 6px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; text-align: right; }}
td small {{ display: block; font-size: 11px; color: var(--muted); font-weight: 400; }}
.muted {{ color: var(--muted); }}
.store, .pname {{ font-weight: 500; white-space: nowrap; }}
.chip {{ font-size: 12px; padding: 1px 8px; border-radius: 10px; color: var(--paper); white-space: nowrap; }}
.chip.s {{ background: var(--south); }} .chip.e {{ background: var(--east); }}
.srate {{ display: flex; gap: 10px; align-items: center; min-width: 210px; }}
.track {{ position: relative; flex: 1; height: 10px; background: var(--track); border-radius: 5px; }}
.track i {{ position: absolute; top: 0; bottom: 0; }} .track .b1 {{ background: var(--band1); }} .track .b2 {{ background: var(--band2); border-radius: 0 5px 5px 0; }}
.dot {{ position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; border: 2px solid var(--paper); }}
.dot.g-优秀 {{ background: var(--good); }} .dot.g-正常 {{ background: var(--mid); }} .dot.g-问题 {{ background: var(--bad); }}
.top small {{ display: inline; }}
.legend {{ display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin: -4px 0 10px; flex-wrap: wrap; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }}
.mrows {{ display: grid; gap: 5px; }}
.mrow {{ display: grid; grid-template-columns: 11.5em 1fr 4em; gap: 10px; align-items: center; }}
.mrow .pname {{ overflow: hidden; text-overflow: ellipsis; font-weight: 400; }}
.mbar {{ display: flex; gap: 2px; height: 14px; }}
.mbar i {{ display: block; height: 100%; min-width: 0; }}
.mbar .s {{ background: var(--south); border-radius: 3px 0 0 3px; }} .mbar .e {{ background: var(--east); border-radius: 0 3px 3px 0; }}
@media (max-width: 520px) {{ .mrow {{ grid-template-columns: 8.5em 1fr 3.4em; }} }}
.rank td {{ vertical-align: middle; }}
.rk {{ color: var(--muted); width: 2em; }}
tr.hot .rk {{ color: var(--bad); font-weight: 700; }}
.rate {{ min-width: 190px; white-space: nowrap; }}
.rate b {{ font-variant-numeric: tabular-nums; margin-left: 8px; }}
.rate small {{ display: inline; margin-left: 4px; }}
.rbar {{ display: inline-block; width: 90px; height: 8px; background: var(--track); border-radius: 4px; vertical-align: middle; overflow: hidden; }}
.rbar i {{ display: block; height: 100%; background: var(--bad); border-radius: 4px; }}
tr:not(.hot) .rbar i {{ opacity: .55; }}
.tags {{ min-width: 180px; }}
.tag {{ display: inline-block; font-size: 12px; padding: 1px 7px; margin: 1px 4px 1px 0; border-radius: 10px; background: var(--bad-bg); color: var(--ink); white-space: nowrap; }}
.tag em {{ font-style: normal; color: var(--muted); margin-left: 3px; font-size: 11px; }}
.heat {{ border-collapse: separate; border-spacing: 2px; }}
.heat th, .heat td {{ border: 0; }}
.heat .vh {{ vertical-align: bottom; height: 9.5em; padding: 0 0 4px; }}
.heat .vh span {{ writing-mode: vertical-rl; display: inline-block; font-size: 12px; max-height: 9em; overflow: hidden; }}
.heat .store {{ font-size: 13px; padding-right: 10px; }}
.hc {{ min-width: 46px; height: 30px; text-align: center; font-size: 12px; font-variant-numeric: tabular-nums;
  background: rgba(var(--heat), calc(.06 + var(--a, 0) * .62)); border-radius: 3px; }}
.hc.few {{ background: var(--track); color: var(--muted); font-size: 11px; }}
.hc.none {{ background: transparent; }}
footer {{ font-size: 12px; color: var(--muted); display: grid; gap: 3px; }}
</style>

<div class="page">
  <header>
    <div>
      <h1>菜品口碑看板</h1>
      <p class="sub">{md(D['from'])} – {md(D['to'])} · {weeks} 周 · 13 家门店（华东 {R['华东']['stores']} 家、华南 {R['华南']['stores']} 家）</p>
    </div>
    <div class="kpi">
      <div><span class="v">{T['reviews']:,}</span><span class="l">顾客评价</span></div>
      <div><span class="v">{pct(T['rate'])}</span><span class="l">中差评率</span></div>
      <div><span class="v">{T['with_product']:,}</span><span class="l">提到具体菜品的评价</span></div>
    </div>
  </header>

  {f'<section><h2>要点</h2><ol class="notes">{note_html}</ol></section>' if notes else ''}

  <section>
    <h2>门店与区域</h2>
    <p class="cap">中差评率：4 星以下一律算；4 星及以上只有指出具体问题才算。浅黄 8%–12% 正常，浅红高于 12% 问题。</p>
    <div class="regions">
      <div class="region e"><b>华东 · 杭州恒隆、南京德基</b><span class="v">{pct(R['华东']['rate'])}</span><span class="l">{R['华东']['reviews']:,} 条评价，占全公司 {R['华东']['reviews'] / T['reviews'] * 100:.0f}%</span></div>
      <div class="region"><b>华南 · 深圳、广州 11 家</b><span class="v">{pct(R['华南']['rate'])}</span><span class="l">{R['华南']['reviews']:,} 条评价，占全公司 {R['华南']['reviews'] / T['reviews'] * 100:.0f}%</span></div>
    </div>
    <div class="scroll"><table>
      <thead><tr><th>门店</th><th>区域</th><th class="num">评价</th><th>中差评率</th><th>被提到最多的菜</th></tr></thead>
      <tbody>{srows}</tbody>
    </table></div>
  </section>

  <section>
    <h2>顾客最关心的菜：被提到的次数</h2>
    <p class="cap">一条评价提到同一道菜只算一次。「牛排」「意面」这类泛称对应不到具体菜品，没有计入。</p>
    <div class="legend"><span><i style="background:var(--south)"></i>华南</span><span><i style="background:var(--east)"></i>华东</span></div>
    <div class="mrows">{bars}</div>
  </section>

  <section>
    <h2>这 {len(P)} 道菜的差评率排名</h2>
    <p class="cap">明确不满率 = 对这道菜本身明确不满的评价 ÷ 提到这道菜的评价，口径与腹心肉 Q4 考核相同。主要问题是 AI 判定理由里出现最多的几类，数字为条数。</p>
    <div class="scroll"><table class="rank">
      <thead><tr><th class="num">#</th><th>菜品</th><th class="num">提及</th><th>明确不满率</th><th class="num">华南</th><th class="num">华东</th><th>主要问题</th></tr></thead>
      <tbody>{rrows}</tbody>
    </table></div>
  </section>

  <section>
    <h2>门店 × 菜品：明确不满率</h2>
    <p class="cap">提到最多的 {len(cols)} 道菜。颜色越深不满越多；灰格只有不到 {HEAT_MIN} 条提及，只显示条数；空格表示这家店没人提到。</p>
    <div class="scroll"><table class="heat">
      <thead><tr><th></th>{heads}</tr></thead>
      <tbody>{grid}</tbody>
    </table></div>
  </section>

  <footer>
    <span>数据：大众点评、美团，{md(D['from'])}–{md(D['to'])}。菜品由 AI 从每条评价中识别，同一道菜的不同写法已合并。</span>
    <span>腹心肉在这里按「AI 识别到这道菜」的评价计算；周报里的考核口径还包括只在文字里写到「腹心」的评价，所以两边数字略有差别。</span>
  </footer>
</div>
"""


def main():
    src, out = sys.argv[1:3]
    notes_file = sys.argv[3] if len(sys.argv) > 3 else None
    notes = [l.strip() for l in open(notes_file, encoding="utf-8") if l.strip()] if notes_file else []
    open(out, "w", encoding="utf-8").write(render(json.load(open(src, encoding="utf-8")), notes))
    print("->", out)


if __name__ == "__main__":
    main()
