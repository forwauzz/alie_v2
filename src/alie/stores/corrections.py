"""Human corrections (PRD §10.2).

When Amélie says "this date is 2004-03-02" or "this isn't a duplicate," she is stating a
fact about the *document*. It is stored here as `status: manual` against the manifest and
the chronology regenerates. If she edited the output directly, the next re-run would
discard her work and break provenance.

Corrections are training data. Each is a labelled example with a source span attached —
the thing you would otherwise pay to create. As a UI event log the value is unrecoverable.
"""

from __future__ import annotations

import sqlite3

from ..config import SETTINGS
from . import audit
from .db import new_id, now


def apply(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    subject_type: str,
    subject_id: str,
    field: str,
    new_value: str | None,
    old_value: str | None = None,
    actor: str | None = None,
    rule: str | None = None,
    block_id: str | None = None,
    span: tuple[int, int] | None = None,
    run_id: str | None = None,
) -> str:
    correction_id = new_id("cor")
    conn.execute(
        """INSERT INTO corrections (id, case_id, subject_type, subject_id, field, old_value,
                                    new_value, actor, rule, block_id, span_start, span_end,
                                    created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            correction_id,
            case_id,
            subject_type,
            subject_id,
            field,
            old_value,
            new_value,
            actor or SETTINGS.actor,
            rule,
            block_id,
            span[0] if span else None,
            span[1] if span else None,
            now(),
        ),
    )
    audit.record(
        conn,
        subject_type=subject_type,
        subject_id=subject_id,
        action="correction",
        run_id=run_id,
        actor=actor,
        rule=rule,
        detail={"field": field, "old": old_value, "new": new_value},
    )
    return correction_id


def for_subject(conn: sqlite3.Connection, subject_type: str, subject_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM corrections WHERE subject_type = ? AND subject_id = ?
               ORDER BY created_at, id""",
            (subject_type, subject_id),
        )
    ]


def latest_by_field(
    conn: sqlite3.Connection, subject_type: str, subject_id: str
) -> dict[str, dict]:
    """Corrections win over anything the pipeline computes. Later corrections win over
    earlier ones on the same field."""
    out: dict[str, dict] = {}
    for c in for_subject(conn, subject_type, subject_id):
        out[c["field"]] = c
    return out


def for_case(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM corrections WHERE case_id = ? ORDER BY created_at, id", (case_id,)
        )
    ]
