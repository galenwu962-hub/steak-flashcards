"""Markdown versions of the weekly board and the monthly report, for the DingTalk cloud docs
kept in the folder 「门店口碑周报·月报」 (HANDOFF.md §10).

    python -m app.report_doc weekly  --to 2026-10-04 --out data/reports/周报_0928-1004.md
    python -m app.report_doc monthly --month 2026-09 --notes data/reports/monthly-2026-09-notes.txt --out data/reports/月报_2026-09.md

The PDF of the same report is attached to the doc afterwards (dws doc +media-insert).
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from . import monthly, weekly
from .db import connect
from .weekly import _md, short


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _pp(cur, prev) -> str:
    if cur is None or prev is None:
        return "—"
    d = (cur - prev) * 100
    return "持平" if abs(d) < 0.05 else f"{'↑' if d > 0 else '↓'} {abs(d):.1f}"


def _cell(s: str) -> str:
    return str(s).replace("|", "／").replace("\n", " ")


def _dish_line(t: dict, rate, bad: int, n: int) -> str:
    tgt = f"{t['target'] * 100:.0f}%" if t["target"] is not None else "待定"
    return (f"**{t['name']}** 明确不满率 {_pct(rate)}（{bad}/{n} 条）· "
            f"{t['baseline_label']}基线 {t['baseline'] * 100:.1f}% · Q4 目标 {tgt}")


def weekly_md(D: dict, title: str) -> str:
    cur, prv = D["overall"]["current"], D["overall"]["previous"]
    out = [f"# {title}", "",
           f"{_md(D['from'])}–{_md(D['to'])}，对比 {_md(D['prev_from'])}–{_md(D['prev_to'])}。PDF 版在文末，可直接转发。", ""]
    gaps = sorted(set(D["missing_days"]) | set(D["thin_days"]))
    if gaps:
        out += [f"> 数据提示：{'、'.join(_md(d) for d in gaps)} 平台几乎没有评价数据，本周数字不完整，与上周比较仅供参考。", ""]
    out += [f"**全公司中差评率 {_pct(cur['neg_rate'])}**（上周 {_pct(prv['neg_rate'])}）· 本周评价 {cur['reviews']} 条 · "
            f"问题门店 {D['grade_counts']['问题']} 家 / 共 {len(D['stores'])} 家", "",
            "## 各门店中差评率", "", "| 门店 | 分档 | 中差评率 | 评价数 | 较上周（百分点） |", "|---|---|---|---|---|"]
    for s in D["stores"]:
        small = " 样本少" if s["reviews"] < weekly.SMALL_SAMPLE else ""
        out.append(f"| {short(s['name'])} | {s['grade']} | {_pct(s['neg_rate'])} | {s['reviews']}{small} | {_pp(s['neg_rate'], s['prev_neg_rate'])} |")
    out += ["", "分档：低于 8% 优秀，8%–12% 正常，高于 12% 问题。样本少 = 本周评价不足 20 条。", "", "## 重点菜品追踪", ""]
    for t in D["tracked_dishes"]:
        w = t["weeks"][-1]
        out.append("- " + _dish_line(t, w["share"], w["neg"], w["n"]) + (" · 样本少" if w["n"] < 30 else ""))
    out += ["", "## 本周红线（食安、卫生）", ""]
    for i in D["red_week"]:
        out.append(f"- **{short(i['store'])}**：{_cell(i['title'])}（本周 {len({e['review_id'] for e in i['week_evidence']})} 条评价）")
    for r in D["risk_week"]:
        out.append(f"- **{short(r['store'])}** {_md(r['review_date'])} {r['star']}★：{_cell(r['summary'] or '')}（新评价，未归纳进事项）")
    if not D["red_week"] and not D["risk_week"]:
        out.append("- 本周没有新的食安、卫生类投诉。")
    if D["red_carry"]:
        out.append(f"- 另有 {len(D['red_carry'])} 条之前的红线事项尚未关闭。")
    out += ["", "## 跨店问题", ""]
    if D["new_themes"]:
        out += [f"- **{_cell(t['title'])}**：{t['category']} · {t['store_count']} 家门店 · {t['evidence_total']} 条意见" for t in D["new_themes"][:5]]
    else:
        out.append("- 本周没有新的归纳，跨店问题每两周归纳一次。")
    total = sum(p["total"] for p in D["progress"])
    done = sum(p["done"] for p in D["progress"])
    out += ["", "## 改善事项进度", "", f"已完成 {done} 条，未完成 {total - done} 条。状态以钉钉「门店改善事项跟进」表为准。", "",
            "## 口径", "",
            "- 中差评：4 星以下（含 3.5 星）一律算；4 星及以上只有指出具体问题（某道菜、服务环节、等位、环境、卫生、价格等）才算，「一般」「中规中矩」这类笼统意见不算。",
            "- 明确不满率：对这道菜本身明确不满的评价 ÷ 提到这道菜的评价；只嫌薯条酱汁、好评里的建议、夸贬参半不算。",
            f"- 数据来自大众点评、美团，截至 {_md(D['to'])}。", ""]
    return "\n".join(out)


def monthly_md(D: dict, title: str) -> str:
    k0, k1, k2 = D["kpi"]
    m = [monthly._m(x) for x in D["months"]]
    out = [f"# {title}", "", f"{_md(D['from'])}–{_md(D['to'])}，对比{m[1]}。PDF 版在文末，可直接转发。", "",
           f"**全公司中差评率 {_pct(k2['neg_rate'])}**（{m[1]} {_pct(k1['neg_rate'])}，{m[0]} {_pct(k0['neg_rate'])}）· "
           f"本月评价 {k2['reviews']:,} 条（{m[1]} {k1['reviews']:,}）· 问题门店 {D['grade_counts']['问题']} 家 / 共 {len(D['stores'])} 家", ""]
    if D["notes"]:
        out += ["## 本月要点", ""] + [f"{i}. {n}" for i, n in enumerate(D["notes"], 1)] + [""]
    out += ["## 各门店中差评率", "", f"| 门店 | {m[0]} | {m[1]} | {m[2]} | 分档 | 较{m[1]}（百分点） |", "|---|---|---|---|---|---|"]
    for s in D["stores"]:
        r = s["rates"]
        small = " 样本少" if s["reviews"] < monthly.SMALL_MONTH else ""
        out.append(f"| {short(s['name'])} | {_pct(r[0])} | {_pct(r[1])} | {_pct(r[2])}（{s['reviews']} 条{small}） | {s['grade']} | {_pp(r[2], r[1])} |")
    out += ["", "## 顾客抱怨最多的方面", "", f"| 方面 | {m[2]}有负面意见的评价 | 每 100 条评价 | 较{m[1]} |", "|---|---|---|---|"]
    for c in D["complaints"]:
        prev = f"{c['prev_per100']:.1f}" if c["prev_per100"] is not None else "—"
        out.append(f"| {c['aspect']} | {c['reviews']} | {c['per100']:.1f} | {prev} → {c['per100']:.1f} |")
    for t in D["dishes"]:
        tgt = f"{t['target'] * 100:.0f}%" if t["target"] is not None else "待定"
        out += ["", f"## 重点菜品：{t['name']}", "", f"{t['note']} · Q4 目标：明确不满率 {tgt} 以下", "",
                "| 月份 | 提到这道菜 | 明确不满 | 明确不满率 |", "|---|---|---|---|"]
        out += [f"| {monthly._m(x['month'])} | {x['n']} | {x['bad']} | {_pct(x['rate'])} |" for x in t["monthly"]]
        out += ["", f"| {m[2]}按门店 | 提到 | 明确不满 | 明确不满率 |", "|---|---|---|---|"]
        out += [f"| {short(r['name'])} | {r['n']} | {r['bad']} | {_pct(r['rate'])}{' 样本少' if r['n'] < 30 else ''} |"
                for r in t["by_store"]]
    out += ["", f"## 食安卫生红线（{m[2]} {len(D['red'])} 条，{m[1]} {D['prev_red']} 条）", ""]
    out += [f"- **{short(r['store'])}** {_md(r['review_date'])} {r['star']}★：{_cell(r['summary'] or '')}" for r in D["red"]] \
        or ["- 本月没有食安、卫生类高风险评价。"]
    P = D["progress"]
    out += ["", "## 改善事项进度", "",
            f"{_md(P['issued'])} 下发 {P['total']} 条（{P['aware']} 条仅需知晓）。截至 {_md(D['today'])}：完成 {P['done']}/{P['tasks']}，"
            f"已逾期 {P['overdue']} 条（红线 {P['overdue_red']} 条），今天到期 {P['due_today']} 条。这里是钉钉事项表里的状态，执行进度以品控 QSC 追踪表为准。", "",
            "## 口径", "",
            "- 中差评：4 星以下（含 3.5 星）一律算；4 星及以上只有指出具体问题（某道菜、服务环节、等位、环境、卫生、价格等）才算，「一般」「中规中矩」这类笼统意见不算。分档：低于 8% 优秀，8%–12% 正常，高于 12% 问题。",
            "- 明确不满率：对这道菜本身明确不满的评价 ÷ 提到这道菜的评价；只嫌薯条酱汁、好评里的建议、夸贬参半不算，熟度与所点不符、觉得不值算。", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="kind", required=True)
    w = sub.add_parser("weekly"); w.add_argument("--to", required=True); w.add_argument("--out", required=True)
    mo = sub.add_parser("monthly"); mo.add_argument("--month", required=True); mo.add_argument("--notes")
    mo.add_argument("--today", default=date.today().isoformat()); mo.add_argument("--out", required=True)
    args = ap.parse_args()
    conn = connect()
    if args.kind == "weekly":
        D = weekly.build(conn, args.to)
        md = weekly_md(D, f"门店口碑周报 · {_md(D['from'])}–{_md(D['to'])}")
    else:
        notes = [l.strip() for l in Path(args.notes).read_text(encoding="utf-8").splitlines() if l.strip()] if args.notes else []
        D = monthly.build(conn, args.month, notes, args.today)
        y, mm = args.month.split("-")
        md = monthly_md(D, f"门店口碑月报 · {y}年{int(mm)}月")
    Path(args.out).write_text(md, encoding="utf-8")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
