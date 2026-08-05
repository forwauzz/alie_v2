"""Regime skills (PRD §5.1).

A pack's **rules** are data — lookups, never read by an agent. What an agent needs beyond
data is procedural knowledge that cannot be a lookup: which instrument governs, what pulls
a document into scope, where to expect the file to be shaped unlike the others.

Three constraints, all enforced here rather than described:

**Loaded into the orchestrator and adjudicator only, never the extractors.** An extractor
sees one report unit and no context on purpose (§5); handing it regime strategy invites it
to reason about the case instead of reading the page in front of it.

**One page maximum.** A long skill dilutes into noise, and the parts that matter stop being
read.

**A skill must never restate a deterministic rule.** The moment a skill says "REM uses the
exam date", it can drift from the date table and you have two sources of truth disagreeing
silently — with no error, because both are internally consistent.

That last one is the hard part, and the fix is not discipline. A skill that needs to state
a pack value writes it as a placeholder — `{vocabulary.milestone}` — which is substituted
from the pack at load. There is then exactly one source of truth, and a skill *cannot*
drift because it holds no copy.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from .loader import Pack

#: One page. Measured in characters because that is what a context window charges for.
MAX_CHARS = 3000

#: `{vocabulary.milestone}` and friends. A skill states a pack value by reference, never by
#: copy, so the two can never disagree.
PLACEHOLDER = re.compile(r"\{([a-z_]+)\.([a-z_]+)\}")


class Audience(StrEnum):
    """Who may be given a skill (§5.1)."""

    ORCHESTRATOR = "orchestrator"
    ADJUDICATOR = "adjudicator"


#: Everything else. Named so the refusal is explicit rather than an omission.
FORBIDDEN_AUDIENCES = frozenset({"extractor", "composer", "classifier"})


def _normalise(text: str) -> str:
    """Accents stripped, punctuation and underscores flattened to single spaces.

    OCR loses accents constantly and a skill is written by a human who may or may not type
    them; neither should decide whether a rule-restatement is caught.
    """
    stripped = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


class SkillNotFound(LookupError):
    """No skill for this regime, and none can be invented."""


class SkillTooLong(ValueError):
    """Over one page. A long skill dilutes into noise (§5.1)."""


class SkillRestatesARule(ValueError):
    """A skill copied a value that lives in pack data (§5.1)."""


@dataclass(frozen=True)
class Skill:
    regime: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


def _substitute(text: str, pack: Pack) -> str:
    """Resolve `{block.key}` against the pack.

    An unresolvable placeholder is an error, not a blank: a skill that renders
    "the milestone is " teaches the orchestrator that the regime has no milestone.
    """

    def replace(match: re.Match) -> str:
        block, key = match.group(1), match.group(2)
        source = getattr(pack, block, None)
        if not isinstance(source, dict):
            source = pack.pack.get(block, {})
        if not isinstance(source, dict) or key not in source:
            raise SkillNotFound(
                f"{pack.id} skill references {{{block}.{key}}}, which the pack does not define"
            )
        return str(source[key])

    return PLACEHOLDER.sub(replace, text)


def load(pack: Pack, *, audience: str = Audience.ORCHESTRATOR) -> Skill:
    """Read a regime's skill, resolved against its pack.

    Refuses any audience but the two §5.1 names. The check is on the way *in* rather than
    left to callers, because "never the extractors" is the kind of rule that erodes one
    convenient call at a time.
    """
    if audience in FORBIDDEN_AUDIENCES or audience not in tuple(Audience):
        raise PermissionError(
            f"skills load into the orchestrator and adjudicator only (§5.1); "
            f"{audience!r} is not one of them"
        )

    path = pack.root / "skill.md"
    if not path.exists():
        raise SkillNotFound(f"no skill for regime {pack.id}")

    text = _substitute(path.read_text(encoding="utf-8").strip(), pack)
    if len(text) > MAX_CHARS:
        raise SkillTooLong(
            f"{pack.id} skill is {len(text)} chars, over the {MAX_CHARS}-char page limit "
            "(§5.1) — a long skill dilutes into noise"
        )
    return Skill(regime=pack.id, text=text)


def restated_rules(pack: Pack) -> list[str]:
    """Deterministic rules a skill copied instead of referencing.

    Deliberately narrow: it looks for the exact failure §5.1 names — a skill pairing a
    document class with a date role, which is the date table restated in prose. Broader
    matching would flag the ordinary sentences a skill is *for*.

    Read the raw file, not the resolved text: a placeholder is the correct way to state a
    pack value, and resolving first would flag the very mechanism that prevents drift.
    """
    path = pack.root / "skill.md"
    if not path.exists():
        return []

    raw = path.read_text(encoding="utf-8")

    # Class *names*, not display codes. `TS` and `OP` are two letters and match inside
    # ordinary words — "asserts", "reports" — which made the first version of this check
    # fire five times on prose that stated no rule at all. A checker that cries wolf gets
    # ignored, and an ignored checker is the same as no checker.
    names: dict[str, str] = {}
    for spec in pack.class_list:
        for token in (spec["id"], spec.get("label", "")):
            # Normalised both sides, because `rapport_medical`, `Rapport médical` and
            # "a rapport medical" are the same claim written three ways, and a checker
            # that only catches one spelling catches nothing.
            key = _normalise(token)
            if key and len(key) > 5:
                names[key] = spec["id"]

    # And the role has to be used *as a date role*. The roles are named `report`, `event`,
    # `visit` — ordinary words. `exam date` asserts a mapping; "surface both with their
    # dates" does not.
    #
    # The pack's own date cues carry the French forms, so they are reused rather than
    # translated here: the role id is `exam`, the document says `date de l'examen`, and a
    # second copy of that mapping in this file would be the very drift the check exists to
    # prevent.
    role_uses: list[tuple[str, re.Pattern[str]]] = []
    for role, spec in pack.date_roles.items():
        role_uses.append((role, re.compile(rf"\b{role}\s+date\b|\bdate\s+of\s+{role}\b")))
        for cue in spec.get("cues", []):
            try:
                role_uses.append((role, re.compile(cue, re.IGNORECASE)))
            except re.error:  # pragma: no cover - the pack test compiles every pattern
                continue

    offences: list[str] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if line.lstrip().startswith(("#", ">")):
            continue  # a heading or a quotation, not an assertion
        flat, lowered = _normalise(line), line.lower()
        hit_class = next((n for n in names if n in flat), None)
        # Class names against the flattened line; cues against the line as written, since
        # they were authored for real document text.
        hit_role = next(
            (role for role, pattern in role_uses
             if pattern.search(lowered) or pattern.search(flat)),
            None,
        )
        if hit_class and hit_role:
            offences.append(
                f"{pack.id} skill line {number} pairs class {names[hit_class]!r} with date "
                f"role {hit_role!r} — that is the date table, and it will drift"
            )
    return offences
