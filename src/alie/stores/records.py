"""Extracted records from stage 4a (structured) and 4b (model).

Every record stores the prompt version *and* model that produced it. This enables diffing
across versions and re-running only affected units — without it, prompt iteration on a
3000-page file is unaffordable (PRD §7).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..provenance import Producer
from .db import new_id


@dataclass(frozen=True)
class Record:
    unit_id: str
    field: str
    value: str | None
    stage: str  # "4a" | "4b"
    confidence: float = 1.0
    block_id: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    rule: str | None = None
    epistemic_tag: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    id: str | None = None

    #: True when the record asserts something no span can carry — a count, or the absence
    #: of a field. Derived records are legitimately uncited; transcriptions are not.
    derived: bool = False

    @property
    def is_cited(self) -> bool:
        """An uncited string is a validation failure, not a warning (§3.5)."""
        return self.block_id is not None and self.span_start is not None

    @property
    def violates_citation_invariant(self) -> bool:
        return not self.derived and not self.is_cited and self.value is not None


def replace_for_unit(
    conn: sqlite3.Connection, unit_id: str, stage: str, recs: list[Record], producer: Producer
) -> None:
    conn.execute("DELETE FROM records WHERE unit_id = ? AND stage = ?", (unit_id, stage))
    conn.executemany(
        """INSERT INTO records (id, unit_id, field, value, stage, block_id, span_start,
                                span_end, confidence, derived, rule, epistemic_tag,
                                prompt_version, model, producer)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                r.id or new_id("rec"),
                r.unit_id,
                r.field,
                r.value,
                r.stage,
                r.block_id,
                r.span_start,
                r.span_end,
                r.confidence,
                1 if r.derived else 0,
                r.rule,
                r.epistemic_tag,
                r.prompt_version,
                r.model,
                producer.to_json(),
            )
            for r in recs
        ],
    )


def _to_record(r: sqlite3.Row) -> Record:
    return Record(
        id=r["id"],
        unit_id=r["unit_id"],
        field=r["field"],
        value=r["value"],
        stage=r["stage"],
        block_id=r["block_id"],
        span_start=r["span_start"],
        span_end=r["span_end"],
        confidence=r["confidence"],
        derived=bool(r["derived"]),
        rule=r["rule"],
        epistemic_tag=r["epistemic_tag"],
        prompt_version=r["prompt_version"],
        model=r["model"],
    )


def for_unit(conn: sqlite3.Connection, unit_id: str) -> list[Record]:
    rows = conn.execute(
        "SELECT * FROM records WHERE unit_id = ? ORDER BY stage, field, id", (unit_id,)
    ).fetchall()
    return [_to_record(r) for r in rows]


def fields_for_unit(conn: sqlite3.Connection, unit_id: str, *, stage: str) -> frozenset[str]:
    """Field names a stage already resolved for this unit.

    Passed to 4b so it fills only what remains (§4.2) — and so the model is not asked to
    re-select something a template already read from a known coordinate, which is both
    cost and a chance to disagree with a deterministic answer.
    """
    rows = conn.execute(
        "SELECT DISTINCT field FROM records WHERE unit_id = ? AND stage = ? AND value IS NOT NULL",
        (unit_id, stage),
    ).fetchall()
    return frozenset(r["field"] for r in rows)


def fields_resolved_without_model(conn: sqlite3.Connection, case_id: str) -> tuple[int, int]:
    """Backs the `extract.structured_first` metric: % fields resolved without the model
    (§9.2)."""
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN r.stage = '4a' THEN 1 ELSE 0 END) AS structured,
             COUNT(*) AS total
           FROM records r JOIN units u ON u.id = r.unit_id
           WHERE u.case_id = ?""",
        (case_id,),
    ).fetchone()
    return (row["structured"] or 0, row["total"] or 0)
