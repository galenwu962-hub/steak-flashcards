"""One-page weekly board for 营运 and 店总.

    python -m app.weekly [--to 2026-09-25] [--out data/weekly/weekly.html]

The week is the 7 days ending at --to (default: the latest review date); it is compared
with the 7 days before. Per store: 中差评率, 分档, change vs last week. Company-wide:
this week's red-line items, new cross-store themes, progress on items issued earlier.

The output has no <html>/<head> wrapper, so it can be published as a hosted page as is.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from html import escape
from pathlib import Path

from . import queries
from .db import connect

SMALL_SAMPLE = 20          # fewer reviews than this in a week: one review moves the rate by 5+ points
THEMES_SHOWN = 5


def short(name: str) -> str:
    return re.sub(r"^(wow |P3 by )?pLeace2? ?(island )?(LOFT )?", "", name)


def _items(conn, where: str, params: list) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        f"SELECT i.*, s.name AS store FROM action_items i JOIN stores s ON s.id = i.store_id WHERE {where}", params)]
    for r in rows:
        r["evidence"] = json.loads(r.pop("evidence_json") or "[]")
    return rows


def build(conn, date_to: str | None = None) -> dict:
    date_to = date_to or conn.execute("SELECT max(review_date) FROM reviews").fetchone()[0]
    d1 = date.fromisoformat(date_to)
    f = queries.Filters((d1 - timedelta(days=6)).isoformat(), date_to)
    prev = f.previous()

    ov = queries.overview(conn, f)
    stores = queries.stores(conn, f)
    order = {"问题": 0, "正常": 1, "优秀": 2}
    stores.sort(key=lambda s: (order.get(s["grade"], 3), -(s["neg_rate"] or 0)))

    # Items: the latest period that ends inside this week is "this period"; earlier periods are
    # what the stores should have been working on.
    cur_period = conn.execute("SELECT max(period_to) FROM action_items WHERE period_to <= ?", [date_to]).fetchone()[0]
    cur_items = _items(conn, "i.period_to = ?", [cur_period]) if cur_period else []

    red = []
    for it in cur_items:
        if not it["escalate"]:
            continue
        wk = [e for e in it["evidence"] if f.date_from <= (e.get("review_date") or "") <= f.date_to]
        it["week_evidence"] = wk
        red.append(it)
    red_week = sorted([i for i in red if i["week_evidence"]], key=lambda i: -len(i["week_evidence"]))
    red_carry = [i for i in red if not i["week_evidence"] and i["status"] != "done"]

    themes = [dict(r) for r in conn.execute(
        "SELECT * FROM themes WHERE period_to = (SELECT max(period_to) FROM themes WHERE period_to <= ?) "
        "ORDER BY store_count DESC, evidence_total DESC", [date_to])]
    prev_period = conn.execute("SELECT max(period_to) FROM themes WHERE period_to < ?",
                               [themes[0]["period_to"] if themes else date_to]).fetchone()[0]
    known_cats = {r[0] for r in conn.execute("SELECT category FROM themes WHERE period_to = ?", [prev_period])}
    for t in themes:
        t["stores"] = json.loads(t.pop("stores_json") or "[]")
        # themes are re-worded each run; a category that already had a theme last time is not new
        t["new"] = not prev_period or t["category"] not in known_cats
    new_themes = [t for t in themes if t["new"]]

    earlier = _items(conn, "i.period_to < ?", [f.date_from])
    progress_items = earlier or cur_items
    progress = {}
    for it in progress_items:
        p = progress.setdefault(it["store"], {"store": it["store"], "total": 0, "done": 0, "high_open": 0})
        p["total"] += 1
        if it["status"] == "done":
            p["done"] += 1
        elif it["priority"] == "高":
            p["high_open"] += 1

    return {
        "from": f.date_from, "to": f.date_to, "prev_from": prev.date_from, "prev_to": prev.date_to,
        "overall": ov, "stores": stores,
        "grade_counts": {g: sum(s["grade"] == g for s in stores) for g in order},
        "red_week": red_week, "red_carry": red_carry,
        "themes": themes, "new_themes": new_themes, "first_themes": not prev_period,
        "progress": sorted(progress.values(), key=lambda p: (p["done"] / p["total"], -p["total"])),
        "progress_first": not earlier,
        "items_period": (cur_items[0]["period_from"], cur_period) if cur_items else None,
    }


# ---------------------------------------------------------------- rendering

def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _delta(cur, prev) -> str:
    if cur is None or prev is None:
        return '<span class="delta flat">—</span>'
    pp = (cur - prev) * 100
    if abs(pp) < 0.05:
        return '<span class="delta flat">持平</span>'
    cls, arrow = ("up", "↑") if pp > 0 else ("down", "↓")
    return f'<span class="delta {cls}">{arrow} {abs(pp):.1f}</span>'


def _md(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x.month}月{x.day}日"


def render(D: dict) -> str:
    cur, prv = D["overall"]["current"], D["overall"]["previous"]
    scale = max(0.30, max((s["neg_rate"] or 0) for s in D["stores"]) * 1.05)
    x = lambda r: f"{min(r, scale) / scale * 100:.2f}%"

    rows = []
    for s in D["stores"]:
        small = s["reviews"] < SMALL_SAMPLE
        rows.append(f"""
      <tr>
        <td class="store">{escape(short(s['name']))}</td>
        <td><span class="pill g-{s['grade']}">{s['grade']}</span></td>
        <td class="num rate">{_pct(s['neg_rate'])}<small>{s['negatives']}/{s['reviews']}{' · 样本少' if small else ''}</small></td>
        <td class="bar"><div class="track"><i class="band b1" style="left:{x(.08)};width:calc({x(.12)} - {x(.08)})"></i><i class="band b2" style="left:{x(.12)};right:0"></i><b class="dot g-{s['grade']}" style="left:{x(s['neg_rate'] or 0)}"></b></div></td>
        <td class="num">{_delta(s['neg_rate'], s['prev_neg_rate'])}</td>
      </tr>""")

    def quote(it, week=True):
        ev = (it.get("week_evidence") if week else None) or it["evidence"]
        e = next((e for e in ev if e.get("quote")), None)
        return f"「{escape(e['quote'])}」" if e else ""

    red = "".join(f"""
        <li><div class="li-head"><span class="who">{escape(short(i['store']))}</span><span class="tag">{len(i['week_evidence'])} 条本周意见</span></div>
          <p class="what">{escape(i['title'])}</p><p class="q">{quote(i)}</p></li>""" for i in D["red_week"]) \
        or '<li class="none">本周没有新的食安、卫生类投诉。</li>'
    carry = ""
    if D["red_carry"]:
        by_store = {}
        for i in D["red_carry"]:
            by_store[short(i["store"])] = by_store.get(short(i["store"]), 0) + 1
        names = "、".join(f"{escape(k)} {v} 条" for k, v in by_store.items())
        carry = f'<p class="foot">另有 {len(D["red_carry"])} 条上周的红线事项尚未关闭：{names}。</p>'

    shown = D["new_themes"][:THEMES_SHOWN]
    themes = "".join(f"""
        <li><div class="li-head"><span class="who">{escape(t['title'])}</span></div>
          <p class="meta">{escape(t['category'])} · {t['store_count']} 家门店 · {t['evidence_total']} 条意见</p></li>""" for t in shown) \
        or '<li class="none">本周没有新出现的跨店问题。</li>'
    theme_note = ("首期，全部视为新增。" if D["first_themes"] else "") + \
        (f"另有 {len(D['new_themes']) - len(shown)} 个见《改善事项清单》。" if len(D["new_themes"]) > len(shown) else "")

    total = sum(p["total"] for p in D["progress"])
    done = sum(p["done"] for p in D["progress"])
    prog = "".join(f"""
        <div class="pg"><span class="who">{escape(short(p['store']))}</span>
          <span class="meter"><i style="width:{p['done'] / p['total'] * 100:.0f}%"></i></span>
          <span class="num">{p['done']}/{p['total']}</span></div>""" for p in D["progress"])
    prog_title = "本期事项处理进度" if D["progress_first"] else "上周事项处理进度"
    prog_note = (f"改善事项本周首次下发（由 {_md(D['items_period'][0])}–{_md(D['items_period'][1])} 的评价归纳），下周起统计完成率。"
                 if D["progress_first"] else f"已完成 {done} 条，未完成 {total - done} 条。")

    gc = D["grade_counts"]
    return f"""<title>门店口碑周报</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@700&display=swap">
<style>
:root {{
  --bg: #f5f6f4; --paper: #ffffff; --ink: #1c2024; --muted: #676e76; --line: #e2e4e0; --track: #eceee9;
  --accent: #23495a;
  --good: #2e7a4f; --good-bg: #e3f1e8; --mid: #5d6975; --mid-bg: #eceff2; --bad: #b3261e; --bad-bg: #fbe7e4;
  --band1: #f3eddc; --band2: #f8e2de;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
    --accent: #8cc0d4;
    --good: #6cc592; --good-bg: #1f3529; --mid: #aab4be; --mid-bg: #2a3036; --bad: #ff8a7e; --bad-bg: #3d2320;
    --band1: #3a3524; --band2: #43282a;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
  --accent: #8cc0d4;
  --good: #6cc592; --good-bg: #1f3529; --mid: #aab4be; --mid-bg: #2a3036; --bad: #ff8a7e; --bad-bg: #3d2320;
  --band1: #3a3524; --band2: #43282a;
}}
body {{ background: var(--bg); color: var(--ink); font: 14px/1.55 "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif; }}
.page {{ max-width: 920px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 40px; display: grid; gap: 22px; }}
header {{ display: flex; flex-wrap: wrap; gap: 16px 32px; align-items: end; justify-content: space-between; }}
h1 {{ font: 700 26px/1.2 "Noto Serif SC", "Songti SC", serif; margin: 0; letter-spacing: .02em; }}
.sub {{ color: var(--muted); margin: 4px 0 0; }}
.kpi {{ display: flex; gap: 28px; flex-wrap: wrap; }}
.kpi div {{ display: grid; }}
.kpi .v {{ font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }}
.kpi .l {{ font-size: 12px; color: var(--muted); letter-spacing: .04em; }}
section {{ background: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 16px 18px; }}
h2 {{ font-size: 15px; margin: 0 0 10px; display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }}
h2 small {{ font-weight: 400; color: var(--muted); font-size: 12px; }}
.scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; min-width: 560px; }}
th {{ text-align: left; font-weight: 500; font-size: 12px; color: var(--muted); padding: 4px 8px 6px; border-bottom: 1px solid var(--line); }}
td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
.store {{ font-weight: 500; white-space: nowrap; }}
.rate {{ font-weight: 700; }}
.rate small {{ display: block; font-weight: 400; font-size: 11px; color: var(--muted); }}
.pill {{ display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; }}
.pill.g-优秀 {{ color: var(--good); background: var(--good-bg); }}
.pill.g-正常 {{ color: var(--mid); background: var(--mid-bg); }}
.pill.g-问题 {{ color: var(--bad); background: var(--bad-bg); font-weight: 700; }}
td.bar {{ width: 38%; }}
.track {{ position: relative; height: 10px; background: var(--track); border-radius: 5px; }}
.band {{ position: absolute; top: 0; bottom: 0; }}
.band.b1 {{ background: var(--band1); }} .band.b2 {{ background: var(--band2); border-radius: 0 5px 5px 0; }}
.dot {{ position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; border: 2px solid var(--paper); }}
.dot.g-优秀 {{ background: var(--good); }} .dot.g-正常 {{ background: var(--mid); }} .dot.g-问题 {{ background: var(--bad); }}
.scale {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 8px; }}
.scale i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; vertical-align: -1px; margin-right: 4px; }}
.delta {{ font-weight: 500; }} .delta.up {{ color: var(--bad); }} .delta.down {{ color: var(--good); }} .delta.flat {{ color: var(--muted); }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }}
@media (max-width: 720px) {{ .two {{ grid-template-columns: 1fr; }} }}
ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }}
li {{ padding-bottom: 10px; border-bottom: 1px solid var(--line); }}
li:last-child {{ border-bottom: 0; padding-bottom: 0; }}
.li-head {{ display: flex; justify-content: space-between; gap: 10px; }}
.who {{ font-weight: 500; }}
.tag {{ font-size: 12px; color: var(--bad); white-space: nowrap; }}
.red {{ border-top: 3px solid var(--bad); }}
.what {{ margin: 2px 0 0; }}
.q, .meta {{ margin: 2px 0 0; font-size: 12px; color: var(--muted); }}
.foot {{ font-size: 12px; color: var(--muted); margin: 12px 0 0; }}
.none {{ color: var(--muted); }}
.pgs {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 6px 28px; }}
.pg {{ display: grid; grid-template-columns: 7.5em 1fr 3.2em; gap: 10px; align-items: center; font-size: 13px; }}
.pg .who {{ font-weight: 400; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.pg .num {{ text-align: right; color: var(--muted); }}
.meter {{ height: 6px; background: var(--track); border-radius: 3px; overflow: hidden; }}
.meter i {{ display: block; height: 100%; background: var(--accent); }}
footer {{ font-size: 12px; color: var(--muted); display: grid; gap: 2px; }}
</style>

<div class="page">
  <header>
    <div>
      <h1>门店口碑周报</h1>
      <p class="sub">{_md(D['from'])} – {_md(D['to'])} · 对比 {_md(D['prev_from'])} – {_md(D['prev_to'])}</p>
    </div>
    <div class="kpi">
      <div><span class="v">{_pct(cur['neg_rate'])}</span><span class="l">全公司中差评率 {_delta(cur['neg_rate'], prv['neg_rate'])}</span></div>
      <div><span class="v">{cur['reviews']}</span><span class="l">本周评价</span></div>
      <div><span class="v" style="color:var(--bad)">{gc['问题']}</span><span class="l">问题门店 / 共 {len(D['stores'])} 家</span></div>
    </div>
  </header>

  <section>
    <h2>各门店中差评率<small>按分档排序，问题门店在前；变化单位为百分点</small></h2>
    <div class="scroll"><table>
      <thead><tr><th>门店</th><th>分档</th><th>中差评率</th><th>位置</th><th>较上周</th></tr></thead>
      <tbody>{''.join(rows)}
      </tbody>
    </table></div>
    <div class="scale"><span><i style="background:var(--track)"></i>&lt; 8% 优秀</span><span><i style="background:var(--band1)"></i>8%–12% 正常</span><span><i style="background:var(--band2)"></i>&gt; 12% 问题</span><span>样本少 = 本周评价不足 {SMALL_SAMPLE} 条，一条评价就能让比率变动 5 个百分点以上</span></div>
  </section>

  <div class="two">
    <section class="red">
      <h2>本周红线事项<small>食安、卫生等，一条就要处理</small></h2>
      <ul>{red}</ul>{carry}
    </section>
    <section>
      <h2>新增跨店问题<small>至少 3 家门店同时出现</small></h2>
      <ul>{themes}</ul>
      {f'<p class="foot">{theme_note}</p>' if theme_note else ''}
    </section>
  </div>

  <section>
    <h2>{prog_title}<small>{prog_note}</small></h2>
    <div class="pgs">{prog}</div>
  </section>

  <footer>
    <span>中差评：AI 判定为消极或中性的评价（有文字的以内容为准，不看星级）；未解析的按星级 ≤ 3 计。</span>
    <span>数据来自大众点评、美团，截至 {_md(D['to'])}。明细见《改善事项清单》。</span>
  </footer>
</div>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--out", default="data/weekly/weekly.html")
    args = ap.parse_args()
    D = build(connect(), args.date_to)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(D), encoding="utf-8")
    print(f"{D['from']}..{D['to']}: {len(D['stores'])} stores, {len(D['red_week'])} red-line, "
          f"{len(D['new_themes'])} new themes -> {out}")


if __name__ == "__main__":
    main()
