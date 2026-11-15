"""SQLite persistence (stdlib only). JSON columns keep the schema flexible during the hackathon."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, scenario TEXT, level TEXT, category TEXT,
  message TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS investigations (
  id TEXT PRIMARY KEY, scenario TEXT, event_id TEXT, status TEXT, mode TEXT, provider TEXT,
  created REAL, updated REAL, event TEXT, report TEXT, recommendation TEXT, decision TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, investigation_id TEXT, seq INTEGER, kind TEXT, name TEXT,
  args TEXT, result TEXT, status TEXT, duration_ms INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS maneuvers (
  id TEXT, investigation_id TEXT, event_id TEXT, result TEXT, ts REAL,
  PRIMARY KEY (id, investigation_id));
"""

_JSON_COLS = {"data", "event", "report", "recommendation", "decision", "args", "result"}


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in _JSON_COLS & d.keys():
                if d[k] is not None:
                    d[k] = json.loads(d[k])
            out.append(d)
        return out

    @staticmethod
    def _j(v: Any) -> Optional[str]:
        return None if v is None else json.dumps(v, ensure_ascii=False, default=str)

    # ---- log ----
    def log(self, scenario: str, level: str, category: str, message: str, data: Any = None) -> None:
        self._exec("INSERT INTO log (ts, scenario, level, category, message, data) VALUES (?,?,?,?,?,?)",
                   (time.time(), scenario, level, category, message, self._j(data)))

    def logs(self, scenario: Optional[str] = None, limit: int = 200) -> list[dict]:
        if scenario:
            return self._rows("SELECT * FROM log WHERE scenario=? ORDER BY id DESC LIMIT ?", (scenario, limit))
        return self._rows("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,))

    # ---- investigations ----
    def create_investigation(self, inv_id: str, scenario: str, event: dict, mode: str) -> None:
        now = time.time()
        self._exec("INSERT INTO investigations (id, scenario, event_id, status, mode, created, updated, event) "
                   "VALUES (?,?,?,?,?,?,?,?)", (inv_id, scenario, event["id"], "running", mode, now, now, self._j(event)))

    def update_investigation(self, inv_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = tuple(self._j(v) if k in _JSON_COLS else v for k, v in fields.items())
        self._exec(f"UPDATE investigations SET {cols}, updated=? WHERE id=?", vals + (time.time(), inv_id))

    def investigation(self, inv_id: str) -> Optional[dict]:
        rows = self._rows("SELECT * FROM investigations WHERE id=?", (inv_id,))
        return rows[0] if rows else None

    def investigations(self, scenario: Optional[str] = None) -> list[dict]:
        sql = "SELECT id, scenario, event_id, status, mode, provider, created, updated FROM investigations"
        if scenario:
            return self._rows(sql + " WHERE scenario=? ORDER BY created DESC", (scenario,))
        return self._rows(sql + " ORDER BY created DESC")

    # ---- steps ----
    def add_step(self, inv_id: str, kind: str, name: str, args: Any = None, result: Any = None,
                 status: str = "ok", duration_ms: int = 0) -> int:
        with self._lock:
            seq = self._conn.execute("SELECT COUNT(*) FROM steps WHERE investigation_id=?", (inv_id,)).fetchone()[0] + 1
        self._exec("INSERT INTO steps (investigation_id, seq, kind, name, args, result, status, duration_ms, ts) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (inv_id, seq, kind, name, self._j(args), self._j(result), status, duration_ms, time.time()))
        return seq

    def steps(self, inv_id: str) -> list[dict]:
        return self._rows("SELECT * FROM steps WHERE investigation_id=? ORDER BY seq", (inv_id,))

    # ---- maneuvers ----
    def save_maneuver(self, result: dict, inv_id: Optional[str]) -> None:
        self._exec("INSERT OR REPLACE INTO maneuvers (id, investigation_id, event_id, result, ts) VALUES (?,?,?,?,?)",
                   (result["maneuver_id"], inv_id or "", result["event_id"], self._j(result), time.time()))

    def maneuvers(self, inv_id: Optional[str] = None, event_id: Optional[str] = None) -> list[dict]:
        if inv_id is not None:
            rows = self._rows("SELECT result FROM maneuvers WHERE investigation_id=? ORDER BY ts", (inv_id,))
        else:
            rows = self._rows("SELECT result FROM maneuvers WHERE event_id=? ORDER BY ts", (event_id,))
        return [r["result"] for r in rows]


def default_db() -> DB:
    return DB(os.getenv("ORBITALGUARD_DB", str(Path(__file__).resolve().parents[1] / "data" / "orbitalguard.sqlite")))
