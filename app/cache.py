"""极简 SQLite 缓存：网页正文 + 每日计数。"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    ts  REAL,
    ok  INTEGER,
    via TEXT,
    title TEXT,
    text TEXT,
    lang TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.CACHE_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def get_page(url: str, max_age: float = 7 * 86400) -> dict | None:
    try:
        with _conn() as c:
            row = c.execute("SELECT ts, ok, via, title, text FROM pages WHERE url=?", (url,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or time.time() - row[0] > max_age:
        return None
    return {"ok": bool(row[1]), "via": row[2], "title": row[3], "text": row[4], "cached": True}


def put_page(url: str, ok: bool, via: str, title: str, text: str) -> None:
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO pages(url, ts, ok, via, title, text) VALUES (?,?,?,?,?,?)",
                      (url, time.time(), int(ok), via, title, text))
    except sqlite3.Error:
        pass


def kv_get(key: str, default=None):
    try:
        with _conn() as c:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    if not row:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return default


def kv_set(key: str, value) -> None:
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
    except sqlite3.Error:
        pass


def today_key(prefix: str) -> str:
    return f"{prefix}:{date.today().isoformat()}"


def bump_counter(prefix: str) -> int:
    key = today_key(prefix)
    n = int(kv_get(key, 0)) + 1
    kv_set(key, n)
    return n


def counter(prefix: str) -> int:
    return int(kv_get(today_key(prefix), 0))
