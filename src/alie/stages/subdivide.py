"""Subdividing a long unit (PRD §12).

Input truncation is structurally near-impossible here: the model sees one report unit, not
a bundle. Unit boundaries are the mitigation, and they were designed for other reasons.

One residual risk remains — a **genuinely long single unit**, the 40-page expertise. That
one document can exceed what a single call should carry, and the mitigation is stated
precisely: subdivide by **the document's own structure**, never by arbitrary character
counts.

The distinction is not stylistic. A character-count split cuts mid-sentence, mid-table and
mid-barème-row, and every downstream guarantee degrades with it: a span that starts in one
chunk and ends in another cannot be cited, and 4a's expected-row count — the only thing
that makes *silent* output truncation detectable — is computed per section. Splitting on
`## 12. BILAN DES SÉQUELLES` keeps each part a thing the document itself claims is a thing.

A unit with no internal structure is **not split**. Handing back arbitrary pieces of an
unstructured document would trade a truncation risk for a citation risk, and only one of
those is detectable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Block, BlockType

#: Above this many characters a unit is worth subdividing. Well under any model's input
#: limit — the point is not to sail close to the wall, it is that a call carrying a whole
#: expertise is a call whose output is more likely to be cut off (§12).
LONG_UNIT_CHARS = 40_000

#: A section this small is a heading and a line, not a section. Merged into the previous
#: part rather than becoming its own call.
MIN_SECTION_CHARS = 200


@dataclass(frozen=True)
class Part:
    """One subdivision of a unit. Blocks only — no text is copied, so every citation still
    resolves against the block store exactly as before (§8.1)."""

    index: int
    heading: str | None
    blocks: tuple[Block, ...]

    @property
    def chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    @property
    def block_ids(self) -> tuple[str, ...]:
        return tuple(b.id for b in self.blocks)


@dataclass(frozen=True)
class Subdivision:
    unit_id: str
    parts: tuple[Part, ...]
    reason: str

    @property
    def split(self) -> bool:
        return len(self.parts) > 1


def needs_subdividing(blocks: list[Block]) -> bool:
    return sum(len(b.text) for b in blocks) > LONG_UNIT_CHARS


def subdivide(unit_id: str, blocks: list[Block]) -> Subdivision:
    """Split a long unit on its own headings, or decline to split it.

    Returns a single part for anything short enough, or for anything long with no internal
    structure — declining is the safe answer, and the caller can see which happened.
    """
    ordered = sorted(blocks, key=lambda b: (b.pdf_index, b.order))

    if not needs_subdividing(ordered):
        return Subdivision(unit_id, (Part(0, None, tuple(ordered)),), "short_enough")

    headings = [i for i, b in enumerate(ordered) if b.type is BlockType.HEADING]
    if not headings:
        # Long and structureless. Not split: arbitrary pieces would trade a truncation
        # risk for a citation risk, and only one of those is detectable (§12).
        return Subdivision(unit_id, (Part(0, None, tuple(ordered)),), "no_internal_structure")

    parts = _split_on(ordered, headings)
    if len(parts) == 1:
        return Subdivision(unit_id, tuple(parts), "structure_too_shallow")
    return Subdivision(unit_id, tuple(parts), "split_on_headings")


def _split_on(ordered: list[Block], headings: list[int]) -> list[Part]:
    """Cut before each heading, then fold away sections too small to be sections."""
    cuts = sorted({0, *headings})
    raw: list[tuple[str | None, list[Block]]] = []
    for position, start in enumerate(cuts):
        end = cuts[position + 1] if position + 1 < len(cuts) else len(ordered)
        chunk = ordered[start:end]
        if not chunk:
            continue
        heading = chunk[0].text.strip() if chunk[0].type is BlockType.HEADING else None
        raw.append((heading, chunk))

    merged: list[tuple[str | None, list[Block]]] = []
    for heading, chunk in raw:
        size = sum(len(b.text) for b in chunk)
        if merged and size < MIN_SECTION_CHARS:
            # `## 12.` on its own line above the real section, or a heading with one line
            # under it. Folding it back keeps a part meaning what the document means.
            merged[-1][1].extend(chunk)
            continue
        merged.append((heading, list(chunk)))

    return [Part(i, heading, tuple(chunk)) for i, (heading, chunk) in enumerate(merged)]
