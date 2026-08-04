"""Parser seam (PRD §13.4): `parse(page) -> blocks`.

We adopt the output contract of a commercial parser and reject the approach: a general
parser must handle any document with no priors, and we have ~15 recurring form types
across three regimes. That asymmetry is the cost advantage (§4.3).

Pages route by type, cost rising left to right: text layer (free) -> OCR -> vision. Phase
1 ships the text-layer tier only; `parse.ocr` and `parse.vision` default off and exist to
be measured against the number the text layer produces (§9.2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..models import Block, BlockSource


@dataclass(frozen=True)
class PageInput:
    bundle_id: str
    pdf_index: int
    pdf_path: str
    width: float
    height: float


class PageParser(Protocol):
    tier: BlockSource

    def can_handle(self, page: PageInput) -> bool: ...

    def parse(self, page: PageInput) -> list[Block]: ...


class TierUnavailable(RuntimeError):
    """A page routed to a tier that is not built or not enabled."""


#: Registered tiers, in escalation order.
_TIERS: list[PageParser] = []


def register(parser: PageParser) -> None:
    _TIERS.append(parser)


def registered_tiers() -> list[str]:
    return [str(p.tier) for p in _TIERS]


def clear() -> None:
    _TIERS.clear()


#: Whether a tier's output is good enough to stop escalating. Set by the parse layer,
#: which owns the notion of readable text; the seam stays free of it so it depends on
#: nothing below itself. Default: anything non-empty.
def _non_empty(blocks: list[Block]) -> bool:
    return bool(blocks)


_ACCEPTABLE: Callable[[list[Block]], bool] = _non_empty


def set_quality_gate(gate: Callable[[list[Block]], bool]) -> None:
    global _ACCEPTABLE
    _ACCEPTABLE = gate


def parse(page: PageInput) -> list[Block]:
    """Route one page to the cheapest tier that can **honestly read** it.

    Which is not the same as the cheapest tier that returns something. A tier may claim a
    page and still produce noise — the pre-OCR'd scans in real bundles do exactly that —
    so a claim is provisional until its output passes the quality gate. If it does not,
    the page escalates.

    Without escalation the vision tier is unreachable: the OCR tier claims every page it
    is offered, so a page OCR mangles would never get a more expensive look, and
    `parse.vision`'s own metric — "% pages OCR queues that vision resolves" — would be
    permanently zero (§4.3, §9.2).
    """
    best: list[Block] = []
    for parser in _TIERS:
        if not parser.can_handle(page):
            continue
        blocks = parser.parse(page)
        if _ACCEPTABLE(blocks):
            return blocks
        # Keep the fullest attempt so far. If every tier fails, the page is reported with
        # the best available text rather than as empty — the text is still cited, and a
        # human reading a bad transcription can tell it is bad. Silence cannot be judged.
        if len(blocks) > len(best):
            best = blocks
    if best:
        return best
    raise TierUnavailable(
        f"no registered parse tier handled page {page.pdf_index} of {page.bundle_id}; "
        f"registered: {registered_tiers() or '[]'}"
    )
