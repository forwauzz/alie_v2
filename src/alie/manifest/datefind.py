"""Step (d) — label every date found (PRD §4.4, §8.4).

Every date on the page is emitted with a role. Nothing is discarded here: `événement`,
`naissance` and `fax` dates are found and kept, they are simply structurally ineligible to
become the row date. A pipeline that returns *one* date has already lost the information
needed to be right.

Two-digit years resolve against the file's own anchors, never a fixed century pivot — a
file spanning 1990-2026 breaks every pivot. Upper bound is today, never an inferred
maximum.
"""

from __future__ import annotations

import re
from datetime import date

from ..models import DateFact, DateRole

#: How far before the earliest 4-digit year in the file a 2-digit year may plausibly sit.
#: Anchors *rank* candidates; only `today` excludes them outright.
ANCHOR_LOOKBACK_YEARS = 30

_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
}

YEAR4 = re.compile(r"\b(19|20)\d{2}\b")

NUMERIC = re.compile(r"\b(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})\b")

TEXTUAL = re.compile(
    r"\b(\d{1,2})\s*(?:er)?\s+(" + "|".join(_MONTHS_FR) + r")\s+(\d{4})\b",
    re.IGNORECASE,
)


def file_anchors(texts: list[str]) -> tuple[int, int] | None:
    """The 4-digit years the file states about itself."""
    years = [int(m.group(0)) for t in texts for m in YEAR4.finditer(t)]
    return (min(years), max(years)) if years else None


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _centuries(two_digit: int, anchors: tuple[int, int] | None, today: date) -> list[int]:
    """Candidate full years for a two-digit year, ordered, hard-bounded by today."""
    candidates = [1900 + two_digit, 2000 + two_digit]
    candidates = [y for y in candidates if y <= today.year]
    if not candidates:
        return []
    if anchors:
        lower = anchors[0] - ANCHOR_LOOKBACK_YEARS
        within = [y for y in candidates if lower <= y <= today.year]
        # Falling back to the unfiltered set rather than returning nothing keeps the date
        # in the manifest; it will simply carry more readings and read as ambiguous.
        return within or candidates
    return candidates


def _numeric_readings(
    a: str, b: str, c: str, anchors: tuple[int, int] | None, today: date
) -> list[date]:
    """Every plausible interpretation. More than one means ambiguous (§8.4)."""
    out: list[date] = []
    ai, bi, ci = int(a), int(b), int(c)

    if len(a) == 4:  # yyyy-mm-dd
        if (d := _valid(ai, bi, ci)) and d <= today:
            out.append(d)
    elif len(c) == 4:  # dd-mm-yyyy
        if (d := _valid(ci, bi, ai)) and d <= today:
            out.append(d)
    else:
        for year in _centuries(ai, anchors, today):  # yy-mm-dd
            if (d := _valid(year, bi, ci)) and d <= today:
                out.append(d)
        for year in _centuries(ci, anchors, today):  # dd-mm-yy
            if (d := _valid(year, bi, ai)) and d <= today:
                out.append(d)

    seen: list[date] = []
    for d in out:
        if d not in seen:
            seen.append(d)
    return seen


def find_in_text(
    text: str,
    *,
    block_id: str,
    pdf_index: int,
    role_for: "RoleResolver",
    anchors: tuple[int, int] | None,
    today: date | None = None,
    confidence: float = 1.0,
) -> list[DateFact]:
    today = today or date.today()
    facts: list[DateFact] = []

    for m in TEXTUAL.finditer(text):
        day, month_name, year = m.groups()
        d = _valid(int(year), _MONTHS_FR[month_name.lower()], int(day))
        if d and d <= today:
            facts.append(
                _fact(m, (d,), text, block_id, pdf_index, role_for, confidence, False)
            )

    for m in NUMERIC.finditer(text):
        readings = _numeric_readings(*m.groups(), anchors, today)
        if not readings:
            continue
        inferred = len(m.group(1)) != 4 and len(m.group(3)) != 4
        facts.append(
            _fact(m, tuple(readings), text, block_id, pdf_index, role_for, confidence, inferred)
        )

    return facts


def _fact(
    m: re.Match[str],
    readings: tuple[date, ...],
    text: str,
    block_id: str,
    pdf_index: int,
    role_for: "RoleResolver",
    confidence: float,
    century_inferred: bool,
) -> DateFact:
    return DateFact(
        role=role_for(text, m.start()),
        readings=readings,
        raw=m.group(0),
        block_id=block_id,
        start=m.start(),
        end=m.end(),
        pdf_index=pdf_index,
        confidence=confidence,
        century_inferred=century_inferred,
    )


class RoleResolver:
    """Assigns a role from the pack's cue patterns. Cues are matched against the text
    preceding the date first, then the whole line — `Date de l'examen: 92-12-10` labels
    from the left, `Examen du 92-12-10, signé` must not be captured by a later cue."""

    def __init__(self, roles: dict[str, dict]) -> None:
        self._compiled: list[tuple[DateRole, re.Pattern[str]]] = []
        for role_name, spec in roles.items():
            role = DateRole(role_name)
            for cue in spec.get("cues", []):
                self._compiled.append((role, re.compile(cue, re.IGNORECASE)))

    def __call__(self, text: str, date_start: int) -> DateRole:
        before = text[:date_start]
        for scope in (before, text):
            best: tuple[int, DateRole] | None = None
            for role, pattern in self._compiled:
                for m in pattern.finditer(scope):
                    # Nearest cue to the left of the date wins.
                    if best is None or m.start() > best[0]:
                        best = (m.start(), role)
            if best:
                return best[1]
        return DateRole.UNKNOWN
