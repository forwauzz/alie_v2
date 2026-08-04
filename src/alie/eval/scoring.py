"""Scoring rules (PRD §11.3, §11.4).

**Fuzzy, never exact.** The gold contains `IRM de la jmabe D`, `Ilisible`, and
`polyneuropathie` where the EMG says *radiculopathie*. An exact-match scorer measures how
well the engine reproduces a human's typos.

**`[PROP]` divergences go to human adjudication**, not automatic failure. A metric that
punishes correct-but-different trains the system to reproduce the answer key's errors.

Deterministic metrics are primary. LLM-judged metrics are secondary and cost tokens; a
flaky judge makes you chase regressions that did not happen (§11.2).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

#: Below this, two strings are different things rather than one thing typed badly.
SIMILAR = 0.82

#: A date this far apart is the same encounter recorded on a different sheet. Beyond it,
#: the engine picked a different date, which is the failure the metric exists to catch.
NEAR_DAYS = 3


def normalise(text: str) -> str:
    """Case, accents and runs of whitespace carry no meaning for scoring. OCR loses
    accents constantly and the gold was typed by hand."""
    stripped = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def matches(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    return similarity(a, b) >= SIMILAR


@dataclass(frozen=True)
class Score:
    """One measured quantity, with the count behind it so a ratio is never read alone."""

    name: str
    hits: int
    total: int
    #: Set when the PRD names a value this metric must hold (§11.3).
    must_hold: float | None = None

    @property
    def value(self) -> float:
        return self.hits / self.total if self.total else 1.0

    @property
    def holds(self) -> bool:
        return self.must_hold is None or self.value >= self.must_hold

    def __str__(self) -> str:
        # ASCII only: this prints to a Windows console, where cp1252 cannot encode the
        # arrows and check marks and the traceback replaces the report.
        mark = "" if self.holds else "  <-- MUST HOLD"
        return f"{self.name}: {self.value:.0%} ({self.hits}/{self.total}){mark}"


@dataclass
class StageReport:
    stage: str
    scores: list[Score] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    #: Divergences the gold marks deliberate, or rows it never resolved. Reported, never
    #: counted as failures (§11.4).
    adjudicate: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return all(s.holds for s in self.scores)


def date_accuracy(got: date | None, want: str | None) -> str:
    """`exact` | `near` | `wrong` | `both_undated` | `missing` | `spurious`.

    Near counts because a report signed the day after the visit is the same encounter, and
    the gold's own dates come from a human reading a fax banner.
    """
    if want in (None, "") and got is None:
        return "both_undated"
    if want in (None, ""):
        return "spurious"
    if got is None:
        return "missing"
    try:
        target = date.fromisoformat(want)
    except ValueError:
        return "wrong"
    delta = abs((got - target).days)
    if delta == 0:
        return "exact"
    return "near" if delta <= NEAR_DAYS else "wrong"


def page_overlap(got: tuple[int, ...], want: tuple[int, ...]) -> float:
    """Boundary agreement as set overlap, because a report unit is a *set* of pages and
    non-contiguous is normal (§2, §8.3)."""
    a, b = set(got), set(want)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 1.0
