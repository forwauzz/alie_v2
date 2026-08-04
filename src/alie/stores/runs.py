"""Runs and the job table.

A run is immutable and records its resolved flag set; changing a flag creates a new run
(PRD §9 rule 3). Jobs never run inside an HTTP request — they go through this table even
locally (§13.4).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import new_id, now

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


def create_run(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    flags: dict[str, Any],
    pack_versions: dict[str, str],
    plan: dict[str, Any],
) -> str:
    run_id = new_id("run")
    conn.execute(
        """INSERT INTO runs (id, case_id, status, flags, pack_versions, plan, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            run_id,
            case_id,
            QUEUED,
            json.dumps(flags, sort_keys=True),
            json.dumps(pack_versions, sort_keys=True),
            json.dumps(plan, ensure_ascii=False, sort_keys=True),
            now(),
        ),
    )
    return run_id


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["flags"] = json.loads(d["flags"])
    d["pack_versions"] = json.loads(d["pack_versions"])
    d["plan"] = json.loads(d["plan"])
    return d


def runs_for_case(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    return [
        get_run(conn, r["id"])  # type: ignore[misc]
        for r in conn.execute(
            "SELECT id FROM runs WHERE case_id = ? ORDER BY created_at DESC, id", (case_id,)
        )
    ]


def set_run_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    finished = now() if status in (DONE, FAILED) else None
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?", (status, finished, run_id)
    )


def enqueue(conn: sqlite3.Connection, run_id: str, stage: str, payload: dict[str, Any]) -> str:
    """The job-runner seam: `enqueue(stage, unit_ids)` (§13.4)."""
    job_id = new_id("job")
    conn.execute(
        """INSERT INTO jobs (id, run_id, stage, payload, status, attempts, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (job_id, run_id, stage, json.dumps(payload, sort_keys=True), QUEUED, 0, now()),
    )
    return job_id


def claim_next(conn: sqlite3.Connection) -> dict | None:
    r = conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY created_at, id LIMIT 1", (QUEUED,)
    ).fetchone()
    if not r:
        return None
    conn.execute(
        "UPDATE jobs SET status = ?, attempts = attempts + 1, started_at = ? WHERE id = ?",
        (RUNNING, now(), r["id"]),
    )
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    d["status"] = RUNNING
    return d


def finish_job(conn: sqlite3.Connection, job_id: str, *, error: str | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        (FAILED if error else DONE, now(), error, job_id),
    )


def jobs_for_run(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return [
        dict(r) | {"payload": json.loads(r["payload"])}
        for r in conn.execute(
            "SELECT * FROM jobs WHERE run_id = ? ORDER BY created_at, id", (run_id,)
        )
    ]


def stage_progress(conn: sqlite3.Connection, run_id: str) -> dict[str, dict[str, int]]:
    """Per-stage counts. Backs `GET /dev/state` so QA asserts on facts (§13.2)."""
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        "SELECT stage, status, COUNT(*) AS n FROM jobs WHERE run_id = ? GROUP BY stage, status",
        (run_id,),
    ):
        out.setdefault(r["stage"], {})[r["status"]] = r["n"]
    return out
