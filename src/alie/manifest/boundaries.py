"""Step (a) — boundary detection (PRD §4.4).

Parser headings are a **signal, not a boundary**. Observed in a 139-page bundle: 117 H1s
including `# Québec`, `# Dossier: <empty>`, `# NOM ET PRI`, plus OCR damage
(`RAPPORI M AL`, `RAPPORTD'IMAGERIE` with the space eaten). One `Certificat Médical`
emitted two headings. So a heading contributes to a score; it does not decide.

The printed page label is the strongest signal available at the text-layer tier: `p. 1 de
2` opens a document and `p. 2 de 2` continues one, regardless of what the headings say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Block, BlockType
from ..packs import Pack
from ..parse.textquality import word_likeness

#: A page starts a new unit at or above this score.
START_THRESHOLD = 0.5

#: Fraction of page height counted as "near the top" for heading signals.
TOP_BAND = 0.4

#: Word-likeness below which a page's text layer is treated as noise for the purpose of
#: grouping. Matches the legibility gate's illegible threshold.
READABLE_QUALITY = 0.35

FORM_SERIAL = re.compile(r"\bformulaire\s*n?[°o]?\s*(\d{3,5})\b", re.IGNORECASE)
SERIAL_WITH_REVISION = re.compile(r"\b(\d{4})\s*\(\s*(\d{4}-\d{2})\s*\)")

#: `p. 1 de 2` opens; `p. 2 de 2` continues.
LABEL_OF = re.compile(r"^\s*(?:p(?:age)?\.?\s*)?(\d{1,4})\s*(?:de|of|/|sur)\s*(\d{1,4})\s*$",
                      re.IGNORECASE)


@dataclass(frozen=True)
class PageSignals:
    pdf_index: int
    starts: bool
    score: float
    reasons: tuple[str, ...]
    label_position: tuple[int, int] | None  # (k, n) from a `k de n` printed label
    serial: str | None
    revision: str | None
    author: str | None
    empty: bool
    #: False when the page's text layer is noise. An unreadable page cannot be known to
    #: continue the document before it.
    readable: bool = True


def _class_heading_patterns(pack: Pack) -> list[re.Pattern[str]]:
    return [
        re.compile(p, re.IGNORECASE)
        for c in pack.class_list
        for p in c.get("headings", [])
    ]


def _label_position(blocks: list[Block]) -> tuple[int, int] | None:
    for b in blocks:
        if b.type is BlockType.PAGE_LABEL:
            m = LABEL_OF.match(b.text)
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def _serial(blocks: list[Block]) -> tuple[str | None, str | None]:
    """Registry key is form id + revision. Coordinates shift between revisions, and
    silently reading wrong coordinates is worse than no template (§4.3)."""
    for b in blocks:
        if m := SERIAL_WITH_REVISION.search(b.text):
            return m.group(1), m.group(2)
        if m := FORM_SERIAL.search(b.text):
            return m.group(1), None
    return None, None


def _author(blocks: list[Block]) -> str | None:
    for b in blocks:
        if b.type is BlockType.SIGNATURE:
            text = re.sub(r"^\s*sign[ée]e?\s*(par|:)?\s*", "", b.text, flags=re.IGNORECASE)
            return text.strip(" .:") or None
    return None


def signals_for_page(
    blocks: list[Block], page_height: float, pack: Pack, patterns: list[re.Pattern[str]]
) -> PageSignals:
    pdf_index = blocks[0].pdf_index if blocks else 0
    if not blocks:
        return PageSignals(
            pdf_index, True, 1.0, ("blank page",), None, None, None, None, True, readable=False
        )

    score = 0.0
    reasons: list[str] = []

    top = page_height * TOP_BAND
    headings = [b for b in blocks if b.type is BlockType.HEADING and b.bbox.y0 <= top]
    if any(p.search(b.text) for b in headings for p in patterns):
        score += 0.5
        reasons.append("class heading near top of page")

    serial, revision = _serial(blocks)
    if serial:
        score += 0.3
        reasons.append(f"form serial {serial}")

    position = _label_position(blocks)
    if position:
        k, n = position
        if k == 1:
            score += 0.4
            reasons.append(f"printed label opens a document (1 de {n})")
        else:
            score -= 0.7
            reasons.append(f"printed label continues a document ({k} de {n})")

    quality = word_likeness(" ".join(b.text for b in blocks))
    readable = quality >= READABLE_QUALITY
    if not readable:
        reasons.append(f"text layer is {quality:.0%} word-like")

    return PageSignals(
        pdf_index=pdf_index,
        starts=score >= START_THRESHOLD or not readable,
        score=score,
        reasons=tuple(reasons),
        label_position=position,
        serial=serial,
        revision=revision,
        author=_author(blocks),
        empty=False,
        readable=readable,
    )


def group_pages(
    pages: dict[int, list[Block]], heights: dict[int, float], pack: Pack
) -> tuple[list[list[int]], dict[int, PageSignals]]:
    """Contiguous first pass. Non-contiguous units are recovered by the re-join pass."""
    patterns = _class_heading_patterns(pack)
    signals = {
        idx: signals_for_page(blocks, heights.get(idx, 792.0), pack, patterns)
        for idx, blocks in sorted(pages.items())
    }

    groups: list[list[int]] = []
    previous: int | None = None
    for idx in sorted(pages):
        if not groups or signals[idx].starts or not _continues(signals, previous, idx):
            groups.append([idx])
        else:
            groups[-1].append(idx)
        previous = idx
    return groups, signals


def _continues(signals: dict[int, PageSignals], previous: int | None, idx: int) -> bool:
    """Whether a non-starting page belongs to the group physically before it.

    A page printed `p. 2 de 2` continues *some* document, but not necessarily the one on
    the preceding sheet — a consult note interrupted by an IRM resumes after it. When the
    labels do not chain, the page opens its own fragment and the re-join pass finds its
    real host (§8.3).

    Legibility gates this too. On the 139-page reference bundle a third of pages carry a
    failed OCR pass, and physical adjacency alone merged 32 of them into one confident,
    wrong 32-page "unit". You cannot know an unreadable page continues the document before
    it, so it does not join one — it becomes its own unit with an illegible status, which
    is the truthful representation (§3.4).
    """
    if previous is None:
        return False
    here, before = signals[idx], signals.get(previous)
    if before is None:
        return False
    if not here.readable or not before.readable:
        return False
    if here.label_position is None or before.label_position is None:
        return True  # no label evidence either way; fall back to physical adjacency
    return (
        before.label_position[1] == here.label_position[1]
        and here.label_position[0] == before.label_position[0] + 1
    )
