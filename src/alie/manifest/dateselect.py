"""Step (e) — select the row date; class decides which role wins (PRD §4.4, §8.4).

The model is not permitted to choose the date. Extraction output is overwritten by this
decision, which explains itself in one line.

Never infer chronology from page position. `Médical pdf 71572-1.pdf` runs newest-first and
interleaving is imperfect, so any heuristic using neighbouring-page dates as a
plausibility prior is prohibited (§8.2). Nothing in this module looks at a neighbour.
"""

from __future__ import annotations

from ..models import ELIGIBLE_ROLES, DateFact, DateRole, Legibility, RowDate, RowStatus
from ..packs import Pack


def _reading_order(f: DateFact) -> tuple[int, int]:
    return (f.pdf_index, f.start)


def select(
    facts: list[DateFact],
    *,
    doc_class: str,
    pack: Pack,
    legibility: Legibility = Legibility.LEGIBLE,
) -> RowDate:
    spec = pack.date_rule_table.get(doc_class) or pack.date_rule_table.get("unknown", {})
    priority = [DateRole(r) for r in spec.get("priority", [])]
    rule = f"{pack.id}.dates.{doc_class}"

    if legibility is Legibility.ILLEGIBLE:
        return RowDate(
            value=None,
            status=RowStatus.ILLEGIBLE,
            role=None,
            rule=rule,
            explanation="Unit is illegible; no date read and no model call permitted.",
        )

    eligible = [f for f in facts if f.role in ELIGIBLE_ROLES]
    ineligible = [f for f in facts if f.role not in ELIGIBLE_ROLES]

    for role in priority:
        candidates = sorted((f for f in eligible if f.role is role), key=_reading_order)
        if candidates:
            return _from_fact(candidates[0], role, rule, ineligible)

    # Eligible dates exist but none carries a role this class ranks. Take the earliest in
    # reading order rather than dropping the date, and say so.
    if eligible:
        chosen = sorted(eligible, key=_reading_order)[0]
        rd = _from_fact(chosen, chosen.role, rule, ineligible)
        return RowDate(
            value=rd.value,
            status=RowStatus.INFERRED if rd.status is RowStatus.RESOLVED else rd.status,
            role=rd.role,
            rule=rule,
            explanation=(
                f"No {'/'.join(str(r) for r in priority)} date found for {doc_class}; "
                f"used the earliest eligible date, role {chosen.role}."
            ),
            alternatives=rd.alternatives,
            source=chosen,
        )

    return RowDate(
        value=None,
        status=RowStatus.UNDATED,
        role=None,
        rule=rule,
        explanation=_undated_reason(ineligible),
    )


def _from_fact(
    fact: DateFact, role: DateRole, rule: str, ineligible: list[DateFact]
) -> RowDate:
    if fact.is_ambiguous:
        return RowDate(
            value=fact.readings[0],
            status=RowStatus.AMBIGUOUS,
            role=role,
            rule=rule,
            explanation=(
                f"{fact.raw!r} has {len(fact.readings)} plausible readings "
                f"({', '.join(d.isoformat() for d in fact.readings)}); needs a human."
            ),
            alternatives=fact.readings[1:],
            source=fact,
        )

    status = RowStatus.INFERRED if fact.century_inferred else RowStatus.RESOLVED
    note = _suppressed_note(ineligible)
    return RowDate(
        value=fact.readings[0],
        status=status,
        role=role,
        rule=rule,
        explanation=(
            f"Role {role} selected from {fact.raw!r}"
            + (" (century resolved against the file's own year anchors)" if fact.century_inferred else "")
            + note
        ),
        source=fact,
    )


def _suppressed_note(ineligible: list[DateFact]) -> str:
    if not ineligible:
        return "."
    roles = sorted({str(f.role) for f in ineligible})
    return f"; {len(ineligible)} structurally ineligible date(s) present ({', '.join(roles)})."


def _undated_reason(ineligible: list[DateFact]) -> str:
    if not ineligible:
        return "No date found anywhere in the unit."
    roles = sorted({str(f.role) for f in ineligible})
    return (
        f"Dates found but all structurally ineligible to be the row date "
        f"({', '.join(roles)}); the unit needs a date from a human."
    )
