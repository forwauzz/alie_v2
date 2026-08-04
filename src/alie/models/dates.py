"""The date model (PRD §8.4).

A page does not have "a date". It has a set of dates, each with a role. The row date is a
selection over that set, governed by document class.

On a REM, `90-05-08` (événement) sits two lines from `92-12-10` (examen). Any pipeline
returning *one* date has already lost the information needed to be right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .status import RowStatus


class DateRole(StrEnum):
    # Eligible to become the row date.
    EXAM = "exam"
    VISIT = "visit"
    SESSION = "session"
    SURGERY = "surgery"
    SIGNATURE = "signature"
    REPORT = "report"

    # Structurally ineligible — never competitors for the row date.
    EVENT = "event"  # `date de l'événement`; feeds the claim-event dimension instead
    RECEIVED = "received"
    FAX = "fax"
    PRINT = "print"
    BIRTH = "birth"
    DEATH = "death"

    UNKNOWN = "unknown"


ELIGIBLE_ROLES: frozenset[DateRole] = frozenset(
    {
        DateRole.EXAM,
        DateRole.VISIT,
        DateRole.SESSION,
        DateRole.SURGERY,
        DateRole.SIGNATURE,
        DateRole.REPORT,
    }
)

INELIGIBLE_ROLES: frozenset[DateRole] = frozenset(
    {
        DateRole.EVENT,
        DateRole.RECEIVED,
        DateRole.FAX,
        DateRole.PRINT,
        DateRole.BIRTH,
        DateRole.DEATH,
    }
)


@dataclass(frozen=True)
class DateFact:
    """One date found on a page, with its role and where it came from.

    `readings` holds every plausible interpretation. `02-03-04` yields more than one and
    the row renders with `(?)`. Ambiguity is a value, not a coin flip.
    """

    role: DateRole
    readings: tuple[date, ...]
    raw: str
    block_id: str
    start: int
    end: int
    pdf_index: int
    confidence: float = 1.0
    century_inferred: bool = False

    @property
    def is_ambiguous(self) -> bool:
        return len(self.readings) > 1

    @property
    def value(self) -> date | None:
        return self.readings[0] if len(self.readings) == 1 else None

    @property
    def eligible(self) -> bool:
        return self.role in ELIGIBLE_ROLES


@dataclass(frozen=True)
class RowDate:
    """The engine's decision, which explains itself in one line.

    Extraction output is overwritten by this. The model is not permitted to choose the
    date (§8.4).
    """

    value: date | None
    status: RowStatus
    role: DateRole | None
    rule: str  # pack rule id, for the why-panel (§7.1)
    explanation: str
    alternatives: tuple[date, ...] = ()
    source: DateFact | None = None

    def render(self) -> str:
        if self.value is None:
            return "—"
        text = self.value.isoformat()
        return f"{text} (?)" if self.status is RowStatus.AMBIGUOUS else text
