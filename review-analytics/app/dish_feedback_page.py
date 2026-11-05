"""One-page feedback sheet on one dish for R&D (研发), from app.dish_feedback's judgments.

    python -m app.dish_feedback_page --dish 皇后牛排 --from 2026-09-28 --to 2026-11-02 \
        --notes data/reports/皇后牛排-notes.txt --out data/reports/皇后牛排.html
    node scripts/render_report.js data/reports/皇后牛排.html --png ... --pdf ...

Notes file: the hand-written findings, one per line. Lines starting with 「差评：」「中评：」「好评：」 are not
findings but picks of quotes to show (the review date + store short name, e.g. 「中评：10-11 杭州恒隆店」).
"""
from __future__ import annotations

import argparse
import html
from collections import Counter, defaultdict
from pathlib import Path

from . import dish_feedback
from .db import connect
from .dish_kpi import rate
from .weekly import short

ASPECTS = ["肉质口感", "熟度", "配菜", "调味酱汁", "性价比", "摆盘卖相", "分量", "温度", "整体"]
VERDICTS = ["好评", "中评", "差评"]


def _md(d: str) -> str:
    return f"{int(d[5:7])}月{int(d[8:10])}日"


def _e(s) -> str:
    return html.escape(str(s or ""))


def _pct(a: int, b: int) -> str:
    return f"{a / b * 100:.0f}%" if b else "—"


def build(conn, dish: str, d0: str, d1: str, notes: list[str]) -> dict:
    rows = dish_feedback.export(conn, dish, d0, d1)
    for r in rows:
        r["store"] = short(r["store"])
    v = Counter(r["verdict"] for r in rows)
    stars = defaultdict(Counter)
    for r in rows:
        s = r["star"] or 0
        stars["4–5★" if s >= 4 else "3★" if s >= 3 else "1–2★" if s else "无星"][r["verdict"]] += 1
    asp = defaultdict(Counter)
    for r in rows:
        for a, sent in {(p["aspect"], p["sentiment"]) for p in r["points"]}:
            asp[a][sent] += 1
    stores = defaultdict(Counter)
    for r in rows:
        stores[r["store"]][r["verdict"]] += 1
    picks = {k: [] for k in VERDICTS}
    findings = []
    for line in notes:
        head, _, rest = line.partition("：")
        if head in picks:
            md_, store = rest.split(" ", 1)
            picks[head] += [r for r in rows if r["date"][5:] == md_ and r["store"] == store.strip() and r["verdict"] == head]
        else:
            findings.append(line)
    fx = rate(conn, "腹心肉", d0, d1)
    return {"dish": dish, "name": dish_feedback.DISHES[dish]["name"], "from": d0, "to": d1, "rows": rows, "v": v,
            "stars": stars, "asp": asp, "stores": stores, "picks": picks, "findings": findings, "fx": fx}


def render(D: dict) -> str:
    n, v = len(D["rows"]), D["v"]
    kpi = "".join(f'<div><span class="v {k}">{_pct(v[k], n)}</span><span class="l">{k} {v[k]} 条</span></div>' for k in VERDICTS)
    bar = "".join(f'<i class="{k}" style="flex:{v[k]}"></i>' for k in VERDICTS if v[k])
    star_rows = ""
    for s in ["4–5★", "3★", "1–2★", "无星"]:
        c = D["stars"].get(s)
        if not c:
            continue
        star_rows += f'<tr><td>{s}</td>' + "".join(f'<td class="num">{c[k] or "·"}</td>' for k in VERDICTS) + f'<td class="num">{sum(c.values())}</td></tr>'
    amax = max(max(c["正面"], c["负面"]) for c in D["asp"].values())
    asp_rows = ""
    for a in sorted(D["asp"], key=lambda a: (a == "整体", -sum(D["asp"][a].values()))):
        c = D["asp"][a]
        asp_rows += (f'<div class="arow"><span class="neg"><b>{c["负面"] or ""}</b><i style="width:{c["负面"] / amax * 100:.1f}%"></i></span>'
                     f'<span class="an">{a}</span><span class="pos"><i style="width:{c["正面"] / amax * 100:.1f}%"></i><b>{c["正面"] or ""}</b></span></div>')
    store_rows = ""
    for s, c in sorted(D["stores"].items(), key=lambda x: (-sum(x[1].values()), x[0])):
        t = sum(c.values())
        store_rows += (f'<tr><td class="store">{_e(s)}</td><td class="num">{t}</td>'
                       + "".join(f'<td class="num {k if c[k] else "muted"}">{c[k] or "·"}</td>' for k in VERDICTS)
                       + f'<td><span class="sbar">' + "".join(f'<i class="{k}" style="flex:{c[k]}"></i>' for k in VERDICTS if c[k]) + '</span></td></tr>')

    def quotes(k):
        out = ""
        for r in D["picks"][k]:
            neg = [p["quote"] for p in r["points"] if p["sentiment"] == "负面"]
            pos = [p["quote"] for p in r["points"] if p["sentiment"] == "正面"]
            q = "；".join((neg if k != "好评" else pos)[:2]) or r["summary"]
            out += f'<li><q>{_e(q)}</q><small>{_e(r["store"])} · {_md(r["date"])} · {r["star"] or "无"}★</small></li>'
        return out

    fx = D["fx"]
    findings = "".join(f"<li>{_e(f)}</li>" for f in D["findings"])
    return f"""<title>{_e(D['dish'])}顾客反馈</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@700&display=swap">
<style>
:root {{
  --bg: #f4f5f3; --paper: #ffffff; --ink: #1c2024; --muted: #676e76; --line: #e2e4e0; --track: #eceee9;
  --good: #2e7a4f; --mid: #a0a8b0; --bad: #b3261e; --good-bg: #e3f1e8; --bad-bg: #fbe7e4;
  --f-display: "Noto Serif SC", "Songti SC", serif;
  --f-body: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
  --good: #6cc592; --mid: #6f7880; --bad: #ff8a7e; --good-bg: #1f3529; --bad-bg: #3d2320; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
  --good: #6cc592; --mid: #6f7880; --bad: #ff8a7e; --good-bg: #1f3529; --bad-bg: #3d2320; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--ink); font: 14px/1.55 var(--f-body); margin: 0; }}
.page {{ max-width: 960px; margin: 0 auto; padding: 28px 16px 40px; display: grid; gap: 18px; }}
header {{ display: flex; flex-wrap: wrap; gap: 14px 32px; align-items: end; justify-content: space-between; }}
h1 {{ font: 700 26px/1.25 var(--f-display); margin: 0; letter-spacing: .02em; }}
.sub {{ color: var(--muted); margin: 4px 0 0; }}
.kpi {{ display: flex; gap: 26px; flex-wrap: wrap; }}
.kpi div {{ display: grid; }}
.kpi .v {{ font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.15; }}
.kpi .l {{ font-size: 12px; color: var(--muted); }}
.v.好评 {{ color: var(--good); }} .v.差评 {{ color: var(--bad); }}
.vbar {{ display: flex; gap: 2px; height: 12px; border-radius: 6px; overflow: hidden; }}
.vbar i, .sbar i {{ display: block; }}
i.好评 {{ background: var(--good); }} i.中评 {{ background: var(--mid); }} i.差评 {{ background: var(--bad); }}
section {{ background: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 16px 18px; min-width: 0; }}
h2 {{ font-size: 16px; margin: 0 0 4px; }}
.cap {{ font-size: 12px; color: var(--muted); margin: 0 0 12px; }}
.notes {{ margin: 0; padding-left: 1.2em; display: grid; gap: 6px; }}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
@media (max-width: 680px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; font-weight: 500; font-size: 12px; color: var(--muted); padding: 4px 6px 6px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
th.num {{ text-align: right; }}
td {{ padding: 6px 6px; border-bottom: 1px solid var(--line); }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }}
td.差评 {{ color: var(--bad); font-weight: 500; }} td.muted {{ color: var(--muted); }}
.store {{ white-space: nowrap; }}
.sbar {{ display: flex; gap: 1px; height: 8px; width: 90px; border-radius: 4px; overflow: hidden; margin-left: auto; }}
.arows {{ display: grid; gap: 6px; }}
.arow {{ display: grid; grid-template-columns: 1fr 5.5em 1fr; gap: 8px; align-items: center; font-size: 13px; }}
.arow .an {{ text-align: center; }}
.arow span.neg, .arow span.pos {{ display: flex; align-items: center; gap: 6px; height: 14px; }}
.arow .neg {{ justify-content: flex-end; }}
.arow i {{ display: block; height: 12px; border-radius: 3px; }}
.arow .neg i {{ background: var(--bad); }} .arow .pos i {{ background: var(--good); }}
.arow b {{ font-weight: 500; font-variant-numeric: tabular-nums; font-size: 12px; min-width: 1.5em; }}
.arow .neg b {{ text-align: right; color: var(--bad); }} .arow .pos b {{ color: var(--good); }}
.ahead {{ display: grid; grid-template-columns: 1fr 5.5em 1fr; gap: 8px; font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
.ahead span:first-child {{ text-align: right; }}
.qs {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
@media (max-width: 760px) {{ .qs {{ grid-template-columns: 1fr; }} }}
.qs h3 {{ font-size: 14px; margin: 0 0 6px; padding-top: 6px; border-top: 3px solid var(--mid); }}
.qs .好评 h3 {{ border-color: var(--good); }} .qs .差评 h3 {{ border-color: var(--bad); }}
.qs ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }}
.qs q {{ quotes: "「" "」"; font-size: 13px; }}
.qs small {{ display: block; font-size: 11px; color: var(--muted); }}
footer {{ font-size: 12px; color: var(--muted); display: grid; gap: 3px; }}
</style>
<div class="page">
  <header>
    <div><h1>{_e(D['name'])} · 顾客反馈</h1>
      <p class="sub">{_md(D['from'])}–{_md(D['to'])} · {n} 条评价说到这道菜 · 大众点评、美团 · 供研发部</p></div>
    <div class="kpi">{kpi}</div>
  </header>
  <div class="vbar">{bar}</div>
  <section><h2>要点</h2><ol class="notes">{findings}</ol></section>
  <div class="grid2">
    <section><h2>顾客在说什么</h2><p class="cap">提到该方面的评价条数（一条评价同一方面只算一次）</p>
      <div class="ahead"><span>负面</span><span></span><span>正面</span></div>
      <div class="arows">{asp_rows}</div></section>
    <section><h2>按星级看</h2><p class="cap">好/中/差评按顾客对这道菜本身的态度分，不按整条评价的星级</p>
      <table><tr><th>星级</th><th class="num">好评</th><th class="num">中评</th><th class="num">差评</th><th class="num">合计</th></tr>{star_rows}</table>
      <p class="cap" style="margin:12px 0 0">对照：同期腹心肉明确不满率 {fx['rate'] * 100:.1f}%（{fx['n']} 条里 {fx['bad']} 条），差评口径相同。</p></section>
  </div>
  <section><h2>顾客原话</h2><p class="cap">差评全部列出；中评、好评选有代表性的</p>
    <div class="qs"><div class="差评"><h3>差评 {v['差评']} 条</h3><ul>{quotes('差评')}</ul></div>
      <div class="中评"><h3>中评 {v['中评']} 条</h3><ul>{quotes('中评')}</ul></div>
      <div class="好评"><h3>好评 {v['好评']} 条</h3><ul>{quotes('好评')}</ul></div></div></section>
  <section><h2>分门店</h2><p class="cap">多数门店不到 10 条，只作参考</p>
    <table><tr><th>门店</th><th class="num">条数</th><th class="num">好评</th><th class="num">中评</th><th class="num">差评</th><th></th></tr>{store_rows}</table></section>
  <footer>
    <span>范围：正文提到这道菜，或买了皇后牛排套餐且正文说到牛排的评价；只夸套餐整体、没说到牛排的不计入。</span>
    <span>好评：明确夸这道菜。中评：「一般」「中规中矩」「不惊艳」、夸贬参半或提了改进建议。差评：明确不满（老、柴、腥、熟度不对、不值、不好吃等），与腹心肉考核口径一致。</span>
    <span>数据截至 {_md(D['to'])}，由 AI 逐条判读；全部原文和分类见附带的表格。</span>
  </footer>
</div>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dish", default="皇后牛排"); ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True); ap.add_argument("--notes", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    notes = [l.strip() for l in Path(a.notes).read_text(encoding="utf-8").splitlines() if l.strip()]
    Path(a.out).write_text(render(build(connect(), a.dish, a.d0, a.d1, notes)), encoding="utf-8")
    print("->", a.out)


if __name__ == "__main__":
    main()
