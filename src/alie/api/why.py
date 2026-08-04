"""The why-panel (PRD §7.1).

The unit of UI display is the answer to *"why does this row say this?"* — resolved prompt
text, prompt version, model and parameters, the rule that fired **and its epistemic tag**,
the source span, a crop of the source region, and the timestamp.

This is the trust surface. It is what makes the product feel like a domain expert rather
than a black box emitting French.
"""

from __future__ import annotations

import sqlite3

from ..packs import load as load_pack
from ..stores import audit, cases, corrections, manifest, records
from ..stores import blocks as blocks_store


def for_unit(conn: sqlite3.Connection, unit_id: str) -> dict:
    unit = manifest.get_unit(conn, unit_id)
    if unit is None:
        raise KeyError(f"unknown unit: {unit_id}")
    case = cases.get_case(conn, unit.case_id)
    bundle = cases.get_bundle(conn, unit.bundle_id)
    pack = load_pack(unit.regime or case["primary_pack"])
    labels = cases.printed_labels(conn, unit.bundle_id)

    return {
        "unit_id": unit.id,
        "pages": list(unit.pages),
        "contiguous": unit.is_contiguous,
        "bundle": {"id": bundle["id"], "folder": bundle["folder_label"]},
        "classification": {
            "class": unit.doc_class,
            "label": pack.class_label(unit.doc_class),
            "confidence": unit.class_confidence,
            "source": unit.class_source,
            "matched": [m for m in unit.attrs.get("class_matched", "").split(";") if m],
            "needs_fallback": unit.attrs.get("needs_class_fallback") == "true",
        },
        "form": {"serial": unit.form_serial, "revision": unit.form_revision},
        "legibility": {
            "level": str(unit.legibility),
            "reason": unit.attrs.get("legibility_reason"),
        },
        "row_date": _row_date(unit, labels),
        "dates_found": [
            {
                "role": str(f.role),
                "eligible": f.eligible,
                "raw": f.raw,
                "readings": [d.isoformat() for d in f.readings],
                "ambiguous": f.is_ambiguous,
                "century_inferred": f.century_inferred,
                "pdf_index": f.pdf_index,
                "printed_label": labels.get(f.pdf_index),
                "block_id": f.block_id,
                "span": [f.start, f.end],
            }
            for f in unit.dates
        ],
        "records": [
            {
                "field": r.field,
                "value": r.value,
                "stage": r.stage,
                "confidence": r.confidence,
                "derived": r.derived,
                "rule": r.rule,
                "epistemic_tag": r.epistemic_tag,
                "prompt_version": r.prompt_version,
                "model": r.model,
                "block_id": r.block_id,
                "span": [r.span_start, r.span_end] if r.is_cited else None,
            }
            for r in records.for_unit(conn, unit_id)
        ],
        "corrections": corrections.for_subject(conn, "unit", unit_id),
        "audit": audit.for_subject(conn, "unit", unit_id),
    }


def _row_date(unit, labels: dict[int, str | None]) -> dict:
    rd = unit.row_date
    if rd is None:
        return {}
    return {
        "value": rd.value.isoformat() if rd.value else None,
        "rendered": rd.render(),
        "status": str(rd.status),
        "role": str(rd.role) if rd.role else None,
        "rule": rd.rule,
        # The one-line explanation the engine owes for overriding any model output (§8.4).
        "explanation": rd.explanation,
        "alternatives": [d.isoformat() for d in rd.alternatives],
    }


def source_crop(conn: sqlite3.Connection, block_id: str) -> dict:
    """The bbox a checkbox read cites. A crop image is rendered from this by the UI (§4.2)."""
    block = blocks_store.by_id(conn, block_id)
    if block is None:
        raise KeyError(f"unknown block: {block_id}")
    labels = cases.printed_labels(conn, block.bundle_id)
    return {
        "block_id": block.id,
        "bundle_id": block.bundle_id,
        "pdf_index": block.pdf_index,
        "printed_label": labels.get(block.pdf_index),
        "type": str(block.type),
        "text": block.text,
        "bbox": list(block.bbox.as_tuple()),
        "source": str(block.source),
        "confidence": block.confidence,
        "attrs": block.attrs,
    }
