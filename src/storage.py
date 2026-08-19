"""Run history storage — a lightweight SQLite store for completed tasks.

Backs the History nav page (previously a decorative placeholder). Kept
deliberately simple:
SQLite via the stdlib (no new dependency), one table, no migrations
framework — proportionate to what a portfolio-project history view
needs, not a production analytics pipeline.

Only real tasks are recorded — chit-chat (a ChatMessage reply, see
planner.check_intent) never reaches here, since "history of things I
asked TaskPilot to do" shouldn't include "said hi". Called from
main.py's POST /run, not from src/agent/graph.py — keeps the agent core
free of side effects so tests (which run real goals through fakes
constantly) never touch a database.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_settings
from src.models import TaskResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    success INTEGER NOT NULL,
    summary TEXT NOT NULL,
    steps_completed INTEGER NOT NULL,
    steps_total INTEGER NOT NULL,
    steps_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _db_path() -> Path:
    path = Path(get_settings().history_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _connection():
    conn = sqlite3.connect(_db_path())
    try:
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_run(result: TaskResult, step_summaries: Iterable[dict]) -> None:
    """Persist a completed run.

    `step_summaries` is a plain list of {tool, status, message} dicts
    (built by main.py from the StepUpdate events it already streamed) --
    not a richer model, since a history list only needs a readable
    summary of what happened, not enough to re-drive execution.
    """
    steps_json = json.dumps(list(step_summaries))
    with _connection() as conn:
        conn.execute(
            "INSERT INTO runs (goal, success, summary, steps_completed, steps_total, steps_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                result.goal,
                int(result.success),
                result.summary,
                result.steps_completed,
                result.steps_total,
                steps_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def list_runs(limit: int = 50) -> list[dict]:
    """Most recent runs first."""
    with _connection() as conn:
        rows = conn.execute(
            "SELECT id, goal, success, summary, steps_completed, steps_total, steps_json, created_at "
            "FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "goal": row[1],
            "success": bool(row[2]),
            "summary": row[3],
            "steps_completed": row[4],
            "steps_total": row[5],
            "steps": json.loads(row[6]),
            "created_at": row[7],
        }
        for row in rows
    ]
