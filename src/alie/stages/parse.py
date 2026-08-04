"""Stage 1 — parse and anchor (PRD §4.2).

In: a PDF. Out: blocks. Fails by: lost page anchors. Proven when: every block has page and
bbox (§14.2).

Pure and idempotent: ids in, stores read, stores written (§3.8). Re-parsing a bundle
replaces its blocks wholesale.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..models import BlockType
from ..parse import pagelabel, register_tiers
from ..parse import pdfium as pdfium_io
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
    #: Pages that yielded no blocks, whatever the reason — no tier claimed them, or a
    #: tier claimed them and found nothing. Both mean the page is still unread and still
    #: needs a more expensive tier, which is what the `parse.ocr` metric asks (§9.2).
    #: `pages_by_tier` carries the distinction when it matters.
    unparseable_pages: tuple[int, ...]
    pages_with_printed_label: int
    #: Pages each tier actually claimed. The `parse.ocr` metric is the shift between these
    #: counts with the flag off and on (§9.2).
    pages_by_tier: dict[str, int]

    @property
    def unparseable_ratio(self) -> float:
        return len(self.unparseable_pages) / self.pages if self.pages else 0.0


def run(
    conn: sqlite3.Connection,
    bundle_id: str,
    *,
    flags: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> ParseResult:
    bundle = cases.get_bundle(conn, bundle_id)
    if bundle is None:
        raise KeyError(f"unknown bundle: {bundle_id}")

    tiers = register_tiers(flags)
    producer = Producer(ocr=_ocr_tag(flags))
    pdf_path = blobs.path_for(bundle["content_hash"])

    sizes = pdfium_io.page_sizes(str(pdf_path))

    all_blocks = []
    page_rows = []
    unparseable: list[int] = []
    label_candidates: dict[int, tuple[str, str]] = {}

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
            source = str(page_blocks[0].source) if page_blocks else "empty"
        except parser_seam.TierUnavailable:
            # No tier handled it. Not an error — a queued page, and a metric (§9.2).
            page_blocks = []
            source = "unclaimed"
        if not page_blocks:
            unparseable.append(idx)

        all_blocks.extend(page_blocks)
        candidate = _label_candidate(page_blocks)
        if candidate:
            label_candidates[idx] = candidate
        page_rows.append(
            {
                "pdf_index": idx,
                "printed_label": None,  # filled in below, once the whole bundle is known
                "width": width,
                "height": height,
                "char_count": sum(len(b.text) for b in page_blocks),
                "parse_source": source,
            }
        )

    # A bare footer number is only a page label if it behaves like one across the bundle.
    # That cannot be decided one page at a time, so it happens here (§8.1).
    confirmed = pagelabel.confirm_bare_labels(label_candidates)
    for row in page_rows:
        row["printed_label"] = confirmed.get(row["pdf_index"])

    blocks_store.replace_bundle(conn, bundle_id, all_blocks, producer)
    cases.replace_pages(conn, bundle_id, page_rows, producer)

    result = ParseResult(
        bundle_id=bundle_id,
        pages=len(sizes),
        blocks=len(all_blocks),
        unparseable_pages=tuple(unparseable),
        pages_with_printed_label=sum(1 for p in page_rows if p["printed_label"]),
        pages_by_tier=Counter(p["parse_source"] for p in page_rows),
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
            "pages_by_tier": dict(result.pages_by_tier),
            "tiers_registered": tiers,
            "parser": producer.parser,
            "ocr": producer.ocr,
        },
    )
    return result


def _label_candidate(page_blocks: list) -> tuple[str, str] | None:
    """`(rule, label)` for the page, or None.

    The last page-label block wins: footers sit below headers, and a running header
    repeating the document number is the more common false positive.
    """
    labels = [b for b in page_blocks if b.type is BlockType.PAGE_LABEL]
    if not labels:
        return None
    winner = labels[-1]
    return (winner.attrs.get("rule", "bare_number"), winner.attrs.get("label", ""))


def _ocr_tag(flags: dict[str, Any] | None) -> str:
    """Producer stamp for the OCR component. A case whose pages were parsed by two engines
    with no record of which is which is worse than never having had the flag (§9)."""
    from ..parse.ocr import available as ocr_available
    from ..parse.ocr import load_config

    if not (flags or {}).get("parse.ocr"):
        return "none"
    config = load_config()
    return config.version_tag if ocr_available(config) else "unavailable"
