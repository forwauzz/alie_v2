"""Audit log (PRD §4.5): who or what decided each thing, when, under which prompt version
and which rule."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import SETTINGS
from .db import new_id, now


def record(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    action: str,
    run_id: str | None = None,
    actor: str | None = None,
    rule: str | None = None,
    epistemic_tag: str | None = None,
    prompt_version: str | None = None,
    model: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str:
    entry_id = new_id("aud")
    conn.execute(
        """INSERT INTO audit (id, ts, actor, run_id, subject_type, subject_id, action,
                              rule, epistemic_tag, prompt_version, model, detail)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            entry_id,
            now(),
            actor or SETTINGS.actor,
            run_id,
            subject_type,
            subject_id,
            action,
            rule,
            epistemic_tag,
            prompt_version,
            model,
            json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
        ),
    )
    return entry_id


def for_subject(conn: sqlite3.Connection, subject_type: str, subject_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit WHERE subject_type = ? AND subject_id = ? ORDER BY ts, id",
        (subject_type, subject_id),
    ).fetchall()
    return [dict(r) | {"detail": json.loads(r["detail"])} for r in rows]


def for_run(conn: sqlite3.Connection, run_id: str, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit WHERE run_id = ? ORDER BY ts, id LIMIT ?", (run_id, limit)
    ).fetchall()
    return [dict(r) | {"detail": json.loads(r["detail"])} for r in rows]
