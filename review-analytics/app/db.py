"""SQLite storage for the review analytics tool.

One database file holds everything: the raw reviews imported from the
existing 顾客说 tool, the per-review analysis produced by Claude, the
aspect-level findings, and the per-store daily business metrics.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("REVIEW_DB", Path(__file__).resolve().parent.parent / "data" / "reviews.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id            INTEGER PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,     -- canonical display name
    brand         TEXT,
    city          TEXT
);

CREATE TABLE IF NOT EXISTS store_alias (
    key           TEXT PRIMARY KEY,         -- normalised branch key, e.g. 印力中心
    store_id      INTEGER NOT NULL REFERENCES stores(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id               TEXT PRIMARY KEY,      -- row id in the source tool
    source           TEXT NOT NULL,         -- portal | xlsx
    platform         TEXT,                  -- 大众点评 / 美团 / 扫码反馈 / 值班桌访
    store_raw        TEXT,
    store_id         INTEGER REFERENCES stores(id),
    review_time      TEXT,                  -- ISO datetime
    review_date      TEXT,                  -- YYYY-MM-DD
    rating           REAL,                  -- 打分 0-5
    star             INTEGER,               -- 星级 1-5
    content          TEXT,
    photo_count      INTEGER DEFAULT 0,
    customer_id      TEXT,
    nickname         TEXT,
    customer_level   INTEGER,
    high_v           TEXT,                  -- v6评价 / v7评价 / v8评价
    review_count     INTEGER,               -- 该顾客评价次数（来源工具字段）
    entry_type       TEXT,                  -- 评价入口
    package_name     TEXT,
    package_price    REAL,
    verify_date      TEXT,
    has_reply        INTEGER,
    reply_text       TEXT,
    legacy_emotion   TEXT,                  -- 顾客说 AI 情绪
    legacy_reasons   TEXT,                  -- 顾客说 AI 情绪原因
    legacy_dishes    TEXT,                  -- 顾客说 AI 菜品名称
    created_at       TEXT,                  -- 抓取入库时间
    imported_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_reviews_date  ON reviews(review_date);
CREATE INDEX IF NOT EXISTS ix_reviews_store ON reviews(store_id, review_date);

CREATE TABLE IF NOT EXISTS review_analysis (
    review_id        TEXT PRIMARY KEY REFERENCES reviews(id),
    model            TEXT,
    analyzed_at      TEXT DEFAULT (datetime('now')),
    sentiment        TEXT,                  -- positive | neutral | negative
    score            INTEGER,               -- -2 .. 2
    revisit          TEXT,                  -- yes | maybe | no | unknown
    scenario         TEXT,                  -- 聚会 | 约会 | 亲子 | 独自 | 商务 | 未提及
    daypart          TEXT,                  -- 午市 | 下午茶 | 晚市 | 未提及
    summary          TEXT,
    reply_suggestion TEXT,
    risk             TEXT,                  -- none | watch | high  (食安、投诉升级等)
    raw_json         TEXT
);

CREATE TABLE IF NOT EXISTS review_aspects (
    id          INTEGER PRIMARY KEY,
    review_id   TEXT NOT NULL REFERENCES reviews(id),
    category    TEXT NOT NULL,              -- 菜品/服务/环境/价格/等待/卫生/整体
    aspect      TEXT NOT NULL,              -- 细项，固定枚举
    sentiment   TEXT NOT NULL,              -- positive | negative | neutral
    quote       TEXT,
    dish_raw    TEXT,
    dish        TEXT                        -- 归一后的菜名
);
CREATE INDEX IF NOT EXISTS ix_aspects_review ON review_aspects(review_id);
CREATE INDEX IF NOT EXISTS ix_aspects_dish   ON review_aspects(dish);

CREATE TABLE IF NOT EXISTS dish_alias (
    raw   TEXT PRIMARY KEY,
    dish  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    store_id     INTEGER NOT NULL REFERENCES stores(id),
    date         TEXT NOT NULL,
    exposure     INTEGER,
    visits       INTEGER,
    buyers       INTEGER,
    orders       INTEGER,
    revenue      REAL,
    favorites    INTEGER,
    checkins     INTEGER,
    dp_rating    REAL,
    mt_rating    REAL,
    PRIMARY KEY (store_id, date)
);

CREATE TABLE IF NOT EXISTS review_replies (
    review_id     TEXT PRIMARY KEY REFERENCES reviews(id),
    model         TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    reply         TEXT,                  -- 建议回复（店长口吻，含知识库口径）
    internal_note TEXT,                  -- 给门店/品控的内部跟进建议
    owner         TEXT,                  -- 店长 | 品控 | 区域经理 | 无需跟进
    escalate      INTEGER,               -- 1 = 必须人工处理
    raw_json      TEXT
);

CREATE TABLE IF NOT EXISTS action_items (
    id             INTEGER PRIMARY KEY,
    store_id       INTEGER NOT NULL REFERENCES stores(id),
    period_from    TEXT NOT NULL,
    period_to      TEXT NOT NULL,
    created_at     TEXT DEFAULT (datetime('now')),
    model          TEXT,
    title          TEXT NOT NULL,          -- 问题，一句话
    aspect         TEXT,
    owner          TEXT,                   -- 店长 | 品控 | 区域经理
    priority       TEXT,                   -- 高 | 中 | 低
    escalate       INTEGER DEFAULT 0,      -- 1 = 食安/虫害/卫生，需区域经理知悉
    action         TEXT,                   -- 建议动作
    evidence_count INTEGER,
    evidence_json  TEXT,                   -- [{review_id, quote, dish, star, review_date}]
    status         TEXT DEFAULT 'open',    -- open | done
    done_at        TEXT,
    category       TEXT                    -- 固定分类，见 app/themes.py
);
CREATE INDEX IF NOT EXISTS ix_actions_store ON action_items(store_id, period_to);

CREATE TABLE IF NOT EXISTS themes (
    id              INTEGER PRIMARY KEY,
    period_from     TEXT NOT NULL,
    period_to       TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    model           TEXT,
    title           TEXT NOT NULL,          -- 跨门店共同问题
    category        TEXT,
    store_count     INTEGER,
    evidence_total  INTEGER,
    stores_json     TEXT,
    item_ids_json   TEXT,
    what_is_common  TEXT,
    company_action  TEXT
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    batch_id    TEXT PRIMARY KEY,
    model       TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    status      TEXT,
    n_requests  INTEGER
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI runs sync endpoints and dependency teardown on different threads
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _add_columns(conn, "action_items", {
        "review_count": "INTEGER",   # distinct reviews behind the item (evidence_count counts quotes)
        "kind": "TEXT",              # 整改 = store can act on it; 知晓 = too vague to act on, awareness only
        "kind_reason": "TEXT",
        "title_orig": "TEXT",        # wording before the actionability review rewrote it
        "action_orig": "TEXT",
    })
    return conn


def _add_columns(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, typ in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
