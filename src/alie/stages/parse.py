"""Stage 1 — parse and anchor (PRD §4.2).

In: a PDF. Out: blocks. Fails by: lost page anchors. Proven when: every block has page and
bbox (§14.2).

Pure and idempotent: ids in, stores read, stores written (§3.8). Re-parsing a bundle
replaces its blocks wholesale.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..models import BlockType
from ..parse import pdfium as pdfium_io
from ..parse import register_default_tiers
from ..provenance import Producer
from ..seams import parser as parser_seam
from ..seams.parser import PageInput
from ..stores import audit, blobs, cases
from ..stores import blocks as blocks_store


@dataclass(frozen=True)
class ParseResult:
    bundle_id: str
    pages: int
    blocks: int
    #: Pages the free path could not read. This is the `parse.ocr` metric: how much of a
    #: real bundle the free path misses (§9.2).
    unparseable_pages: tuple[int, ...]
    pages_with_printed_label: int

    @property
    def unparseable_ratio(self) -> float:
        return len(self.unparseable_pages) / self.pages if self.pages else 0.0


def run(conn: sqlite3.Connection, bundle_id: str, *, run_id: str | None = None) -> ParseResult:
    bundle = cases.get_bundle(conn, bundle_id)
    if bundle is None:
        raise KeyError(f"unknown bundle: {bundle_id}")

    register_default_tiers()
    producer = Producer()
    pdf_path = blobs.path_for(bundle["content_hash"])

    sizes = pdfium_io.page_sizes(str(pdf_path))

    all_blocks = []
    page_rows = []
    unparseable: list[int] = []

    for idx, (width, height) in enumerate(sizes, start=1):
        page = PageInput(
            bundle_id=bundle_id,
            pdf_index=idx,
            pdf_path=str(pdf_path),
            width=width,
            height=height,
        )
        try:
            page_blocks = parser_seam.parse(page)
            source = "text_layer"
        except parser_seam.TierUnavailable:
            # No tier handled it. Not an error — a queued page, and a metric (§9.2).
            page_blocks = []
            source = "unparseable"
            unparseable.append(idx)

        all_blocks.extend(page_blocks)
        page_rows.append(
            {
                "pdf_index": idx,
                "printed_label": _printed_label(page_blocks),
                "width": width,
                "height": height,
                "char_count": sum(len(b.text) for b in page_blocks),
                "parse_source": source,
            }
        )

    blocks_store.replace_bundle(conn, bundle_id, all_blocks, producer)
    cases.replace_pages(conn, bundle_id, page_rows, producer)

    result = ParseResult(
        bundle_id=bundle_id,
        pages=len(sizes),
        blocks=len(all_blocks),
        unparseable_pages=tuple(unparseable),
        pages_with_printed_label=sum(1 for p in page_rows if p["printed_label"]),
    )
    audit.record(
        conn,
        subject_type="bundle",
        subject_id=bundle_id,
        action="parse",
        run_id=run_id,
        rule="stage.parse",
        detail={
            "pages": result.pages,
            "blocks": result.blocks,
            "unparseable_pages": list(result.unparseable_pages),
            "pages_with_printed_label": result.pages_with_printed_label,
            "parser": producer.parser,
        },
    )
    return result


def _printed_label(page_blocks: list) -> str | None:
    """The last page-label block wins: footers sit below headers, and a running header
    repeating the document number is the more common false positive."""
    labels = [b for b in page_blocks if b.type is BlockType.PAGE_LABEL]
    return labels[-1].attrs.get("label") if labels else None
