"""SQLite mission store (stdlib only). Keeps missions, states, and results.

One row per mission: indexed id/status/updated_at plus the full mission
JSON. ``db_path`` overrides the settings URL so tests can use tmp files.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.models.jobs import JobRecord
from app.models.mission import Mission


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_db_path(url: str | None = None) -> Path:
    raw = url if url is not None else get_settings().database_url
    if raw.startswith("sqlite:///"):
        raw = raw[len("sqlite:///") :]
    return Path(raw)


def init_db(path: Path | None = None) -> Path:
    db = path if path is not None else resolve_db_path()
    if str(db) != ":memory:":
        db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL DEFAULT '',
                heartbeat_at TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL
            )"""
        )
        for column_ddl in (
            "ALTER TABLE jobs ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE jobs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT NOT NULL DEFAULT ''",
        ):
            try:
                conn.execute(column_ddl)
            except sqlite3.OperationalError:
                pass  # column already present (fresh table or migrated DB)
        # At most one live job per mission+operation: the atomic lock
        # against duplicate builds (double-click, retry storms, restarts).
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active
               ON jobs(mission_id, kind)
               WHERE status IN ('queued', 'claimed', 'running')"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_jobs_status
               ON jobs(status, updated_at)"""
        )
        # One job per idempotency key: concurrent duplicate requests
        # collapse atomically instead of double-building.
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
               ON jobs(mission_id, idempotency_key)
               WHERE idempotency_key IS NOT NULL"""
        )
    return db


def save_mission(mission: Mission, path: Path | None = None) -> Mission:
    db = init_db(path)
    mission.updated_at = utc_now_iso()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO missions (id, status, updated_at, data) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "updated_at=excluded.updated_at, data=excluded.data",
            (mission.id, mission.status.value, mission.updated_at, mission.model_dump_json()),
        )
    return mission


def load_mission(mission_id: str, path: Path | None = None) -> Mission | None:
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT data FROM missions WHERE id = ?", (mission_id,)).fetchone()
    if row is None:
        return None
    return Mission.model_validate_json(row[0])


def save_job(job: JobRecord, path: Path | None = None) -> JobRecord:
    db = init_db(path)
    job.updated_at = utc_now_iso()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs (id, mission_id, kind, status, updated_at, "
            "idempotency_key, created_at, heartbeat_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "updated_at=excluded.updated_at, heartbeat_at=excluded.heartbeat_at, "
            "data=excluded.data",
            (job.id, job.mission_id, job.kind.value, job.status.value,
             job.updated_at, job.idempotency_key, job.created_at,
             job.heartbeat_at, job.model_dump_json()),
        )
    return job


def load_job(job_id: str, path: Path | None = None) -> JobRecord | None:
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return JobRecord.model_validate_json(row[0])


def load_active_job(
    mission_id: str, kind: str, path: Path | None = None
) -> JobRecord | None:
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT data FROM jobs WHERE mission_id = ? AND kind = ? "
            "AND status IN ('queued', 'claimed', 'running') "
            "ORDER BY updated_at DESC LIMIT 1",
            (mission_id, kind),
        ).fetchone()
    if row is None:
        return None
    return JobRecord.model_validate_json(row[0])


def list_mission_ids(path: Path | None = None) -> list[str]:
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id FROM missions ORDER BY updated_at").fetchall()
    return [row[0] for row in rows]


def load_job_by_key(
    mission_id: str, idempotency_key: str, path: Path | None = None
) -> JobRecord | None:
    db = init_db(path)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT data FROM jobs WHERE mission_id = ? AND idempotency_key = ?",
            (mission_id, idempotency_key),
        ).fetchone()
    if row is None:
        return None
    return JobRecord.model_validate_json(row[0])
