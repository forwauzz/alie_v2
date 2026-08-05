"""Product metrics (PRD §11.5).

MLflow measures extraction. It does not measure whether the firm keeps using the thing.

The one that matters most is **flag precision** — the percentage of flagged items that
were genuinely wrong. A review queue that cries wolf gets ignored, and an ignored queue
silently disables the main safety mechanism. That failure is invisible to every extraction
metric in §11.3: a system can score perfectly on groundedness while flagging so much that
nobody reads the flags.

These are computed from what the app already records, because the app is the source of
truth (§11.1):

    flagged item    a row the engine marked for review — undated, ambiguous, illegible,
                    unclassified, low confidence
    genuinely wrong the paralegal corrected it
    cried wolf      she approved the run and never touched it

The inference is deliberately coarse and says so. "She did not correct it" is evidence the
flag was unnecessary, not proof — she may have missed it. So this reports a *rate with its
denominator*, never a verdict, and a case nobody has reviewed yet is excluded rather than
counted as agreement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..models import Row, RowStatus
from ..stores import audit, corrections, runs
from ..stores import rows as rows_store


@dataclass(frozen=True)
class FlagPrecision:
    """How much of the review queue was worth reading."""

    flagged: int
    corrected: int
    #: Rows corrected that carried no flag. The queue missed these, which is the more
    #: dangerous direction — a flag that never fires costs a case, a flag that fires too
    #: often costs patience.
    missed: int

    @property
    def precision(self) -> float | None:
        """None when nothing was flagged: a rate over zero is not 100%, it is unknown."""
        return self.corrected / self.flagged if self.flagged else None

    @property
    def recall(self) -> float | None:
        total_wrong = self.corrected + self.missed
        return self.corrected / total_wrong if total_wrong else None


@dataclass(frozen=True)
class ProductMetrics:
    case_id: str
    rows: int
    #: Rows the paralegal accepted without editing. The headline number for "is this
    #: saving her time".
    accepted_unedited: int
    flag_precision: FlagPrecision
    #: Seconds from run start to a rendered chronology. "Time to first draft" (§11.5).
    seconds_to_draft: float | None
    reviewed: bool

    @property
    def accept_rate(self) -> float | None:
        return self.accepted_unedited / self.rows if self.rows else None

    def summary(self) -> str:
        if not self.reviewed:
            return (
                f"{self.rows} rows, not yet reviewed - "
                "acceptance and flag precision are unknown, not perfect"
            )
        parts = [f"{self.accepted_unedited}/{self.rows} rows accepted unedited"]
        p = self.flag_precision
        if p.precision is not None:
            parts.append(f"flag precision {p.precision:.0%} ({p.corrected}/{p.flagged})")
        if p.missed:
            parts.append(f"{p.missed} correction(s) the queue did not flag")
        if self.seconds_to_draft is not None:
            parts.append(f"first draft in {self.seconds_to_draft:.0f}s")
        return " · ".join(parts)


def _is_flagged(row: Row) -> bool:
    """What the review surface puts in front of her (§10.2)."""
    return (
        row.row_date.status
        in (RowStatus.UNDATED, RowStatus.AMBIGUOUS, RowStatus.ILLEGIBLE, RowStatus.INFERRED)
        or row.warns
    )


def for_run(conn: sqlite3.Connection, run_id: str) -> ProductMetrics:
    run = runs.get_run(conn, run_id)
    if run is None:
        raise KeyError(f"unknown run: {run_id}")

    case_id = run["case_id"]
    rows = rows_store.for_run(conn, run_id)
    fixed_units = {
        c["subject_id"] for c in corrections.for_case(conn, case_id) if c["subject_type"] == "unit"
    }

    flagged = corrected = missed = 0
    accepted = 0
    for row in rows:
        touched = bool(set(row.unit_ids) & fixed_units)
        if _is_flagged(row):
            flagged += 1
            corrected += 1 if touched else 0
        elif touched:
            missed += 1
        if not touched:
            accepted += 1

    # A case nobody has opened tells you nothing about the queue. Counting it as agreement
    # would make flag precision rise every time the tool goes unused.
    reviewed = bool(fixed_units) or _approved(conn, run_id)

    return ProductMetrics(
        case_id=case_id,
        rows=len(rows),
        accepted_unedited=accepted,
        flag_precision=FlagPrecision(flagged=flagged, corrected=corrected, missed=missed),
        seconds_to_draft=_elapsed(run),
        reviewed=reviewed,
    )


def _approved(conn: sqlite3.Connection, run_id: str) -> bool:
    return any(e["action"] == "approve" for e in audit.for_run(conn, run_id))


def _elapsed(run: dict) -> float | None:
    if not run.get("finished_at"):
        return None
    try:
        start = datetime.fromisoformat(run["created_at"])
        end = datetime.fromisoformat(run["finished_at"])
    except (TypeError, ValueError):
        return None
    return (end - start).total_seconds()
