"""Duplicates are a view over the manifest (PRD §10.1).

Amélie: *"il y a sept choses que vous devriez regarder"* — seven things you have to look at
to make sure it is a duplicate. Her example: identical masthead, different visit dates →
keep both.

Page hashing cannot express that. Seven axes over report units:

    doc_class · event_date · author · form_serial · clinical_content · annotations ·
    transmission

Only `identical` — all seven agreeing — is auto-removable, and even then removal is a
**status**, not a deletion. Removing pages from a legal record is never a destructive
operation on the source, and a deduplicated export carries a manifest of what was held
back and why.

The content fingerprint strips transmission furniture before comparing, then reports it on
its own axis — so a re-fax scores 1.00 on content *and* shows exactly what changed.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import combinations

from ..models import Block, BlockType, ReportUnit
from ..stores import blocks as blocks_store
from ..stores import manifest

#: The seven axes, in the order Amélie names them.
AXES: tuple[str, ...] = (
    "doc_class",
    "event_date",
    "author",
    "form_serial",
    "clinical_content",
    "annotations",
    "transmission",
)

#: Content this similar is the same document. Below it, two reports that share a masthead
#: and a date are still two reports.
CONTENT_SAME = 0.97


class Verdict(StrEnum):
    IDENTICAL = "identical"
    SAME_DOC_DIFFERENT_ARTIFACT = "same_doc_different_artifact"
    RELATED = "related"
    DIFFERENT = "different"

    @property
    def removable(self) -> bool:
        """The only auto-removable case. Everything else is firm policy or a real
        distinction (§10.1)."""
        return self is Verdict.IDENTICAL


@dataclass(frozen=True)
class Fingerprint:
    """One unit reduced to the seven axes."""

    unit_id: str
    doc_class: str
    event_date: date | None
    author: str | None
    form_serial: str | None
    #: Clinical text with transmission furniture removed.
    clinical_content: str
    #: Handwriting and stamps added to a copy — a marked-up rescan is not a duplicate.
    annotations: tuple[str, ...]
    #: Fax banners and mailroom marks. Its own axis so a re-fax shows what changed.
    transmission: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    a: str
    b: str
    verdict: Verdict
    #: Per-axis agreement, so the paralegal sees *which* of the seven differ (§10.1).
    axes: dict[str, bool]
    content_similarity: float

    @property
    def differing(self) -> tuple[str, ...]:
        return tuple(name for name, same in self.axes.items() if not same)


_FURNITURE_TYPES = frozenset({BlockType.STAMP, BlockType.PAGE_LABEL, BlockType.EMPTY})
_NOISE = re.compile(r"[^a-z0-9 ]+")


def _normalise(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", _NOISE.sub(" ", stripped)).strip()


def fingerprint(conn: sqlite3.Connection, unit: ReportUnit) -> Fingerprint:
    blocks = blocks_store.for_pages(conn, unit.bundle_id, unit.pages)
    # The date of the encounter this document records — the row date, not the claim event.
    # Amélie's example is "identical masthead, different visit dates → keep both", so the
    # axis is the visit. Using the claim event made two consultation notes with different
    # visit dates agree, because neither carried one (§8.4, §10.1).
    return Fingerprint(
        unit_id=unit.id,
        doc_class=unit.doc_class,
        event_date=unit.row_date.value if unit.row_date else None,
        author=_normalise(unit.author) if unit.author else None,
        form_serial=unit.form_serial,
        clinical_content=_content(blocks),
        annotations=_of_type(blocks, BlockType.HANDWRITING),
        transmission=_of_type(blocks, BlockType.STAMP),
    )


def _content(blocks: list[Block]) -> str:
    """Body text with transmission furniture stripped, so a re-fax scores 1.00 here and
    differs only on its own axis (§10.1)."""
    return " ".join(
        _normalise(b.text)
        for b in blocks
        if b.is_body_text and b.type not in _FURNITURE_TYPES
    ).strip()


def _of_type(blocks: list[Block], kind: BlockType) -> tuple[str, ...]:
    return tuple(sorted(_normalise(b.text) for b in blocks if b.type is kind and b.text.strip()))


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def compare(a: Fingerprint, b: Fingerprint) -> Comparison:
    """Score one pair across the seven axes and name the verdict."""
    content = _similarity(a.clinical_content, b.clinical_content)
    axes = {
        "doc_class": a.doc_class == b.doc_class,
        # Two undated units have not *agreed* on a date, they have both failed to state
        # one. Unknown counts against sameness, because only `identical` is removable and
        # the conservative direction is the safe one (§9, §10.1).
        "event_date": a.event_date is not None and a.event_date == b.event_date,
        "author": a.author == b.author,
        "form_serial": a.form_serial == b.form_serial,
        "clinical_content": content >= CONTENT_SAME,
        "annotations": a.annotations == b.annotations,
        "transmission": a.transmission == b.transmission,
    }
    return Comparison(a.unit_id, b.unit_id, _verdict(axes), axes, content)


def _verdict(axes: dict[str, bool]) -> Verdict:
    if all(axes.values()):
        return Verdict.IDENTICAL

    identity = ("doc_class", "event_date", "author", "form_serial", "clinical_content")
    if all(axes[name] for name in identity):
        # Same document, different physical artifact: a rescan, a re-fax, or a copy
        # somebody wrote on. Not removable — firm policy, and the annotation may be the
        # most important thing on the page (§10.1).
        return Verdict.SAME_DOC_DIFFERENT_ARTIFACT

    if axes["doc_class"] and axes["event_date"] and not axes["clinical_content"]:
        # Same class, same date, different content: the same encounter documented twice,
        # not a duplicate.
        return Verdict.RELATED

    return Verdict.DIFFERENT


def candidates(conn: sqlite3.Connection, case_id: str) -> list[Comparison]:
    """Every pair worth reporting. Excludes `different`, which is most pairs.

    Deliberately O(n²) over units, not pages: a unit is a document, and a case holds tens
    of them where a bundle holds hundreds of pages. Vector prefiltering is a §16 concern
    and only ever a secondary index over the manifest.
    """
    prints = [fingerprint(conn, u) for u in manifest.units_for_case(conn, case_id)]
    out = [compare(a, b) for a, b in combinations(prints, 2)]
    return [c for c in out if c.verdict is not Verdict.DIFFERENT]


def removable(comparisons: list[Comparison]) -> dict[str, str]:
    """unit_id -> the unit it duplicates. Only strictly identical pairs, and only the
    later one of each pair.

    A safety invariant, not a flag: only strictly identical duplicates are auto-removable
    (§9). The kept unit is named so the export's manifest can say what was held back and
    against what.
    """
    out: dict[str, str] = {}
    for c in comparisons:
        if not c.verdict.removable:
            continue
        # Never remove something already removed — chains would empty a whole set.
        if c.a in out:
            continue
        out.setdefault(c.b, c.a)
    return out
