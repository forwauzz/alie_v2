"""Job runner (PRD §13.4).

Jobs never run inside an HTTP request — even locally they go through the job table. The
background thread here is the local implementation of the job-runner seam; swapping it for
a queue is a deployment detail.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import pipeline
from .stores import db, runs

log = logging.getLogger("alie.worker")

#: How long to wait between polls when the queue is empty.
IDLE_SLEEP = 0.05


def drain(db_path: Path | None = None, *, max_jobs: int = 10_000) -> int:
    """Run every queued job to completion. Used by tests and by `alie run`, which need a
    deterministic finish rather than a background thread."""
    done = 0
    while done < max_jobs:
        if not _step(db_path):
            return done
        done += 1
    return done


def _step(db_path: Path | None = None) -> bool:
    """Claim and execute one job. Returns False when the queue is empty."""
    with db.session(db_path) as conn:
        job = runs.claim_next(conn)
    if job is None:
        return False

    run_id = job["run_id"]
    with db.read_only(db_path) as conn:
        run = runs.get_run(conn, run_id)

    # A newer run for this case took over while this job sat in the queue. Running it
    # would rebuild the manifest under the abandoned run's flag set.
    if run and run["status"] == runs.SUPERSEDED:
        with db.session(db_path) as conn:
            runs.finish_job(conn, job["id"], error=None, status=runs.SUPERSEDED)
        return True

    flags = run["flags"] if run else {}

    try:
        with db.session(db_path) as conn:
            detail = pipeline.handle(conn, job, flags)
        error = None
    except Exception as exc:  # a failed stage must not take the worker down
        log.exception("job %s (%s) failed", job["id"], job["stage"])
        detail, error = {}, f"{type(exc).__name__}: {exc}"

    with db.session(db_path) as conn:
        runs.finish_job(conn, job["id"], error=error)
        case_id = job["payload"].get("case_id")
        # Intake stops at the manifest: the plan exists so a scoping error can be caught
        # before cost is approved (§4.1). Assembling a chronology is what approval buys.
        if case_id and not error and not pipeline.is_intake(conn, run_id):
            pipeline.advance(conn, run_id, case_id)
        # A job already in flight when the case was handed to a newer run still finishes,
        # but must not resurrect the run it belonged to.
        current = runs.get_run(conn, run_id)
        if current and current["status"] == runs.SUPERSEDED:
            pass
        elif error:
            runs.set_run_status(conn, run_id, runs.FAILED)
        elif pipeline.is_finished(conn, run_id):
            runs.set_run_status(conn, run_id, runs.DONE)
        else:
            runs.set_run_status(conn, run_id, runs.RUNNING)

    log.info("job %s %s -> %s", job["id"], job["stage"], error or detail)
    return True


class BackgroundWorker:
    """Drains the queue in a thread so the user may close the tab (§4.1)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="alie-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not _step(self._db_path):
                    time.sleep(IDLE_SLEEP)
            except Exception:
                log.exception("worker loop error")
                time.sleep(IDLE_SLEEP)
