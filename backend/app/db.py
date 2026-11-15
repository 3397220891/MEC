"""Tiny SQLite persistence layer (stdlib only)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS operators (
  id TEXT PRIMARY KEY, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_sessions (
  token_hash TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(id), expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, scenario_id TEXT, object_a TEXT, object_b TEXT,
  tca_s REAL, tca_utc TEXT, miss_km REAL, rel_speed_km_s REAL, severity TEXT,
  data TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS investigations (
  id TEXT PRIMARY KEY, event_id TEXT, scenario_id TEXT, status TEXT, mode TEXT, model TEXT,
  started_at TEXT, finished_at TEXT, recommendation TEXT, validation TEXT, report TEXT,
  error TEXT, decision TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, investigation_id TEXT, idx INTEGER, kind TEXT,
  tool TEXT, input TEXT, output TEXT, evidence_id TEXT, status TEXT, duration_ms REAL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS maneuvers (
  id TEXT, investigation_id TEXT, data TEXT, created_at TEXT,
  PRIMARY KEY (investigation_id, id)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, detail TEXT
);
"""

JSON_COLS = {"data", "recommendation", "validation", "report", "decision", "input", "output", "detail"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
        return _conn


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid or 0


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [_row(r) for r in conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    with _lock:
        return _row(conn().execute(sql, params).fetchone())


def dumps(x: Any) -> str:
    return json.dumps(x, default=str)


def audit(actor: str, action: str, detail: dict) -> None:
    execute("INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)", (now_iso(), actor, action, dumps(detail)))
