"""Blocks — parse output, immutable, page and bbox anchored (PRD §4.3, §4.5).

Blocks are the truth; markdown is a rendering of blocks. Commercial markdown output loses
page boundaries entirely, which is fatal when column 2 is a page locator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BlockType(StrEnum):
    # Adopted from the commercial parser output contract (§4.3).
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CHECKBOX = "checkbox"
    SIGNATURE = "signature"
    EMPTY = "empty"  # load-bearing: field present but blank, vs field absent

    # Added beyond that vocabulary.
    HANDWRITING = "handwriting"  # detected, never merged into body text, never recognised
    PAGE_LABEL = "page_label"  # the printed "p. 3 de 4" / EMR stamp (§8.1)
    STAMP = "stamp"  # fax banners, mailroom marks — the dedupe transmission axis


class BlockSource(StrEnum):
    """Which parse tier produced this block. Cost rises left to right (§4.3)."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    VISION = "vision"
    TEMPLATE = "template"  # read from a registered field map at known coordinates


@dataclass(frozen=True)
class BBox:
    """PDF user-space coordinates, origin top-left, in points."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class Block:
    id: str
    bundle_id: str
    pdf_index: int  # 1-based page position in the uploaded PDF
    type: BlockType
    text: str
    bbox: BBox
    source: BlockSource
    confidence: float  # so a mis-OCR'd percentage ("2°2") is flagged rather than shipped
    order: int  # reading order within the page
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def is_body_text(self) -> bool:
        """Handwriting, stamps and page labels are never merged into body text."""
        return self.type in (BlockType.HEADING, BlockType.PARAGRAPH, BlockType.TABLE)
