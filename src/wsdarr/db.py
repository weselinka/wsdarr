"""Small SQLite persistence layer (jobs, key/value store, metadata cache)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    nzo_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    ident TEXT NOT NULL,
    ws_name TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    bytes_done INTEGER NOT NULL DEFAULT 0,
    speed REAL NOT NULL DEFAULT 0,
    storage TEXT NOT NULL DEFAULT '',
    fail_message TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    download_time INTEGER NOT NULL DEFAULT 0,
    in_history INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Job:
    nzo_id: str
    name: str
    category: str
    ident: str
    status: str
    priority: int = 0
    ws_name: str = ""
    size: int = 0
    paused: bool = False
    bytes_done: int = 0
    speed: float = 0.0
    storage: str = ""
    fail_message: str = ""
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    download_time: int = 0
    in_history: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["paused"] = bool(data["paused"])
        data["in_history"] = bool(data["in_history"])
        return cls(**data)


JOB_FIELDS = [f.name for f in fields(Job)]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    # --- key/value -----------------------------------------------------------------------
    def kv_get(self, key: str) -> str | None:
        row = self._exec("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def kv_delete(self, key: str) -> None:
        self._exec("DELETE FROM kv WHERE key = ?", (key,))

    def kv_get_or_create(self, key: str, factory) -> str:
        value = self.kv_get(key)
        if value is None:
            value = factory()
            self.kv_set(key, value)
        return value

    # --- cache ---------------------------------------------------------------------------
    def cache_get(self, key: str) -> Any | None:
        row = self._exec("SELECT value, expires FROM cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        if row["expires"] < time.time():
            self._exec("DELETE FROM cache WHERE key = ?", (key,))
            return None
        return json.loads(row["value"])

    def cache_set(self, key: str, value: Any, ttl: float) -> None:
        self._exec(
            "INSERT INTO cache(key, value, expires) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires = excluded.expires",
            (key, json.dumps(value), time.time() + ttl),
        )

    def cache_purge(self) -> None:
        self._exec("DELETE FROM cache WHERE expires < ?", (time.time(),))

    # --- jobs ----------------------------------------------------------------------------
    def job_insert(self, job: Job) -> None:
        data = asdict(job)
        cols = ", ".join(data)
        marks = ", ".join(f":{k}" for k in data)
        self._exec(f"INSERT INTO jobs({cols}) VALUES({marks})", data)

    def job_update(self, nzo_id: str, **changes: Any) -> None:
        if not changes:
            return
        unknown = set(changes) - set(JOB_FIELDS)
        if unknown:
            raise ValueError(f"unknown job fields: {unknown}")
        assignments = ", ".join(f"{k} = :{k}" for k in changes)
        self._exec(f"UPDATE jobs SET {assignments} WHERE nzo_id = :nzo_id", {**changes, "nzo_id": nzo_id})

    def job_get(self, nzo_id: str) -> Job | None:
        row = self._exec("SELECT * FROM jobs WHERE nzo_id = ?", (nzo_id,)).fetchone()
        return Job.from_row(row) if row else None

    def job_delete(self, nzo_id: str) -> None:
        self._exec("DELETE FROM jobs WHERE nzo_id = ?", (nzo_id,))

    def jobs_queue(self) -> list[Job]:
        rows = self._exec(
            "SELECT * FROM jobs WHERE in_history = 0 ORDER BY priority DESC, created_at ASC"
        ).fetchall()
        return [Job.from_row(r) for r in rows]

    def jobs_history(self, limit: int | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE in_history = 1 ORDER BY completed_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [Job.from_row(r) for r in self._exec(sql).fetchall()]
