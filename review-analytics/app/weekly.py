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

from . import dish_kpi, queries
from .db import connect

SMALL_SAMPLE = 20          # fewer reviews than this in a week: one review moves the rate by 5+ points
THEMES_SHOWN = 5
# Dishes the company is actively fixing, tracked by their clear-dissatisfaction rate (app/dish_kpi.py):
# reviews clearly unhappy with the dish ÷ reviews mentioning it. baseline = September 2026 under that
# definition (matches 品控's hand count); target = the Q4 goal the owner set on 2026-10-05 (None = not set yet).
TRACKED_DISHES = [
    {"key": "腹心肉", "name": "椒麻炙烤腹心肉", "baseline": 0.087, "baseline_label": "9月",
     "target": 0.05, "note": "Q4 店总 × 总厨考核"},
]
TRACK_WEEKS = 8


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
    # Two runs can end on the same day (e.g. 9/12–9/25 and 9/1–9/25): take the most recently generated one.
    row = conn.execute("SELECT period_from, period_to FROM action_items WHERE period_to <= ? "
                       "ORDER BY period_to DESC, created_at DESC LIMIT 1", [date_to]).fetchone()
    cur_from, cur_period = (row[0], row[1]) if row else (None, None)
    cur_items = _items(conn, "i.period_from = ? AND i.period_to = ?", [cur_from, cur_period]) if row else []

    red = []
    for it in cur_items:
        if not it["escalate"]:
            continue
        wk = [e for e in it["evidence"] if f.date_from <= (e.get("review_date") or "") <= f.date_to]
        it["week_evidence"] = wk
        red.append(it)
    red_week = sorted([i for i in red if i["week_evidence"]], key=lambda i: -len(i["week_evidence"]))
    red_carry = [i for i in red if not i["week_evidence"] and i["status"] != "done"]
    # High-risk reviews this week that no improvement item covers yet (items are only generated every
    # two weeks, so new food-safety complaints would otherwise wait for the next run to show up).
    covered = {e["review_id"] for i in red_week for e in i["week_evidence"]}
    risk_week = [dict(r) for r in conn.execute(
        """SELECT r.id, r.review_date, r.star, r.content, a.summary, s.name AS store FROM review_analysis a
           JOIN reviews r ON r.id = a.review_id JOIN stores s ON s.id = r.store_id
           WHERE a.risk = 'high' AND r.review_date BETWEEN ? AND ? ORDER BY r.review_date""",
        [f.date_from, f.date_to]) if r["id"] not in covered]
    # Days with no reviews at all mean the platform data has not arrived yet, not a quiet day.
    have = {r[0] for r in conn.execute("SELECT DISTINCT review_date FROM reviews WHERE review_date BETWEEN ? AND ?",
                                       [f.date_from, f.date_to])}
    missing = [(d1 - timedelta(days=k)).isoformat() for k in range(6, -1, -1)
               if (d1 - timedelta(days=k)).isoformat() not in have]
    thin = [r[0] for r in conn.execute(
        "SELECT review_date FROM reviews WHERE review_date BETWEEN ? AND ? GROUP BY review_date HAVING count(*) < 5",
        [f.date_from, f.date_to])]

    themes = [dict(r) for r in conn.execute(
        "SELECT * FROM themes WHERE period_from = ? AND period_to = ? ORDER BY store_count DESC, evidence_total DESC",
        [cur_from, cur_period])]
    prev_period = conn.execute("SELECT max(period_to) FROM themes WHERE period_to < ?",
                               [themes[0]["period_to"] if themes else date_to]).fetchone()[0]
    known_cats = {r[0] for r in conn.execute("SELECT category FROM themes WHERE period_to = ?", [prev_period])}
    for t in themes:
        t["stores"] = json.loads(t.pop("stores_json") or "[]")
        # themes are re-worded each run; a category that already had a theme last time is not new
        t["new"] = not prev_period or t["category"] not in known_cats
    # themes are summarised with the items every two weeks; in a week without a new run nothing is new
    fresh_run = bool(cur_period) and cur_period >= f.date_from
    new_themes = [t for t in themes if t["new"]] if fresh_run else []

    # progress counts the latest issued batch from before this week (a re-run over a longer period
    # replaces the earlier batch for the same dates, so only the most recently generated one counts)
    row = conn.execute("SELECT period_from, period_to FROM action_items WHERE period_to < ? "
                       "ORDER BY created_at DESC LIMIT 1", [f.date_from]).fetchone()
    earlier = _items(conn, "i.period_from = ? AND i.period_to = ?", [row[0], row[1]]) if row else []
    # awareness-only items (kind 知晓) carry no task, so they are left out of the progress count
    progress_items = [i for i in (earlier or cur_items) if i.get("kind") != "知晓"]
    progress = {}
    for it in progress_items:
        p = progress.setdefault(it["store"], {"store": it["store"], "total": 0, "done": 0, "high_open": 0})
        p["total"] += 1
        if it["status"] == "done":
            p["done"] += 1
        elif it["priority"] == "高":
            p["high_open"] += 1

    dishes = []
    for t in TRACKED_DISHES:
        weeks = []
        for k in range(TRACK_WEEKS - 1, -1, -1):
            w1 = d1 - timedelta(days=7 * k)
            w0 = w1 - timedelta(days=6)
            r = dish_kpi.rate(conn, t["key"], w0.isoformat(), w1.isoformat())
            weeks.append({"from": w0.isoformat(), "to": w1.isoformat(), "n": r["n"], "neg": r["bad"],
                          "unjudged": r["unjudged"], "share": r["rate"]})
        dishes.append({**t, "weeks": weeks})

    return {
        "tracked_dishes": dishes,
        "from": f.date_from, "to": f.date_to, "prev_from": prev.date_from, "prev_to": prev.date_to,
        "overall": ov, "stores": stores,
        "grade_counts": {g: sum(s["grade"] == g for s in stores) for g in order},
        "red_week": red_week, "red_carry": red_carry, "risk_week": risk_week,
        "missing_days": missing, "thin_days": thin,
        "themes": themes, "new_themes": new_themes, "first_themes": not prev_period and fresh_run,
        "next_run": (date.fromisoformat(cur_period) + timedelta(days=14)).isoformat() if cur_period and not fresh_run else None,
        "progress": sorted(progress.values(), key=lambda p: (p["done"] / p["total"], -p["total"])),
        "progress_first": not earlier,
        "items_period": (cur_from, cur_period) if cur_items else None,
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


def _dish_rows(D: dict) -> str:
    out = []
    for t in D["tracked_dishes"]:
        wk = t["weeks"]
        cur, prv = wk[-1], wk[-2]
        vals = [w["share"] for w in wk]
        goal = t["target"] if t["target"] is not None else t["baseline"]
        top = max([v for v in vals if v is not None] + [t["baseline"], goal]) * 1.15 or 1
        W, H = 220, 44
        X = lambda i: 4 + i * (W - 8) / (len(wk) - 1)
        Y = lambda v: H - 4 - v / top * (H - 8)
        pts = [(X(i), Y(v)) for i, v in enumerate(vals) if v is not None]
        dots = "".join(
            f'<circle cx="{X(i):.1f}" cy="{Y(w["share"]):.1f}" r="{4 if i == len(wk) - 1 else 2.5}" class="{"end" if i == len(wk) - 1 else "pt"}">'
            f'<title>{_md(w["from"])}–{_md(w["to"])}：明确不满 {w["neg"]}/{w["n"]}（{w["share"] * 100:.0f}%）</title></circle>'
            for i, w in enumerate(wk) if w["share"] is not None)
        spark = (f'<svg class="spark" viewBox="0 0 {W} {H}" role="img" aria-label="近 {len(wk)} 周负面占比">'
                 f'<line x1="0" x2="{W}" y1="{Y(goal):.1f}" y2="{Y(goal):.1f}" class="tgt"/>'
                 f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" class="ln"/>{dots}</svg>')
        state = ("good" if cur["share"] is not None and cur["share"] <= goal else "bad")
        small = f'{cur["neg"]}/{cur["n"]} 条评价' + (" · 样本少" if cur["n"] < 30 else "") + \
            (f' · {cur["unjudged"]} 条待判定' if cur["unjudged"] else "")
        tgt = f'{t["target"] * 100:.0f}%' if t["target"] is not None else "待定"
        out.append(f"""
      <tr>
        <td class="store">{escape(t['name'])}<small class="muted">{escape(t['note'])}</small></td>
        <td class="num rate {state}">{_pct(cur['share'])}<small>{small}</small></td>
        <td class="num">{_delta(cur['share'], prv['share'])}</td>
        <td class="num muted">{t['baseline_label']} {t['baseline'] * 100:.1f}% → {tgt}</td>
        <td>{spark}</td>
      </tr>""")
    return "".join(out)


FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@700&display=swap">"""

STYLE = """:root {
  --bg: #f5f6f4; --paper: #ffffff; --ink: #1c2024; --muted: #676e76; --line: #e2e4e0; --track: #eceee9;
  --accent: #23495a;
  --good: #2e7a4f; --good-bg: #e3f1e8; --mid: #5d6975; --mid-bg: #eceff2; --bad: #b3261e; --bad-bg: #fbe7e4;
  --band1: #f3eddc; --band2: #f8e2de;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
    --accent: #8cc0d4;
    --good: #6cc592; --good-bg: #1f3529; --mid: #aab4be; --mid-bg: #2a3036; --bad: #ff8a7e; --bad-bg: #3d2320;
    --band1: #3a3524; --band2: #43282a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #15181b; --paper: #1d2125; --ink: #e8eaec; --muted: #9aa1a8; --line: #30353a; --track: #2a2f34;
  --accent: #8cc0d4;
  --good: #6cc592; --good-bg: #1f3529; --mid: #aab4be; --mid-bg: #2a3036; --bad: #ff8a7e; --bad-bg: #3d2320;
  --band1: #3a3524; --band2: #43282a;
}
body { background: var(--bg); color: var(--ink); font: 14px/1.55 "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif; }
.page { max-width: 920px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 40px; display: grid; gap: 22px; }
header { display: flex; flex-wrap: wrap; gap: 16px 32px; align-items: end; justify-content: space-between; }
h1 { font: 700 26px/1.2 "Noto Serif SC", "Songti SC", serif; margin: 0; letter-spacing: .02em; }
.sub { color: var(--muted); margin: 4px 0 0; }
.kpi { display: flex; gap: 28px; flex-wrap: wrap; }
.kpi div { display: grid; }
.kpi .v { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
.kpi .l { font-size: 12px; color: var(--muted); letter-spacing: .04em; }
section { background: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 16px 18px; }
h2 { font-size: 15px; margin: 0 0 10px; display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
h2 small { font-weight: 400; color: var(--muted); font-size: 12px; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 560px; }
th { text-align: left; font-weight: 500; font-size: 12px; color: var(--muted); padding: 4px 8px 6px; border-bottom: 1px solid var(--line); }
td { padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: 0; }
.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.store { font-weight: 500; white-space: nowrap; }
.rate { font-weight: 700; }
.rate small { display: block; font-weight: 400; font-size: 11px; color: var(--muted); }
.pill { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; }
.pill.g-优秀 { color: var(--good); background: var(--good-bg); }
.pill.g-正常 { color: var(--mid); background: var(--mid-bg); }
.pill.g-问题 { color: var(--bad); background: var(--bad-bg); font-weight: 700; }
td.bar { width: 38%; }
.track { position: relative; height: 10px; background: var(--track); border-radius: 5px; }
.band { position: absolute; top: 0; bottom: 0; }
.band.b1 { background: var(--band1); } .band.b2 { background: var(--band2); border-radius: 0 5px 5px 0; }
.dot { position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; border: 2px solid var(--paper); }
.dot.g-优秀 { background: var(--good); } .dot.g-正常 { background: var(--mid); } .dot.g-问题 { background: var(--bad); }
.scale { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 8px; }
.scale i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; vertical-align: -1px; margin-right: 4px; }
.delta { font-weight: 500; } .delta.up { color: var(--bad); } .delta.down { color: var(--good); } .delta.flat { color: var(--muted); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
@media (max-width: 720px) { .two { grid-template-columns: 1fr; } }
ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
li { padding-bottom: 10px; border-bottom: 1px solid var(--line); }
li:last-child { border-bottom: 0; padding-bottom: 0; }
.li-head { display: flex; justify-content: space-between; gap: 10px; }
.who { font-weight: 500; }
.tag { font-size: 12px; color: var(--bad); white-space: nowrap; }
.red { border-top: 3px solid var(--bad); }
.what { margin: 2px 0 0; }
.q, .meta { margin: 2px 0 0; font-size: 12px; color: var(--muted); }
.foot { font-size: 12px; color: var(--muted); margin: 12px 0 0; }
.none { color: var(--muted); }
.spark { width: 220px; height: 44px; display: block; }
.spark .ln { fill: none; stroke: var(--bad); stroke-width: 2; stroke-linejoin: round; }
.spark .pt { fill: var(--bad); }
.spark .end { fill: var(--bad); stroke: var(--paper); stroke-width: 2; }
.spark .tgt { stroke: var(--good); stroke-width: 1.5; stroke-dasharray: 4 3; }
.rate.bad { color: var(--bad); } .rate.good { color: var(--good); }
td small.muted { display: block; font-size: 11px; color: var(--muted); font-weight: 400; }
td.muted { color: var(--muted); }
.pgs { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 6px 28px; }
.pg { display: grid; grid-template-columns: 7.5em 1fr 3.2em; gap: 10px; align-items: center; font-size: 13px; }
.pg .who { font-weight: 400; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pg .num { text-align: right; color: var(--muted); }
.meter { height: 6px; background: var(--track); border-radius: 3px; overflow: hidden; }
.meter i { display: block; height: 100%; background: var(--accent); }
.gap { margin: 0; padding: 10px 14px; border-radius: 6px; background: var(--band1); color: var(--ink); font-size: 13px; }
footer { font-size: 12px; color: var(--muted); display: grid; gap: 2px; }
"""


def _gap_note(D: dict) -> str:
    gaps = sorted(set(D["missing_days"]) | set(D["thin_days"]))
    if not gaps:
        return ""
    days = "、".join(_md(d) for d in gaps)
    return (f'<p class="gap">数据提示：{days} 平台几乎没有评价数据，多半是数据源还没同步完，本周数字不完整，'
            f'与上周比较仅供参考。数据补齐后会重出。</p>')


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
        <li><div class="li-head"><span class="who">{escape(short(i['store']))}</span><span class="tag">本周 {len({e['review_id'] for e in i['week_evidence']})} 条评价</span></div>
          <p class="what">{escape(i['title'])}</p><p class="q">{quote(i)}</p></li>""" for i in D["red_week"]) + "".join(f"""
        <li><div class="li-head"><span class="who">{escape(short(r['store']))}</span><span class="tag">{_md(r['review_date'])} · {r['star']}★ · 新评价</span></div>
          <p class="what">{escape(r['summary'] or '')}</p></li>""" for r in D["risk_week"]) \
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
        or ('<li class="none">本周没有新的归纳，跨店问题每两周归纳一次。</li>' if D.get("next_run")
            else '<li class="none">本周没有新出现的跨店问题。</li>')
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
{FONTS}
<style>
{STYLE}</style>

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

  {_gap_note(D)}
  <section>
    <h2>各门店中差评率<small>按分档排序，问题门店在前；变化单位为百分点</small></h2>
    <div class="scroll"><table>
      <thead><tr><th>门店</th><th>分档</th><th>中差评率</th><th>位置</th><th>较上周</th></tr></thead>
      <tbody>{''.join(rows)}
      </tbody>
    </table></div>
    <div class="scale"><span><i style="background:var(--track)"></i>&lt; 8% 优秀</span><span><i style="background:var(--band1)"></i>8%–12% 正常</span><span><i style="background:var(--band2)"></i>&gt; 12% 问题</span><span>样本少 = 本周评价不足 {SMALL_SAMPLE} 条，一条评价就能让比率变动 5 个百分点以上</span></div>
  </section>

  <section>
    <h2>重点菜品追踪<small>明确不满率 = 对这道菜本身明确不满的评价 ÷ 提到这道菜的评价；虚线为{'目标' if all(t['target'] is not None for t in D['tracked_dishes']) else '基线（目标待定）'}</small></h2>
    <div class="scroll"><table>
      <thead><tr><th>菜品</th><th>本周明确不满率</th><th>较上周</th><th>基线 → 目标</th><th>近 {TRACK_WEEKS} 周</th></tr></thead>
      <tbody>{_dish_rows(D)}
      </tbody>
    </table></div>
  </section>

  <div class="two">
    <section class="red">
      <h2>本周红线事项<small>食安、卫生等，一条就要处理；「新评价」是还没归纳进事项表的</small></h2>
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
