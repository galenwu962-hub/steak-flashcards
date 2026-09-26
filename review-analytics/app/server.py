"""FastAPI app: JSON endpoints for the dashboard plus the static front end.

    uvicorn app.server:app --reload --port 8000
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .db import connect

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="Review Analytics")


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_filters(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    stores: str | None = None,
    platform: str | None = None,
) -> queries.Filters:
    d1 = date_to or date.today().isoformat()
    d0 = date_from or (date.fromisoformat(d1) - timedelta(days=29)).isoformat()
    ids = [int(s) for s in stores.split(",") if s.strip().isdigit()] if stores else []
    return queries.Filters(d0, d1, ids, platform or None)


@app.get("/api/meta")
def api_meta(conn=Depends(get_conn)):
    return queries.meta(conn)


@app.get("/api/overview")
def api_overview(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.overview(conn, f)


@app.get("/api/trend")
def api_trend(granularity: str = "day", f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.trend(conn, f, "week" if granularity == "week" else "day")


@app.get("/api/stores")
def api_stores(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.stores(conn, f)


@app.get("/api/aspects")
def api_aspects(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.aspects(conn, f)


@app.get("/api/dishes")
def api_dishes(limit: int = 30, f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.dishes(conn, f, limit)


@app.get("/api/segments")
def api_segments(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.segments(conn, f)


@app.get("/api/reviews")
def api_reviews(sentiment: str | None = None, aspect: str | None = None, dish: str | None = None,
                risk: str | None = None, q: str | None = None, page: int = 1, size: int = 30,
                f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.review_list(conn, f, sentiment, aspect, dish, risk, q, page, min(size, 100))


@app.get("/api/business")
def api_business(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.business(conn, f)


@app.get("/api/alerts")
def api_alerts(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.alerts(conn, f)


@app.get("/api/actions")
def api_actions(f: queries.Filters = Depends(get_filters), conn=Depends(get_conn)):
    return queries.actions(conn, f)


@app.post("/api/actions/{item_id}/status")
def api_action_status(item_id: int, status: str, conn=Depends(get_conn)):
    if status not in ("open", "done"):
        return {"ok": False}
    with conn:
        conn.execute("UPDATE action_items SET status=?, done_at=CASE WHEN ?='done' THEN datetime('now') END WHERE id=?",
                     (status, status, item_id))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
