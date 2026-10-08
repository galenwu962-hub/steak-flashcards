"""Monthly review report for 总办: one month against the month before.

    python -m app.monthly --month 2026-09 [--notes data/reports/monthly-2026-09-notes.txt] [--out data/monthly/2026-09.html]

Sections: headline numbers, the takeaways written by hand (--notes, one per line), per-store
中差评率 for the last three months, the company's weekly trend, what customers complain about most,
the tracked dishes on the clear-dissatisfaction basis (app/dish_kpi.py), food-safety red lines and
progress on the improvement items. Same look as the weekly board (app/weekly.py).
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from html import escape
from pathlib import Path

from . import dish_kpi, queries
from .db import connect
from .weekly import FONTS, STYLE, TRACKED_DISHES, _delta, _md, _pct, short

# Deadlines in the AI table are counted from the issue date by priority (HANDOFF.md §6).
DUE_DAYS = {"红线": 1, "高": 3, "中": 7, "低": 14}
ISSUED = {"2026-09-01": "2026-09-28"}      # item period start -> date the items went out to the stores
SMALL_MONTH = 60                            # fewer reviews in a month: the rate is shaky
DISH_STORE_MIN = 10                         # stores mentioning the dish less often are summed up in one line


def month_range(ym: str) -> tuple[str, str]:
    y, m = map(int, ym.split("-"))
    d0 = date(y, m, 1)
    d1 = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    return d0.isoformat(), d1.isoformat()


def prev_month(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def _store_rates(conn, d0: str, d1: str) -> dict[int, dict]:
    return {r["id"]: r for r in queries._rows(conn, f"""
        SELECT s.id, s.name, count(*) AS reviews, sum({queries.NEG_EXPR}) AS negatives
        {queries.BASE_JOIN} JOIN stores s ON s.id = r.store_id WHERE r.review_date BETWEEN ? AND ? GROUP BY s.id""", [d0, d1])}


def build(conn, ym: str, notes: list[str], today: str) -> dict:
    months = [prev_month(prev_month(ym)), prev_month(ym), ym]
    ranges = [month_range(m) for m in months]
    d0, d1 = ranges[-1]
    kpi = [queries._kpi(conn, queries.Filters(*r)) for r in ranges]

    per = [_store_rates(conn, *r) for r in ranges]
    stores = []
    for sid, s in per[-1].items():
        rates = [(p[sid]["negatives"] / p[sid]["reviews"]) if sid in p and p[sid]["reviews"] else None for p in per]
        stores.append({"name": s["name"], "reviews": s["reviews"], "negatives": s["negatives"], "rates": rates,
                       "grade": queries.grade(rates[-1])})
    order = {"问题": 0, "正常": 1, "优秀": 2}
    stores.sort(key=lambda s: (order.get(s["grade"], 3), -(s["rates"][-1] or 0)))

    # weekly trend: Monday-to-Sunday weeks touching the last three months
    first = date.fromisoformat(ranges[0][0])
    wk0 = first - timedelta(days=first.weekday())
    weeks = []
    while wk0 <= date.fromisoformat(d1):
        w1 = min(wk0 + timedelta(days=6), date.fromisoformat(d1))
        k = queries._kpi(conn, queries.Filters(wk0.isoformat(), w1.isoformat()))
        weeks.append({"from": wk0.isoformat(), "to": w1.isoformat(), "reviews": k["reviews"], "rate": k["neg_rate"]})
        wk0 += timedelta(days=7)

    def neg_aspects(a, b):
        return {r["aspect"]: r for r in queries._rows(conn, """
            SELECT x.aspect, x.category, count(*) AS n, count(DISTINCT x.review_id) AS reviews
            FROM review_aspects x JOIN reviews r ON r.id = x.review_id
            WHERE x.sentiment = 'negative' AND r.review_date BETWEEN ? AND ? GROUP BY x.aspect""", [a, b])}
    cur_a, prev_a = neg_aspects(d0, d1), neg_aspects(*ranges[-2])
    analyzed = [k["analyzed"] or 0 for k in kpi]
    complaints = sorted(cur_a.values(), key=lambda r: -r["reviews"])[:8]
    for c in complaints:
        p = prev_a.get(c["aspect"])
        # per 100 analysed reviews, so months with different volumes compare fairly
        c["per100"] = c["reviews"] / analyzed[-1] * 100 if analyzed[-1] else None
        c["prev_per100"] = (p["reviews"] / analyzed[-2] * 100) if p and analyzed[-2] else None

    dishes = []
    for t in TRACKED_DISHES:
        monthly = [{"month": m, **dish_kpi.rate(conn, t["key"], *r)} for m, r in zip(months, ranges)]
        by_store = []
        for sid, s in per[-1].items():
            r = dish_kpi.rate(conn, t["key"], d0, d1, store_id=sid)
            if r["n"]:
                by_store.append({"name": s["name"], **r})
        by_store.sort(key=lambda r: (-r["n"]))
        dishes.append({**t, "monthly": monthly, "by_store": by_store})

    red = [dict(r) for r in conn.execute(
        """SELECT r.review_date, r.star, a.summary, s.name AS store FROM review_analysis a
           JOIN reviews r ON r.id = a.review_id JOIN stores s ON s.id = r.store_id
           WHERE a.risk = 'high' AND r.review_date BETWEEN ? AND ? ORDER BY r.review_date""", [d0, d1])]
    prev_red = conn.execute("SELECT count(*) FROM review_analysis a JOIN reviews r ON r.id = a.review_id "
                            "WHERE a.risk = 'high' AND r.review_date BETWEEN ? AND ?", ranges[-2]).fetchone()[0]

    items = []
    for it in conn.execute("SELECT period_from, kind, priority, escalate, status FROM action_items "
                           "WHERE period_from IN (%s)" % ",".join("?" * len(ISSUED)), list(ISSUED)):
        level = "红线" if it["escalate"] else it["priority"]
        due = None
        if it["kind"] != "知晓":
            due = (date.fromisoformat(ISSUED[it["period_from"]]) + timedelta(days=DUE_DAYS.get(level, 7))).isoformat()
        items.append({"level": level, "kind": it["kind"], "done": it["status"] == "done", "due": due})
    tasks = [i for i in items if i["kind"] != "知晓"]
    progress = {
        "issued": min(ISSUED.values()), "total": len(items), "tasks": len(tasks), "aware": len(items) - len(tasks),
        "done": sum(i["done"] for i in tasks),
        "overdue": sum(1 for i in tasks if not i["done"] and i["due"] < today),
        "overdue_red": sum(1 for i in tasks if not i["done"] and i["due"] < today and i["level"] == "红线"),
        "due_today": sum(1 for i in tasks if not i["done"] and i["due"] == today),
    }

    return {"month": ym, "months": months, "from": d0, "to": d1, "kpi": kpi, "stores": stores, "weeks": weeks,
            "complaints": complaints, "dishes": dishes, "red": red, "prev_red": prev_red,
            "progress": progress, "notes": notes, "today": today,
            "grade_counts": {g: sum(s["grade"] == g for s in stores) for g in order}}


# ---------------------------------------------------------------- rendering

def _m(ym: str) -> str:
    return f"{int(ym.split('-')[1])}月"


def _trend_svg(weeks: list[dict]) -> str:
    W, H, P = 860, 150, 28
    top = max(0.20, max((w["rate"] or 0) for w in weeks) * 1.1)
    X = lambda i: P + i * (W - P - 8) / max(1, len(weeks) - 1)
    Y = lambda v: H - 22 - v / top * (H - 34)
    band = (f'<rect x="{P}" width="{W - P - 8}" y="{Y(.12):.1f}" height="{Y(.08) - Y(.12):.1f}" class="b1"/>'
            f'<rect x="{P}" width="{W - P - 8}" y="{Y(top):.1f}" height="{Y(.12) - Y(top):.1f}" class="b2"/>')
    ticks = "".join(f'<text x="{P - 6}" y="{Y(v) + 4:.1f}" class="ax" text-anchor="end">{v * 100:.0f}%</text>'
                    for v in (0.08, 0.12))
    pts = " ".join(f"{X(i):.1f},{Y(w['rate']):.1f}" for i, w in enumerate(weeks) if w["rate"] is not None)
    dots = "".join(
        f'<circle cx="{X(i):.1f}" cy="{Y(w["rate"]):.1f}" r="{3.5 if w["reviews"] >= 150 else 2.5}" class="{"pt" if w["reviews"] >= 150 else "thin"}">'
        f'<title>{_md(w["from"])}–{_md(w["to"])}：{w["rate"] * 100:.1f}%（{w["reviews"]} 条）</title></circle>'
        for i, w in enumerate(weeks) if w["rate"] is not None)
    labels, last = "", None
    for i, w in enumerate(weeks):
        m = int(w["to"][5:7])
        if m != last:
            labels += f'<text x="{X(i):.1f}" y="{H - 4}" class="ax">{m}月</text>'
            last = m
    return (f'<svg class="trend" viewBox="0 0 {W} {H}" role="img" aria-label="全公司每周中差评率">{band}{ticks}'
            f'<polyline points="{pts}" class="ln"/>{dots}{labels}</svg>')


def render(D: dict) -> str:
    k0, k1, k2 = D["kpi"]
    m0, m1, m2 = D["months"]
    scale = max(0.30, max((s["rates"][-1] or 0) for s in D["stores"]) * 1.05)

    rows = "".join(f"""
      <tr>
        <td class="store">{escape(short(s['name']))}</td>
        <td class="num muted">{_pct(s['rates'][0])}</td>
        <td class="num muted">{_pct(s['rates'][1])}</td>
        <td class="num rate">{_pct(s['rates'][2])}<small>{s['negatives']}/{s['reviews']}{' · 样本少' if s['reviews'] < SMALL_MONTH else ''}</small></td>
        <td><span class="pill g-{s['grade']}">{s['grade']}</span></td>
        <td class="num">{_delta(s['rates'][2], s['rates'][1])}</td>
      </tr>""" for s in D["stores"])

    top = max(c["per100"] or 0 for c in D["complaints"]) or 1
    comp = "".join(f"""
        <div class="cm"><span class="who">{escape(c['aspect'])}</span>
          <span class="meter wide"><i style="width:{(c['per100'] or 0) / top * 100:.0f}%"></i></span>
          <span class="num">{c['reviews']} 条</span>
          <span class="num">{_delta((c['per100'] or 0) / 100, (c['prev_per100'] / 100) if c['prev_per100'] is not None else None)}</span></div>"""
                   for c in D["complaints"])

    dish_html = ""
    for t in D["dishes"]:
        mrow = "".join(
            f'<td class="num{" rate" if i == 2 else " muted"}">{_pct(x["rate"]) if not x["unjudged"] else "待判定"}'
            f'<small>{x["bad"]}/{x["n"]} 条</small></td>' for i, x in enumerate(t["monthly"]))
        big = [r for r in t["by_store"] if r["n"] >= DISH_STORE_MIN]
        rest = [r for r in t["by_store"] if r["n"] < DISH_STORE_MIN]
        rest_note = (f'其余 {len(rest)} 家店合计提到 {sum(r["n"] for r in rest)} 条，明确不满 {sum(r["bad"] for r in rest)} 条：'
                     + "、".join(f'{escape(short(r["name"]))} {r["bad"]}/{r["n"]}' for r in rest) + "。") if rest else ""
        srows = "".join(f"""
          <tr><td class="store">{escape(short(r['name']))}</td><td class="num">{r['n']}</td><td class="num">{r['bad']}</td>
              <td class="num rate">{_pct(r['rate'])}{'<small>样本少</small>' if r['n'] < 30 else ''}</td></tr>""" for r in big)
        tgt = f"{t['target'] * 100:.0f}%" if t["target"] is not None else "待定"
        dish_html += f"""
  <section>
    <h2>重点菜品：{escape(t['name'])}<small>{escape(t['note'])} · 明确不满率 = 对这道菜本身明确不满的评价 ÷ 提到这道菜的评价 · Q4 目标：{tgt}</small></h2>
    <div class="scroll"><table class="tight">
      <thead><tr><th></th><th>{_m(m0)}</th><th>{_m(m1)}</th><th>{_m(m2)}</th></tr></thead>
      <tbody><tr><td class="store">全公司</td>{mrow}</tr></tbody>
    </table></div>
    <div class="scroll" style="margin-top:12px"><table class="tight">
      <thead><tr><th>{_m(m2)}按门店</th><th>提到</th><th>明确不满</th><th>明确不满率</th></tr></thead>
      <tbody>{srows}</tbody>
    </table></div>
    {f'<p class="foot">{rest_note}</p>' if rest_note else ''}
    <p class="foot">只嫌薯条、酱汁、熟度选项，好评里的建议语气，夸贬参半，都不算；熟度跟点的不一致、觉得不值，算。标准由品控逐条核对 9 月评价后确定。
    一家店一个月提到这道菜的评价多在 30 条以下，多一条不满就差好几个百分点，门店层面建议按季度累计考核。</p>
  </section>"""

    by_store = {}
    for r in D["red"]:
        by_store[short(r["store"])] = by_store.get(short(r["store"]), 0) + 1
    red_list = "".join(f"""
        <li><div class="li-head"><span class="who">{escape(short(r['store']))}</span><span class="tag">{_md(r['review_date'])} · {r['star']}★</span></div>
          <p class="what">{escape(r['summary'] or '')}</p></li>""" for r in D["red"]) or '<li class="none">本月没有食安、卫生类高风险评价。</li>'
    red_sum = "、".join(f"{escape(k)} {v} 条" for k, v in sorted(by_store.items(), key=lambda kv: -kv[1]))

    P = D["progress"]
    notes = "".join(f"<li>{escape(n)}</li>" for n in D["notes"])
    gc = D["grade_counts"]

    return f"""<title>门店口碑月报</title>
{FONTS}
<style>
{STYLE}
.notes {{ list-style: decimal; padding-left: 1.3em; gap: 6px; }}
.notes li {{ border: 0; padding: 0; }}
.trend {{ width: 100%; height: auto; display: block; }}
.trend .b1 {{ fill: var(--band1); }} .trend .b2 {{ fill: var(--band2); }}
.trend .ln {{ fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; }}
.trend .pt {{ fill: var(--accent); }} .trend .thin {{ fill: var(--paper); stroke: var(--accent); stroke-width: 1.5; }}
.trend .ax {{ fill: var(--muted); font-size: 11px; }}
.cms {{ display: grid; gap: 6px; }}
.cm {{ display: grid; grid-template-columns: 6.5em 1fr 3.5em 3.8em; gap: 10px; align-items: center; font-size: 13px; }}
.cm .num {{ text-align: right; }}
table.tight {{ min-width: 360px; }}
table.tight td small {{ display: block; font-size: 11px; color: var(--muted); font-weight: 400; }}
.stat {{ display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 6px; }}
.stat div {{ display: grid; }} .stat .v {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.stat .l {{ font-size: 12px; color: var(--muted); }}
</style>

<div class="page">
  <header>
    <div>
      <h1>门店口碑月报 · {_m(m2)}</h1>
      <p class="sub">{_md(D['from'])} – {_md(D['to'])} · 对比 {_m(m1)}</p>
    </div>
    <div class="kpi">
      <div><span class="v">{_pct(k2['neg_rate'])}</span><span class="l">全公司中差评率 {_delta(k2['neg_rate'], k1['neg_rate'])}</span></div>
      <div><span class="v">{k2['reviews']:,}</span><span class="l">本月评价（{_m(m1)} {k1['reviews']:,}）</span></div>
      <div><span class="v" style="color:var(--bad)">{gc['问题']}</span><span class="l">问题门店 / 共 {len(D['stores'])} 家</span></div>
    </div>
  </header>

  {f'<section><h2>本月要点</h2><ol class="notes">{notes}</ol></section>' if notes else ''}

  <section>
    <h2>全公司每周中差评率<small>近三个月；空心点 = 当周评价不足 150 条；浅黄 8%–12% 正常，浅红 &gt; 12% 问题</small></h2>
    {_trend_svg(D['weeks'])}
  </section>

  <section>
    <h2>各门店中差评率<small>按{_m(m2)}分档排序；变化单位为百分点</small></h2>
    <div class="scroll"><table>
      <thead><tr><th>门店</th><th>{_m(m0)}</th><th>{_m(m1)}</th><th>{_m(m2)}</th><th>分档</th><th>较{_m(m1)}</th></tr></thead>
      <tbody>{rows}
      </tbody>
    </table></div>
    <div class="scale"><span>&lt; 8% 优秀</span><span>8%–12% 正常</span><span>&gt; 12% 问题</span><span>样本少 = 当月评价不足 {SMALL_MONTH} 条</span></div>
  </section>

  <section>
    <h2>顾客抱怨最多的方面<small>{_m(m2)}有负面意见的评价条数；变化 = 每 100 条评价里提到的条数，较{_m(m1)}增减</small></h2>
    <div class="cms">{comp}</div>
  </section>
{dish_html}
  <div class="two">
    <section class="red">
      <h2>食安卫生红线<small>{_m(m2)} {len(D['red'])} 条（{_m(m1)} {D['prev_red']} 条）</small></h2>
      <ul>{red_list}</ul>
      {f'<p class="foot">按门店：{red_sum}。</p>' if red_sum else ''}
    </section>
    <section>
      <h2>改善事项进度<small>{_md(P['issued'])} 下发</small></h2>
      <div class="stat">
        <div><span class="v">{P['done']}/{P['tasks']}</span><span class="l">已完成</span></div>
        <div><span class="v" style="color:var(--bad)">{P['overdue']}</span><span class="l">已逾期，其中红线 {P['overdue_red']}</span></div>
        <div><span class="v">{P['due_today']}</span><span class="l">今天到期</span></div>
      </div>
      <p class="foot">共 {P['total']} 条，由 9 月 1 日–25 日的评价归纳；其中 {P['aware']} 条仅需知晓，不计入。这里是钉钉「门店改善事项跟进」表里的状态，执行进度以品控 QSC 追踪表为准；统计到 {_md(D['today'])}。</p>
    </section>
  </div>

  <footer>
    <span>中差评：AI 判定为消极或中性的评价（有文字的以内容为准，不看星级）；未解析的按星级 ≤ 3 计。</span>
    <span>数据来自大众点评、美团，{_md(D['from'])}–{_md(D['to'])}。</span>
  </footer>
</div>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--notes", help="text file, one takeaway per line")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--out")
    args = ap.parse_args()
    notes = [l.strip() for l in Path(args.notes).read_text(encoding="utf-8").splitlines() if l.strip()] if args.notes else []
    D = build(connect(), args.month, notes, args.today)
    out = Path(args.out or f"data/monthly/{args.month}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(D), encoding="utf-8")
    print(f"{args.month}: {len(D['stores'])} stores, {len(D['red'])} red-line reviews -> {out}")
    return D


if __name__ == "__main__":
    main()
