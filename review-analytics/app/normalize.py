"""Normalisation helpers: store names and dish names.

The source tool spells the same store several ways
(``pLeace必乐时（印力中心店）``, ``pLeace(印力中心店)``, ``wow pLeace | 休闲西餐（杭州恒隆店）``).
We key a store by the branch name inside the last pair of brackets and keep every
spelling we have seen in ``store_alias`` pointing at one ``stores`` row.
"""
from __future__ import annotations

import re
import sqlite3

# Seed list: branch key -> (display name, brand, city). Several keys may share one display name.
KNOWN_STORES = {
    "深业上城":      ("pLeace 深业上城店",          "pLeace",           "深圳"),
    "印力中心":      ("pLeace 印力中心店",          "pLeace",           "深圳"),
    "罗湖万象城":    ("pLeace 罗湖万象城店",        "pLeace",           "深圳"),
    "深圳万象城":    ("pLeace 罗湖万象城店",        "pLeace",           "深圳"),
    "深圳湾":        ("pLeace 深圳湾店",            "pLeace",           "深圳"),
    "龙岗万科广场":  ("pLeace 龙岗万科店",          "pLeace",           "深圳"),
    "龙岗万科":      ("pLeace 龙岗万科店",          "pLeace",           "深圳"),
    "海上世界":      ("pLeace island 海上世界店",   "pLeace island",    "深圳"),
    "创意文化园":    ("pLeace LOFT 创意文化园店",   "pLeace LOFT",      "深圳"),
    "福田cocopark":  ("P3 by pLeace 福田cocopark店", "P3 by pLeace",    "深圳"),
    "福田卓悦中心":  ("pLeace2 福田卓悦中心店",     "pLeace2",          "深圳"),
    "天环广场":      ("pLeace 天环广场店",          "pLeace",           "广州"),
    "万菱汇":        ("pLeace2 万菱汇店",           "pLeace2",          "广州"),
    "杭州恒隆":      ("wow pLeace 杭州恒隆店",      "wow pLeace",       "杭州"),
    "南京德基广场":  ("wow pLeace 南京德基广场店",  "wow pLeace",       "南京"),
    "南京德基":      ("wow pLeace 南京德基广场店",  "wow pLeace",       "南京"),
}

_BRACKET_RE = re.compile(r"[（(]([^（）()]+)[）)]\s*$")


def store_key(raw: str | None) -> str | None:
    """``pLeace必乐时（印力中心店）`` -> ``印力中心``; a name without a branch returns None."""
    if not raw:
        return None
    m = _BRACKET_RE.search(raw.strip())
    if not m:
        return None
    branch = m.group(1).strip().lower().replace(" ", "")
    branch = re.sub(r"店$", "", branch)
    return branch or None


def resolve_store(conn: sqlite3.Connection, raw: str | None, create: bool = True) -> int | None:
    """Return the store id for a raw platform name, creating the store when ``create`` is set."""
    key = store_key(raw)
    if not key:
        return None
    row = conn.execute("SELECT store_id FROM store_alias WHERE key=?", (key,)).fetchone()
    if row:
        return row["store_id"]
    if key in KNOWN_STORES:
        name, brand, city = KNOWN_STORES[key]
    elif create:
        name, brand, city = raw.strip(), None, None
    else:
        return None
    store = conn.execute("SELECT id FROM stores WHERE name=?", (name,)).fetchone()
    store_id = store["id"] if store else conn.execute(
        "INSERT INTO stores(name, brand, city) VALUES (?,?,?)", (name, brand, city)).lastrowid
    conn.execute("INSERT INTO store_alias(key, store_id) VALUES (?,?)", (key, store_id))
    return store_id


_DISH_STRIP = re.compile(r"[「」『』【】\[\]《》“”\"'‘’!！。，,.、~～·•\s]")
_DISH_PREFIX = re.compile(r"^(招牌|人气|必点|推荐|新品|经典|明星|爆款|超模|超级|特色)")


def dish_key(raw: str | None) -> str | None:
    """Cheap canonical form for a dish name; the dish_alias table refines it."""
    if not raw:
        return None
    s = _DISH_STRIP.sub("", raw.strip()).lower()
    s = _DISH_PREFIX.sub("", s)
    return s or None


def resolve_dish(conn: sqlite3.Connection, raw: str | None) -> str | None:
    key = dish_key(raw)
    if not key:
        return None
    row = conn.execute("SELECT dish FROM dish_alias WHERE raw=?", (key,)).fetchone()
    return row["dish"] if row else key
