"""SQLite mission store (stdlib only). Keeps missions, states, and results.

One row per mission: indexed id/status/updated_at plus the full mission
JSON. ``db_path`` overrides the settings URL so tests can use tmp files.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
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
