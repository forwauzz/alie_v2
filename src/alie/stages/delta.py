"""Delta runs (PRD §10.3).

Real firms receive records in batches. The CHUM file arrives three weeks after `Médical`,
and the paralegal has already read and approved 74 rows. Handing her a fresh chronology
means re-reading all of them to find the six that changed.

So a delta run produces a **diff against the approved version**, not a new document:

    6 new rows · 2 existing rows gained a second locator · 1 row changed

Three things make that possible, and all three are already in the data model because §10.3
says this cannot be added later:

- **Unit ids are derived from content**, so a re-run over unchanged input reproduces them
  and an approved row stays attached to the same unit (§10.4).
- **Runs are immutable.** A delta compares two runs; neither is edited.
- **Row ids are derived from their unit set**, so a row that gained a locator from a second
  bundle is recognisably the same row rather than a deletion plus an insertion.

What this deliberately does not do is decide anything. A row whose text changed is reported
as changed; whether the approval carries over is the paralegal's call, and §10.2 puts
corrections in the manifest where a re-run cannot silently overwrite them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum

from ..models import Row
from ..stores import rows as rows_store


class Change(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    #: Same row, same content, an additional bundle now cites it. The §10.3 example: "2
    #: existing rows gained a second locator".
    GAINED_LOCATOR = "gained_locator"
    CONTENT_CHANGED = "content_changed"
    DATE_CHANGED = "date_changed"
    #: Present before, absent now. Never silent: a row that disappears between runs is the
    #: single most alarming thing a delta can contain (§3.4).
    DROPPED = "dropped"


@dataclass(frozen=True)
class RowChange:
    row_id: str
    change: Change
    title: str
    date: str | None
    #: Locators added since the approved run, by bundle.
    added_locators: tuple[str, ...] = ()

    @property
    def needs_review(self) -> bool:
        """Everything except a row that did not move."""
        return self.change is not Change.UNCHANGED


@dataclass
class Delta:
    before_run: str
    after_run: str
    changes: list[RowChange] = field(default_factory=list)

    def of(self, kind: Change) -> list[RowChange]:
        return [c for c in self.changes if c.change is kind]

    @property
    def review_count(self) -> int:
        return sum(1 for c in self.changes if c.needs_review)

    def summary(self) -> str:
        """The review screen's one line. `6 new rows, 2 existing rows gained a second
        locator` — not 74 rows to re-read (§10.3)."""
        parts = []
        for kind, singular, plural in (
            (Change.NEW, "nouvelle ligne", "nouvelles lignes"),
            (Change.GAINED_LOCATOR, "ligne a gagné un locateur", "lignes ont gagné un locateur"),
            (Change.CONTENT_CHANGED, "ligne modifiée", "lignes modifiées"),
            (Change.DATE_CHANGED, "ligne redatée", "lignes redatées"),
            (Change.DROPPED, "ligne disparue", "lignes disparues"),
        ):
            n = len(self.of(kind))
            if n:
                parts.append(f"{n} {singular if n == 1 else plural}")
        if not parts:
            return "aucun changement depuis la version approuvée"
        unchanged = len(self.of(Change.UNCHANGED))
        return " · ".join(parts) + f" · {unchanged} inchangées"


def _fingerprint(row: Row) -> tuple:
    return (
        row.row_date.value.isoformat() if row.row_date.value else None,
        row.title,
        tuple(b.text for b in row.bullets),
    )


def _locators(row: Row) -> set[str]:
    return {c.bundle_id for c in row.locators}


def compare(before: list[Row], after: list[Row]) -> list[RowChange]:
    """Diff two chronologies by row id.

    Row ids are derived from the unit set, so a row that gained a locator from a second
    bundle keeps its identity instead of arriving as a deletion plus an insertion — which
    is what would make a delta unreadable on the exact case §10.3 describes.
    """
    old = {r.id: r for r in before}
    new = {r.id: r for r in after}
    out: list[RowChange] = []

    for row_id, row in new.items():
        date = row.row_date.value.isoformat() if row.row_date.value else None
        previous = old.get(row_id)
        if previous is None:
            out.append(RowChange(row_id, Change.NEW, row.title, date))
            continue

        gained = _locators(row) - _locators(previous)
        if _fingerprint(previous) == _fingerprint(row):
            change = Change.GAINED_LOCATOR if gained else Change.UNCHANGED
        elif previous.row_date.value != row.row_date.value:
            change = Change.DATE_CHANGED
        else:
            change = Change.CONTENT_CHANGED
        out.append(RowChange(row_id, change, row.title, date, tuple(sorted(gained))))

    for row_id, row in old.items():
        if row_id not in new:
            date = row.row_date.value.isoformat() if row.row_date.value else None
            out.append(RowChange(row_id, Change.DROPPED, row.title, date))

    return out


def between(conn: sqlite3.Connection, before_run: str, after_run: str) -> Delta:
    """Diff two stored runs. Runs are immutable, so neither is touched (§9)."""
    return Delta(
        before_run=before_run,
        after_run=after_run,
        changes=compare(rows_store.for_run(conn, before_run), rows_store.for_run(conn, after_run)),
    )
