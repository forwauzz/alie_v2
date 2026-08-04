"""Parser seam (PRD §13.4): `parse(page) -> blocks`.

We adopt the output contract of a commercial parser and reject the approach: a general
parser must handle any document with no priors, and we have ~15 recurring form types
across three regimes. That asymmetry is the cost advantage (§4.3).

Pages route by type, cost rising left to right: text layer (free) -> OCR -> vision. Phase
1 ships the text-layer tier only; `parse.ocr` and `parse.vision` default off and exist to
be measured against the number the text layer produces (§9.2).
"""

from __future__ import annotations

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


def parse(page: PageInput) -> list[Block]:
    """Route one page to the cheapest tier that can handle it."""
    for parser in _TIERS:
        if parser.can_handle(page):
            return parser.parse(page)
    raise TierUnavailable(
        f"no registered parse tier handled page {page.pdf_index} of {page.bundle_id}; "
        f"registered: {registered_tiers() or '[]'}"
    )
