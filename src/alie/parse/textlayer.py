"""Text-layer parse tier — born digital, free (PRD §4.3).

This is the tier Phase 1 ships. It produces the number everything else is judged against:
what percentage of a real bundle the free parse path covers. Until it exists, OCR and
vision have no baseline to beat (§14).

A page with no extractable text is not an error — it is queued as unparseable, which is
the `parse.ocr` metric (§9.2).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ..models import BBox, Block, BlockSource, BlockType
from ..provenance import hash_text
from ..seams.parser import PageInput
from . import blocktype, pagelabel

#: Confidence assigned to text-layer reads. Extraction is exact; the discount below is a
#: source-quality signal, not an extraction-fidelity one.
BASE_CONFIDENCE = 1.0
DEGENERATE_CONFIDENCE = 0.55


class TextLayerParser:
    """Implements the `PageParser` protocol from the parser seam."""

    tier = BlockSource.TEXT_LAYER

    def can_handle(self, page: PageInput) -> bool:
        return page_char_count(Path(page.pdf_path), page.pdf_index) > 0

    def parse(self, page: PageInput) -> list[Block]:
        return parse_page(page)


def page_char_count(pdf_path: Path, pdf_index: int) -> int:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        textpage = pdf[pdf_index - 1].get_textpage()
        return textpage.count_chars()
    finally:
        pdf.close()


def _block_id(bundle_id: str, pdf_index: int, order: int, text: str) -> str:
    """Stable across re-parses of identical input, so re-runs produce no spurious diffs."""
    return f"blk_{hash_text(f'{bundle_id}|{pdf_index}|{order}|{text}')[:20]}"


def _char_sizes(textpage) -> list[tuple[float, float, float]]:
    """`(centre_x, centre_y, font_size)` per character, in PDF coordinates."""
    out = []
    for i in range(textpage.count_chars()):
        left, bottom, right, top = textpage.get_charbox(i)
        size = pdfium_c.FPDFText_GetFontSize(textpage.raw, i)
        out.append(((left + right) / 2, (bottom + top) / 2, size))
    return out


def _size_for_rect(
    rect: tuple[float, float, float, float], chars: list[tuple[float, float, float]]
) -> float:
    left, bottom, right, top = rect
    sizes = [s for cx, cy, s in chars if left <= cx <= right and bottom <= cy <= top]
    if not sizes:
        return 0.0
    return Counter(sizes).most_common(1)[0][0]


def parse_page(page: PageInput) -> list[Block]:
    pdf = pdfium.PdfDocument(page.pdf_path)
    try:
        pdf_page = pdf[page.pdf_index - 1]
        _, height = pdf_page.get_size()
        textpage = pdf_page.get_textpage()

        # count_rects() must be called with default params once before get_rect().
        count = textpage.count_rects()
        chars = _char_sizes(textpage)
        raw: list[tuple[tuple[float, float, float, float], str, float]] = []
        for i in range(count):
            rect = textpage.get_rect(i)
            text = textpage.get_text_bounded(*rect)
            if text.strip():
                raw.append((rect, text, _size_for_rect(rect, chars)))
    finally:
        pdf.close()

    if not raw:
        return []

    # Body size is the size most characters on the page are set in — not the mean, which
    # a long heading would drag upward.
    body_size = Counter(s for _, _, s in chars).most_common(1)[0][0] if chars else 0.0

    blocks: list[Block] = []
    for order, ((left, bottom, right, top), text, font_size) in enumerate(raw):
        # pdfium is origin bottom-left; blocks are origin top-left.
        bbox = BBox(x0=left, y0=height - top, x1=right, y1=height - bottom)
        blocks.append(_make_block(page, order, text, bbox, font_size, body_size, height))
    return blocks


def _make_block(
    page: PageInput,
    order: int,
    text: str,
    bbox: BBox,
    font_size: float,
    body_size: float,
    page_height: float,
) -> Block:
    label = pagelabel.detect(text, bbox.y0, bbox.y1, page_height)
    if label:
        rule, value = label
        btype, attrs = BlockType.PAGE_LABEL, {"label": value, "rule": rule}
    else:
        btype, attrs = blocktype.infer(
            text,
            font_size=font_size,
            body_size=body_size,
            is_upper_dense=blocktype.upper_density(text) > 0.8,
        )
    attrs = attrs | {"font_size": f"{font_size:g}"}

    confidence = BASE_CONFIDENCE
    if blocktype.is_degenerate_number(text):
        confidence = DEGENERATE_CONFIDENCE
        attrs = attrs | {"degenerate_number": "true"}

    return Block(
        id=_block_id(page.bundle_id, page.pdf_index, order, text),
        bundle_id=page.bundle_id,
        pdf_index=page.pdf_index,
        type=btype,
        text=text.strip(),
        bbox=bbox,
        source=BlockSource.TEXT_LAYER,
        confidence=confidence,
        order=order,
        attrs=attrs,
    )
