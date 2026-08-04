"""Filters (PRD §6, §3.4).

Nothing is dropped. A filtered unit still reaches the manifest, the plan and the coverage
report; `excluded_by` names the rule that fired. Exclusion is a **status**, not a deletion,
and the paralegal can see every unit a rule removed and why.

Two shapes, from the pack's `filters.yaml`:

- **unconditional** — a regex over the unit's text, optionally scoped to a set of classes.
  Admin noise: billing, consent forms, transmission cover sheets.
- **conditional** — a named predicate over manifest facts. The pack states the *name*; the
  engine owns the evaluation, because a filter that removes pages from a legal record is
  not something a YAML author should be able to express in prose.

A predicate whose feature is not built yet is reported `unavailable` — never silently
false. A filter that quietly never fires looks exactly like a filter that found nothing,
and the plan would claim "0 excluded by rule" with equal confidence either way.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..models import Block, BlockType
from ..packs import Pack


@dataclass(frozen=True)
class FilterVerdict:
    """Why a unit is or is not excluded. Carried into the manifest either way."""

    rule_id: str | None = None
    reason: str | None = None
    tag: str | None = None
    #: Matched text, so the why-panel can show what tripped the rule (§7.1).
    evidence: str | None = None

    @property
    def excluded(self) -> bool:
        return self.rule_id is not None


@dataclass(frozen=True)
class UnitFacts:
    """What a predicate is allowed to see. Deliberately narrow — a predicate that can
    reach the whole database will eventually depend on something that is not a fact about
    this unit."""

    doc_class: str
    is_admin_class: bool
    has_clinical_content: bool
    text: str


#: Predicates the engine can actually evaluate. A conditional filter naming anything else
#: is `unavailable`, and the manifest says so.
Predicate = Callable[[UnitFacts], bool]

#: Conditions named by packs whose supporting feature is not built. Listed explicitly so
#: the gap is visible in code review rather than discovered when a filter never fires.
PENDING: dict[str, str] = {
    # Fires only on the `identical` verdict — all seven axes matching (§10.1).
    "unit_has_identical_duplicate_already_included": "dedupe (§10.1) is not built",
    # Needs the claim-event dimension: 1990 / 2011 / 2022 coexist in one file (§8.6).
    "unit_date_precedes_earliest_claim_event_and_no_rra_reference": (
        "the claim-event dimension (§8.6) is not built"
    ),
}


def _no_clinical_content_and_admin(facts: UnitFacts) -> bool:
    """Zero-content *clinical* documents are kept as title-only rows for evidentiary
    completeness. This applies to admin classes only (§8.5)."""
    return facts.is_admin_class and not facts.has_clinical_content


PREDICATES: dict[str, Predicate] = {
    "unit_has_no_clinical_content_and_class_is_admin": _no_clinical_content_and_admin,
}


def _compiled(pack: Pack) -> list[tuple[dict[str, Any], list[re.Pattern[str]]]]:
    out = []
    for rule in pack.filters.get("unconditional", []) or []:
        patterns = [re.compile(m, re.IGNORECASE) for m in rule.get("matches", [])]
        out.append((rule, patterns))
    return out


#: How far into a unit a self-declaration can appear. A document names itself at the top;
#: past that, a match is the document *mentioning* something, not *being* it.
DECLARATION_BLOCKS = 6


def evaluate(
    blocks: list[Block], pack: Pack, *, doc_class: str, admin_classes: set[str]
) -> tuple[FilterVerdict, list[str]]:
    """Return the verdict for one unit, plus the ids of filters that could not be judged.

    The first rule to fire wins and is named. Order is the pack's, so a pack author can
    reason about precedence without the engine inventing one.
    """
    text = "\n".join(b.text for b in blocks)
    unavailable: list[str] = []
    has_content = _has_clinical_content(blocks)

    # A filter never removes a unit that carries clinical content and is not an admin
    # document. Measured on the real bundle: a 4-page imaging report was excluded because
    # a consent form was bundled into it and the words appeared in its text. Filters exist
    # for admin noise; deleting a radiologist's findings is not a tradeoff worth making
    # for a cleaner chronology (§3.4, §8.5).
    protected = has_content and doc_class not in admin_classes

    for rule, patterns in _compiled(pack):
        classes = rule.get("classes")
        if classes and doc_class not in classes:
            continue
        if protected:
            continue
        for pattern in patterns:
            # Only where the document names itself. `FORMULAIRE DE CONSENTEMENT` in a
            # heading means the unit *is* a consent form; the same words on page 3 of an
            # imaging report mean it *references* one.
            hit = _self_declaration(blocks, pattern)
            if hit:
                return (
                    FilterVerdict(
                        rule_id=rule["id"],
                        reason=rule.get("reason"),
                        tag=rule.get("tag"),
                        evidence=hit,
                    ),
                    unavailable,
                )

    facts = UnitFacts(
        doc_class=doc_class,
        is_admin_class=doc_class in admin_classes,
        has_clinical_content=_has_clinical_content(blocks),
        text=text,
    )

    for rule in pack.filters.get("conditional", []) or []:
        name = rule.get("when", "")
        predicate = PREDICATES.get(name)
        if predicate is None:
            unavailable.append(rule["id"])
            continue
        if predicate(facts):
            return (
                FilterVerdict(rule_id=rule["id"], reason=rule.get("reason"), tag=rule.get("tag")),
                unavailable,
            )

    return FilterVerdict(), unavailable


def _self_declaration(blocks: list[Block], pattern: re.Pattern[str]) -> str | None:
    """Match only where a document names itself: a heading, or the opening blocks.

    Mirrors how boundary detection reads a declaration (§4.4) — a document announces what
    it is at the top, and anything further down is body text that may mention anything.
    """
    for index, block in enumerate(blocks):
        if index >= DECLARATION_BLOCKS and block.type is not BlockType.HEADING:
            continue
        if hit := pattern.search(block.text):
            return hit.group(0)
    return None


def _has_clinical_content(blocks: list[Block]) -> bool:
    """Body text beyond the furniture. A unit whose only text is a letterhead and a fax
    banner has no clinical content, whatever its class."""
    return any(b.is_body_text and len(b.text.strip()) >= 40 for b in blocks)
