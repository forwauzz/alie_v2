"""Which source lines become row content (PRD §1.1).

    She does **not** summarise. She **transcribes selected lines** into a fixed row shape.

Transcribing every line is not that either — it is the document again, with citations. A
single EMR note carries around a hundred lines, of which perhaps eight belong in a
chronology; the rest is letterhead, patient identity and section furniture repeated on
every sheet.

Nothing is lost by leaving a line out of the row. Blocks stay in the block store and every
citation still resolves to them: the row is a projection of the manifest, not a
replacement for it (§3.1, §3.4).

*Which* lines matter is regime and specialty knowledge, so the rules are the pack's
(§6 output contract). What the engine contributes is the one signal no pack can state in
advance — a line that repeats across the sheets of a single document is furniture, because
clinical content does not appear identically on every page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Block, BlockType
from ..packs import Pack


@dataclass(frozen=True)
class Selection:
    kept: tuple[Block, ...]
    #: Why each dropped block was dropped, for the why-panel and for auditing a row that
    #: looks too thin.
    dropped: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reason in self.dropped.values():
            counts[reason] = counts.get(reason, 0) + 1
        return counts


def _compiled(pack: Pack, key: str) -> list[re.Pattern[str]]:
    spec = pack.output.get("row_content", {})
    return [re.compile(p, re.IGNORECASE) for p in spec.get(key, [])]


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def repeated_lines(blocks: list[Block], minimum: int) -> set[str]:
    """Lines appearing on most pages of the same unit.

    A share of the pages, not a fixed count. A masthead appears on *every* sheet, while
    clinical content legitimately recurs on two of five — `Suivi CNESST` and `ATCD HD en
    1990-05-08` open several consecutive notes. A flat threshold of two pages deleted
    those, and with them an entire five-page unit's content.
    """
    page_count = _page_count(blocks)
    if page_count < minimum:
        return set()
    needed = max(minimum, -(-page_count * 3 // 4))  # ceil(0.75 * pages)

    pages: dict[str, set[int]] = {}
    for b in blocks:
        pages.setdefault(_normalise(b.text), set()).add(b.pdf_index)
    return {text for text, seen in pages.items() if len(seen) >= needed}


def select(blocks: list[Block], pack: Pack) -> Selection:
    spec = pack.output.get("row_content", {})
    include = _compiled(pack, "include_sections")
    exclude = _compiled(pack, "exclude_sections")
    suppress = _compiled(pack, "suppress")
    threshold = int(spec.get("repeat_is_furniture", 2))

    furniture = repeated_lines(blocks, threshold) if _page_count(blocks) > 1 else set()

    # A note that declares no recognised section is not thereby empty. Falling back to
    # every content line keeps such a document in the chronology rather than rendering a
    # title with nothing under it.
    has_sections = any(
        b.type is BlockType.HEADING and any(p.search(b.text) for p in include) for b in blocks
    )

    kept: list[Block] = []
    dropped: dict[str, str] = {}
    in_included = not has_sections

    for block in blocks:
        if block.type is BlockType.HEADING:
            # Only a *content* section is exempt from the furniture rule. An excluded
            # heading that repeats on every sheet is the confidentiality footer, and
            # letting it close the section above is the bug this rule exists to fix.
            names_section = any(p.search(block.text) for p in include)
            # A heading printed on every sheet is a running header or footer, not a
            # section. The confidentiality notice at the foot of each page was closing the
            # section above it, so the tail of every note disappeared.
            #
            # Unless the pack names it: `RAISON DE LA VISITE` recurs on every page of a
            # unit holding several consecutive notes, and discarding it meant no section
            # ever opened and the whole unit came out empty.
            if not names_section and _normalise(block.text) in furniture:
                dropped[block.id] = "repeats_on_every_page"
                continue
            # Only a named exclusion closes a section. An *unrecognised* heading leaves it
            # open, because OCR promotes clinical lines to headings — `FLD 25%`, `HBA1C
            # 6.3 % -> 6.5%` — and treating those as section breaks silently dropped
            # findings like `SLR G 25 et D 40` and `A été opéré pour son tunnel carpien G`.
            # Between keeping boilerplate and losing a finding, keep the boilerplate.
            if any(p.search(block.text) for p in exclude):
                in_included = False
            elif any(p.search(block.text) for p in include):
                in_included = True
            dropped[block.id] = "section_heading"
            continue

        if not block.is_body_text:
            dropped[block.id] = "furniture_type"
            continue
        if _normalise(block.text) in furniture:
            dropped[block.id] = "repeats_on_every_page"
            continue
        if any(p.search(block.text) for p in suppress):
            dropped[block.id] = "identity_or_letterhead"
            continue
        if not in_included:
            dropped[block.id] = "outside_content_section"
            continue
        kept.append(block)

    if not kept:
        # Selection rules that empty a readable document are worse than rules that keep
        # too much: the row renders as a title with nothing under it, and a reviewer has
        # no way to tell that from a document that genuinely said nothing. Fall back to
        # every content line that is not letterhead, and say so (§3.4).
        recovered = [
            b
            for b in blocks
            if b.is_body_text
            and b.type is not BlockType.HEADING
            and not any(p.search(b.text) for p in suppress)
            and _normalise(b.text) not in furniture
        ]
        if recovered:
            return Selection(
                kept=tuple(recovered),
                dropped={"__fallback__": "no_section_matched_kept_all_content"},
            )

    return Selection(kept=tuple(kept), dropped=dropped)


def _page_count(blocks: list[Block]) -> int:
    return len({b.pdf_index for b in blocks})
