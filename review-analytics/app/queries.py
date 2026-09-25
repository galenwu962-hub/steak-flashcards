"""Aggregation queries behind the dashboard API.

One definition of 差评 everywhere: a review is negative when its star is 3 or
below, or when the Claude analysis judged the overall sentiment negative.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

NEG_EXPR = "CASE WHEN r.star IS NOT NULL AND r.star <= 3 THEN 1 WHEN a.sentiment = 'negative' THEN 1 ELSE 0 END"
BASE_JOIN = "FROM reviews r LEFT JOIN review_analysis a ON a.review_id = r.id"


@dataclass
class Filters:
    date_from: str
    date_to: str
    store_ids: list[int] = field(default_factory=list)
    platform: str | None = None

    def where(self, alias: str = "r") -> tuple[str, list]:
        clauses = [f"{alias}.review_date BETWEEN ? AND ?"]
        params: list = [self.date_from, self.date_to]
        if self.store_ids:
            clauses.append(f"{alias}.store_id IN ({','.join('?' * len(self.store_ids))})")
            params += self.store_ids
        if self.platform:
            clauses.append(f"{alias}.platform = ?")
            params.append(self.platform)
        return " AND ".join(clauses), params

    def previous(self) -> "Filters":
        d0, d1 = date.fromisoformat(self.date_from), date.fromisoformat(self.date_to)
        span = (d1 - d0).days + 1
        return Filters((d0 - timedelta(days=span)).isoformat(), (d0 - timedelta(days=1)).isoformat(),
                       self.store_ids, self.platform)


def _rows(conn: sqlite3.Connection, sql: str, params: list) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def meta(conn: sqlite3.Connection) -> dict:
    stores = _rows(conn, """SELECT s.id, s.name, s.brand, s.city, count(r.id) AS n
                            FROM stores s LEFT JOIN reviews r ON r.store_id = s.id
                            GROUP BY s.id ORDER BY n DESC""", [])
    rng = conn.execute("SELECT min(review_date) AS d0, max(review_date) AS d1, count(*) AS n FROM reviews").fetchone()
    analyzed = conn.execute("SELECT count(*) FROM review_analysis").fetchone()[0]
    platforms = [r["platform"] for r in conn.execute("SELECT DISTINCT platform FROM reviews WHERE platform IS NOT NULL")]
    return {"stores": stores, "date_min": rng["d0"], "date_max": rng["d1"], "reviews": rng["n"],
            "analyzed": analyzed, "platforms": platforms,
            "definitions": {"差评": "星级 ≤ 3，或 AI 判定总体倾向为消极", "差评率": "差评数 ÷ 评价量"}}


def _kpi(conn, f: Filters) -> dict:
    w, p = f.where()
    row = conn.execute(f"""
        SELECT count(*) AS reviews, sum({NEG_EXPR}) AS negatives, avg(r.star) AS avg_star,
               sum(CASE WHEN r.star <= 3 THEN 1 ELSE 0 END) AS low_star,
               sum(CASE WHEN a.sentiment = 'negative' THEN 1 ELSE 0 END) AS ai_negative,
               sum(CASE WHEN a.review_id IS NOT NULL THEN 1 ELSE 0 END) AS analyzed,
               sum(CASE WHEN a.risk = 'high' THEN 1 ELSE 0 END) AS high_risk,
               sum(CASE WHEN r.high_v IS NOT NULL THEN 1 ELSE 0 END) AS high_v,
               sum(CASE WHEN r.has_reply = 1 THEN 1 ELSE 0 END) AS replied
        {BASE_JOIN} WHERE {w}""", p).fetchone()
    d = dict(row)
    d["neg_rate"] = (d["negatives"] or 0) / d["reviews"] if d["reviews"] else None
    return d


def overview(conn, f: Filters) -> dict:
    return {"current": _kpi(conn, f), "previous": _kpi(conn, f.previous()),
            "period": {"from": f.date_from, "to": f.date_to}}


def trend(conn, f: Filters, granularity: str = "day") -> list[dict]:
    w, p = f.where()
    bucket = "r.review_date" if granularity == "day" else "date(r.review_date, 'weekday 0', '-6 days')"
    return _rows(conn, f"""
        SELECT {bucket} AS d, count(*) AS reviews, sum({NEG_EXPR}) AS negatives, avg(r.star) AS avg_star,
               sum(CASE WHEN r.platform = '大众点评' THEN 1 ELSE 0 END) AS dianping,
               sum(CASE WHEN r.platform = '美团' THEN 1 ELSE 0 END) AS meituan
        {BASE_JOIN} WHERE {w} GROUP BY d ORDER BY d""", p)


def stores(conn, f: Filters) -> list[dict]:
    w, p = f.where()
    w2, p2 = f.previous().where()
    cur = _rows(conn, f"""
        SELECT s.id, s.name, s.city, count(*) AS reviews, sum({NEG_EXPR}) AS negatives, avg(r.star) AS avg_star,
               sum(CASE WHEN a.review_id IS NOT NULL THEN 1 ELSE 0 END) AS analyzed
        {BASE_JOIN} JOIN stores s ON s.id = r.store_id WHERE {w} GROUP BY s.id ORDER BY reviews DESC""", p)
    prev = {r["id"]: r for r in _rows(conn, f"""
        SELECT s.id, count(*) AS reviews, sum({NEG_EXPR}) AS negatives
        {BASE_JOIN} JOIN stores s ON s.id = r.store_id WHERE {w2} GROUP BY s.id""", p2)}
    top_neg = {}
    for r in _rows(conn, f"""
        SELECT r.store_id, x.aspect, count(*) AS n
        FROM review_aspects x JOIN reviews r ON r.id = x.review_id
        WHERE x.sentiment = 'negative' AND {w} GROUP BY r.store_id, x.aspect ORDER BY n DESC""", p):
        top_neg.setdefault(r["store_id"], f"{r['aspect']}({r['n']})")
    for s in cur:
        s["neg_rate"] = s["negatives"] / s["reviews"] if s["reviews"] else None
        pv = prev.get(s["id"])
        s["prev_reviews"] = pv["reviews"] if pv else 0
        s["prev_neg_rate"] = (pv["negatives"] / pv["reviews"]) if pv and pv["reviews"] else None
        s["top_negative_aspect"] = top_neg.get(s["id"])
    return cur


def aspects(conn, f: Filters) -> dict:
    w, p = f.where()
    by_aspect = _rows(conn, f"""
        SELECT x.category, x.aspect,
               sum(CASE WHEN x.sentiment='positive' THEN 1 ELSE 0 END) AS positive,
               sum(CASE WHEN x.sentiment='negative' THEN 1 ELSE 0 END) AS negative,
               sum(CASE WHEN x.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral,
               count(DISTINCT x.review_id) AS reviews
        FROM review_aspects x JOIN reviews r ON r.id = x.review_id
        WHERE {w} GROUP BY x.category, x.aspect ORDER BY negative DESC""", p)
    by_category: dict[str, dict] = {}
    for a in by_aspect:
        c = by_category.setdefault(a["category"], {"category": a["category"], "positive": 0, "negative": 0, "neutral": 0})
        for k in ("positive", "negative", "neutral"):
            c[k] += a[k]
    heat = _rows(conn, f"""
        SELECT s.name AS store, x.aspect, count(*) AS negative
        FROM review_aspects x JOIN reviews r ON r.id = x.review_id JOIN stores s ON s.id = r.store_id
        WHERE x.sentiment = 'negative' AND {w} GROUP BY s.name, x.aspect""", p)
    prev_w, prev_p = f.previous().where()
    prev = {r["aspect"]: r["negative"] for r in _rows(conn, f"""
        SELECT x.aspect, count(*) AS negative FROM review_aspects x JOIN reviews r ON r.id = x.review_id
        WHERE x.sentiment = 'negative' AND {prev_w} GROUP BY x.aspect""", prev_p)}
    for a in by_aspect:
        a["prev_negative"] = prev.get(a["aspect"], 0)
    return {"by_category": list(by_category.values()), "by_aspect": by_aspect, "store_heatmap": heat}


def dishes(conn, f: Filters, limit: int = 30) -> dict:
    w, p = f.where()
    rows = _rows(conn, f"""
        SELECT x.dish, count(DISTINCT x.review_id) AS mentions,
               sum(CASE WHEN x.sentiment='positive' THEN 1 ELSE 0 END) AS positive,
               sum(CASE WHEN x.sentiment='negative' THEN 1 ELSE 0 END) AS negative,
               count(DISTINCT r.store_id) AS stores
        FROM review_aspects x JOIN reviews r ON r.id = x.review_id
        WHERE x.dish IS NOT NULL AND {w} GROUP BY x.dish HAVING mentions >= 2
        ORDER BY mentions DESC LIMIT {int(limit) * 3}""", p)
    for d in rows:
        d["neg_rate"] = d["negative"] / (d["positive"] + d["negative"]) if (d["positive"] + d["negative"]) else 0
    complaints = _rows(conn, f"""
        SELECT x.dish, x.aspect, count(*) AS n
        FROM review_aspects x JOIN reviews r ON r.id = x.review_id
        WHERE x.dish IS NOT NULL AND x.sentiment = 'negative' AND {w}
        GROUP BY x.dish, x.aspect ORDER BY n DESC LIMIT 60""", p)
    return {"top": rows[:limit],
            "worst": sorted([d for d in rows if d["negative"] >= 2], key=lambda d: (-d["neg_rate"], -d["negative"]))[:limit],
            "complaints": complaints}


def segments(conn, f: Filters) -> dict:
    w, p = f.where()

    def by(expr: str, label: str) -> list[dict]:
        rows = _rows(conn, f"""
            SELECT {expr} AS k, count(*) AS reviews, sum({NEG_EXPR}) AS negatives, avg(r.star) AS avg_star
            {BASE_JOIN} WHERE {w} GROUP BY k ORDER BY reviews DESC""", p)
        for r in rows:
            r["neg_rate"] = r["negatives"] / r["reviews"] if r["reviews"] else None
            r["segment"] = label
        return rows

    return {
        "daypart": by("coalesce(a.daypart, '未分析')", "时段"),
        "scenario": by("coalesce(a.scenario, '未分析')", "场景"),
        "weekday": by("CASE strftime('%w', r.review_date) WHEN '0' THEN '周日' WHEN '1' THEN '周一' WHEN '2' THEN '周二' "
                      "WHEN '3' THEN '周三' WHEN '4' THEN '周四' WHEN '5' THEN '周五' ELSE '周六' END", "星期"),
        "hour": by("CAST(strftime('%H', r.review_time) AS INTEGER)", "发布小时"),
        "customer": by("CASE WHEN r.high_v IS NOT NULL THEN '高V用户' WHEN r.customer_level >= 5 THEN 'Lv5+' "
                       "WHEN r.customer_level >= 1 THEN 'Lv1-4' ELSE '匿名/Lv0' END", "顾客等级"),
        "repeat": by("CASE WHEN r.review_count > 1 THEN '多次评价' ELSE '首次评价' END", "新老客"),
        "entry": by("coalesce(r.entry_type, '未知')", "评价入口"),
        "revisit": by("coalesce(a.revisit, 'unknown')", "复购意愿"),
    }


def review_list(conn, f: Filters, sentiment: str | None = None, aspect: str | None = None, dish: str | None = None,
                risk: str | None = None, q: str | None = None, page: int = 1, size: int = 30) -> dict:
    w, p = f.where()
    extra, ep = [], []
    if sentiment == "negative":
        extra.append(f"{NEG_EXPR} = 1")
    elif sentiment:
        extra.append("a.sentiment = ?"); ep.append(sentiment)
    if aspect:
        extra.append("EXISTS (SELECT 1 FROM review_aspects x WHERE x.review_id = r.id AND x.aspect = ? AND x.sentiment='negative')"); ep.append(aspect)
    if dish:
        extra.append("EXISTS (SELECT 1 FROM review_aspects x WHERE x.review_id = r.id AND x.dish = ?)"); ep.append(dish)
    if risk:
        extra.append("a.risk = ?"); ep.append(risk)
    if q:
        extra.append("r.content LIKE ?"); ep.append(f"%{q}%")
    where = " AND ".join([w] + extra)
    total = conn.execute(f"SELECT count(*) {BASE_JOIN} WHERE {where}", p + ep).fetchone()[0]
    rows = _rows(conn, f"""
        SELECT r.id, r.review_time, r.platform, r.star, r.rating, r.content, r.nickname, r.high_v, r.package_name,
               r.has_reply, s.name AS store, a.sentiment, a.score, a.summary, a.reply_suggestion, a.risk,
               a.scenario, a.daypart, a.revisit
        {BASE_JOIN} LEFT JOIN stores s ON s.id = r.store_id WHERE {where}
        ORDER BY r.review_time DESC LIMIT ? OFFSET ?""", p + ep + [size, (page - 1) * size])
    ids = [r["id"] for r in rows]
    if ids:
        asp = _rows(conn, f"SELECT review_id, aspect, sentiment, quote, dish FROM review_aspects WHERE review_id IN ({','.join('?' * len(ids))})", ids)
        by_id: dict[str, list] = {}
        for a in asp:
            by_id.setdefault(a["review_id"], []).append(a)
        for r in rows:
            r["aspects"] = by_id.get(r["id"], [])
    return {"total": total, "page": page, "size": size, "items": rows}


def business(conn, f: Filters) -> list[dict]:
    """Daily traffic/sales next to review counts, per date (all filtered stores summed)."""
    w, p = f.where()
    clauses = ["m.date BETWEEN ? AND ?"]
    mp: list = [f.date_from, f.date_to]
    if f.store_ids:
        clauses.append(f"m.store_id IN ({','.join('?' * len(f.store_ids))})"); mp += f.store_ids
    metrics = {r["date"]: r for r in _rows(conn, f"""
        SELECT m.date, sum(m.exposure) AS exposure, sum(m.visits) AS visits, sum(m.buyers) AS buyers,
               sum(m.orders) AS orders, sum(m.revenue) AS revenue, sum(m.favorites) AS favorites, sum(m.checkins) AS checkins
        FROM daily_metrics m WHERE {' AND '.join(clauses)} GROUP BY m.date""", mp)}
    reviews = {r["d"]: r for r in trend(conn, f, "day")}
    days = sorted(set(metrics) | set(reviews))
    out = []
    for d in days:
        m, r = metrics.get(d, {}), reviews.get(d, {})
        out.append({"date": d, "exposure": m.get("exposure"), "visits": m.get("visits"), "buyers": m.get("buyers"),
                    "orders": m.get("orders"), "revenue": m.get("revenue"), "favorites": m.get("favorites"),
                    "checkins": m.get("checkins"), "reviews": r.get("reviews", 0), "negatives": r.get("negatives", 0),
                    "avg_star": r.get("avg_star")})
    return out


def alerts(conn, f: Filters) -> dict:
    """Stores whose negative count in the last 7 days of the window exceeds their 4-week average."""
    d1 = date.fromisoformat(f.date_to)
    recent = Filters((d1 - timedelta(days=6)).isoformat(), f.date_to, f.store_ids, f.platform)
    base = Filters((d1 - timedelta(days=34)).isoformat(), (d1 - timedelta(days=7)).isoformat(), f.store_ids, f.platform)
    rw, rp = recent.where(); bw, bp = base.where()
    rec = {r["id"]: r for r in _rows(conn, f"""SELECT s.id, s.name, count(*) AS reviews, sum({NEG_EXPR}) AS negatives
        {BASE_JOIN} JOIN stores s ON s.id = r.store_id WHERE {rw} GROUP BY s.id""", rp)}
    bas = {r["id"]: r for r in _rows(conn, f"""SELECT s.id, sum({NEG_EXPR}) AS negatives
        {BASE_JOIN} JOIN stores s ON s.id = r.store_id WHERE {bw} GROUP BY s.id""", bp)}
    spikes = []
    for sid, r in rec.items():
        weekly_base = (bas.get(sid, {}).get("negatives") or 0) / 4
        if r["negatives"] >= 3 and r["negatives"] > weekly_base * 1.5:
            spikes.append({"store": r["name"], "negatives_7d": r["negatives"], "weekly_baseline": round(weekly_base, 1),
                           "reviews_7d": r["reviews"]})
    high = review_list(conn, recent, risk="high", size=20)["items"]
    return {"window": {"from": recent.date_from, "to": recent.date_to}, "spikes": spikes, "high_risk": high}
