"""Export the dashboard as a static bundle that needs no server.

    python -m app.export_static [out_dir]        # default: data/static

Writes into out_dir:

    data.js          every API response the page can ask for, precomputed for the four
                     time presets × every store × every platform, plus the review rows
                     of the longest preset for client-side filtering
    echarts.min.js   copied from web/
    index.html       the dashboard with fetch() replaced by lookups into data.js
                     (no <html>/<head> wrapper: ready to publish as a hosted page)
    standalone.html  the same page with data.js and echarts inlined: double-click to open

The custom date inputs are disabled in the static page; the presets still work.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from . import queries
from .db import connect

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PRESETS = [7, 30, 90, 365]
SEGMENT_ROWS = 12  # the page shows at most 8 bars per segment; hour/weekday keep every row
REVIEW_DAYS = 90   # the review list ships this many days of rows (full text + analysis)

STATIC_JS = r"""
// --- static mode: answers come from window.STATIC instead of the API ---------------------
function staticKey() {
  const days = Math.round((new Date(state.to) - new Date(state.from)) / 864e5) + 1;
  const preset = STATIC.presets.reduce((b, p) => Math.abs(p - days) < Math.abs(b - days) ? p : b, STATIC.presets[0]);
  return `${preset}|${state.stores[0] || ''}|${state.platform || ''}`;
}
function staticReviews(extra) {
  const from = state.from, to = state.to, sid = state.stores[0] ? +state.stores[0] : null, pl = state.platform || null;
  const { sentiment, aspect, dish, risk, q } = extra;
  const rows = STATIC.reviews.filter(r =>
    r.review_date >= from && r.review_date <= to &&
    (sid == null || r.store_id === sid) && (pl == null || r.platform === pl) &&
    (!sentiment || (sentiment === 'negative' ? ((r.star != null && r.star <= 3) || r.sentiment === 'negative') : r.sentiment === sentiment)) &&
    (!aspect || r.aspects.some(a => a.aspect === aspect && a.sentiment === 'negative')) &&
    (!dish || r.aspects.some(a => a.dish === dish)) &&
    (!risk || r.risk === risk) &&
    (!q || (r.content || '').includes(q)));
  const size = 30, page = +extra.page || 1;
  return { total: rows.length, page, size, items: rows.slice((page - 1) * size, page * size) };
}
const api = (path, extra = {}) => Promise.resolve(path === 'reviews' ? staticReviews(extra) : (STATIC.data[staticKey()] || {})[path]);
"""


def build_data(conn) -> dict:
    meta = queries.meta(conn)
    end = date.fromisoformat(meta["date_max"])
    data: dict[str, dict] = {}
    store_ids = [None] + [s["id"] for s in meta["stores"]]
    platforms = [None] + list(meta["platforms"])
    for days in PRESETS:
        start = end - timedelta(days=days - 1)
        for sid in store_ids:
            for pl in platforms:
                f = queries.Filters(start.isoformat(), end.isoformat(), [sid] if sid else [], pl)
                seg = queries.segments(conn, f)
                for k, rows in seg.items():
                    if k not in ("hour", "weekday"):
                        seg[k] = rows[:SEGMENT_ROWS]
                # the business charts read only these columns
                business = [{k: b[k] for k in ("date", "exposure", "buyers", "revenue", "reviews", "negatives")}
                            for b in queries.business(conn, f)]
                data[f"{days}|{sid or ''}|{pl or ''}"] = {
                    "overview": queries.overview(conn, f), "trend": queries.trend(conn, f),
                    "stores": queries.stores(conn, f), "aspects": queries.aspects(conn, f),
                    "dishes": queries.dishes(conn, f), "segments": seg,
                    "business": business, "alerts": queries.alerts(conn, f),
                }
        print(f"preset {days}d: {len(store_ids) * len(platforms)} combinations", file=sys.stderr)

    since = (end - timedelta(days=REVIEW_DAYS - 1)).isoformat()
    reviews = queries._rows(conn, f"""
        SELECT r.review_date, r.review_time, r.store_id, r.platform, r.star, r.content, r.nickname,
               r.high_v, r.package_name, s.name AS store, a.sentiment, a.summary, a.reply_suggestion,
               a.risk, a.scenario, a.daypart, r.id
        {queries.BASE_JOIN} LEFT JOIN stores s ON s.id = r.store_id
        WHERE r.review_date >= ? ORDER BY r.review_time DESC""", [since])
    by_id: dict[str, list] = {}
    for a in queries._rows(conn, """SELECT x.review_id, x.aspect, x.sentiment, x.dish FROM review_aspects x
                                    JOIN reviews r ON r.id = x.review_id WHERE r.review_date >= ?""", [since]):
        rid = a.pop("review_id")
        by_id.setdefault(rid, []).append({k: v for k, v in a.items() if v is not None})
    for r in reviews:
        r["aspects"] = by_id.get(r.pop("id"), [])
        for k in [k for k, v in r.items() if v is None]:
            del r[k]
    print(f"reviews since {since}: {len(reviews)}", file=sys.stderr)
    return {"meta": meta, "presets": PRESETS, "review_days": REVIEW_DAYS,
            "data": _round(data), "reviews": reviews}


def _round(x, nd=4):
    """Shorten floats (0.11571428571428571 -> 0.1157; the page shows one decimal of a percentage) and drop nulls."""
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_round(v, nd) for v in x]
    return x


def build_page(html: str) -> str:
    """Rewrite web/index.html so it reads from window.STATIC. Returns the full document."""
    html = html.replace('<script src="/static/echarts.min.js"></script>',
                        '<script src="echarts.min.js"></script>\n<script src="data.js"></script>')
    html = html.replace("const api = (path, extra) => fetch(`/api/${path}?${qs(extra)}`).then(r => r.json());", STATIC_JS)
    html = html.replace("META = await fetch('/api/meta').then(r => r.json());", "META = STATIC.meta;")
    html = html.replace('<div class="pager">', '<p class="legend">离线版的评价明细只包含最近 ' + str(REVIEW_DAYS) + ' 天的评价。</p>\n    <div class="pager">')
    html = html.replace('<input type="date" id="from">', '<input type="date" id="from" disabled title="离线版只支持左侧的固定时间段">')
    html = html.replace('<input type="date" id="to">', '<input type="date" id="to" disabled title="离线版只支持左侧的固定时间段">')
    for marker in ("<script src=\"echarts.min.js\">", "const api =", "META = STATIC.meta", 'id="from" disabled'):
        assert marker in html, f"index.html changed: could not find {marker!r}"
    return html


def artifact_body(doc: str) -> str:
    """Strip the document wrapper: hosted pages get their own <html>/<head>/<body>."""
    body = re.sub(r"^.*?<head>\s*", "", doc, count=1, flags=re.S)
    body = re.sub(r'<meta[^>]*>\s*', "", body)
    body = body.replace("</head>\n<body>\n", "").replace("</body>\n</html>", "")
    return body.strip() + "\n"


def main(argv=None):
    out = Path(argv[0]) if argv else ROOT / "data" / "static"
    out.mkdir(parents=True, exist_ok=True)
    conn = connect()
    payload = build_data(conn)
    # a few reviews carry U+FFFD from a broken emoji in the source export; hosting refuses the byte
    data_js = "window.STATIC = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("\ufffd", "") + ";\n"
    (out / "data.js").write_text(data_js, encoding="utf-8")
    shutil.copy(WEB / "echarts.min.js", out / "echarts.min.js")
    doc = build_page((WEB / "index.html").read_text(encoding="utf-8"))
    (out / "index.html").write_text(artifact_body(doc), encoding="utf-8")
    echarts = (out / "echarts.min.js").read_text(encoding="utf-8")
    single = doc.replace('<script src="echarts.min.js"></script>\n<script src="data.js"></script>',
                         f"<script>{echarts}</script>\n<script>{data_js}</script>")
    (out / "standalone.html").write_text(single, encoding="utf-8")
    for p in sorted(out.iterdir()):
        print(f"{p.name:<16} {p.stat().st_size / 1e6:6.1f} MB")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
