"""The metadata-store seam (PRD §13.4). Plain SQL; SQLite specifics stop at this module."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..config import SETTINGS, ensure_dirs

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: How long a writer waits for a competing write before giving up.
BUSY_TIMEOUT_MS = 10_000


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(db_path or SETTINGS.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # More than one worker may drain the job table at once. Without this, a concurrent
    # writer fails immediately with "database is locked" instead of waiting its turn.
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def migrate(db_path: Path | None = None) -> None:
    """Apply the schema. DDL only, and never inside a `session()` — `executescript`
    commits any open transaction, which would silently split a caller's write."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()


@contextmanager
def session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        # IMMEDIATE, not deferred. A deferred transaction that reads first and writes
        # later gets SQLITE_BUSY *without* the busy handler running — SQLite refuses to
        # block on a lock upgrade because that is how deadlocks happen. Taking the write
        # lock up front means `busy_timeout` applies and concurrent writers queue.
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def read_only(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def is_empty(conn: sqlite3.Connection) -> bool:
    """True when no case exists — the signal for `make dev` to seed fixtures (§13.2)."""
    row = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()
    return row["n"] == 0


def reset(conn: sqlite3.Connection) -> None:
    """Restore known state. Backs `POST /dev/reset` (§13.2)."""
    tables = [
        "audit",
        "corrections",
        "row_locators",
        "row_bullets",
        "rows_out",
        "jobs",
        "runs",
        "records",
        "unit_row_dates",
        "unit_dates",
        "units",
        "blocks",
        "pages",
        "bundles",
        "cases",
    ]
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("PRAGMA foreign_keys = ON")
