"""The claim-event dimension (PRD §8.6).

1990 / 2011 / 2022 coexist in one file. A chronology that flattens them reads as one long
injury history and loses the question the file is actually about: which claim does this
document belong to.

The engine already has what it needs. `date de l'événement` is extracted with role
`EVENT`, and §8.4 makes that role structurally ineligible to become a row date — it was
never a competitor, it was always this. So the dimension is derived, not re-parsed.

Attribution rule, in order:

1. The unit states an event date itself → it belongs to that claim.
2. Otherwise the most recent claim event *on or before* the unit's row date. A report
   cannot document an accident that has not happened yet.
3. No row date, or no claim event before it → unattributed, and said so.

Never guessed into the nearest event. An RRA of a 1990 claim pulls 1992-96 reports into a
2022 file (§5.1); assigning them to 2022 because it is closer would be confidently wrong
in exactly the way that costs a case.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..models import DateRole, ReportUnit
from ..stores import manifest


@dataclass(frozen=True)
class ClaimEvent:
    """One accident/event date the file argues about."""

    value: date
    #: Units whose own text states this event date. These are the evidence for it.
    declared_by: tuple[str, ...]

    @property
    def year(self) -> int:
        return self.value.year


@dataclass(frozen=True)
class Attribution:
    unit_id: str
    event: date | None
    rule: str
    #: True when the unit itself names the event date, rather than being placed by time.
    declared: bool = False

    @property
    def unattributed(self) -> bool:
        return self.event is None


def events_for_case(conn: sqlite3.Connection, case_id: str) -> list[ClaimEvent]:
    """Distinct event dates in the file, earliest first.

    Ambiguous readings are skipped rather than collapsed to a first guess — a claim event
    the engine is unsure of is worse than one it does not offer (§8.4).
    """
    declared: dict[date, list[str]] = {}
    for unit in manifest.units_for_case(conn, case_id):
        for fact in manifest.dates_for_unit(conn, unit.id):
            if fact.role is not DateRole.EVENT or fact.is_ambiguous:
                continue
            declared.setdefault(fact.readings[0], []).append(unit.id)

    return [
        ClaimEvent(value=value, declared_by=tuple(sorted(set(units))))
        for value, units in sorted(declared.items())
    ]


def attribute(
    unit: ReportUnit, unit_event_dates: list[date], events: list[ClaimEvent]
) -> Attribution:
    """Place one unit on the claim-event dimension."""
    if not events:
        return Attribution(unit.id, None, "claim_event.none_in_file")

    # 1. The unit names an event date. Prefer the file's own words over any inference.
    stated = [d for d in unit_event_dates if any(e.value == d for e in events)]
    if stated:
        return Attribution(unit.id, min(stated), "claim_event.declared", declared=True)

    # 2. The most recent claim event on or before the row date.
    row_date = unit.row_date.value if unit.row_date else None
    if row_date is None:
        return Attribution(unit.id, None, "claim_event.undated_unit")

    prior = [e.value for e in events if e.value <= row_date]
    if not prior:
        # Predates every claim event in the file. Real — an RRA pulls older reports in —
        # and left unattributed rather than assigned to the nearest one (§5.1).
        return Attribution(unit.id, None, "claim_event.precedes_all")

    return Attribution(unit.id, max(prior), "claim_event.most_recent_prior")


def attribute_case(conn: sqlite3.Connection, case_id: str) -> list[Attribution]:
    events = events_for_case(conn, case_id)
    out = []
    for unit in manifest.units_for_case(conn, case_id):
        stated = [
            f.readings[0]
            for f in manifest.dates_for_unit(conn, unit.id)
            if f.role is DateRole.EVENT and not f.is_ambiguous
        ]
        out.append(attribute(unit, stated, events))
    return out
