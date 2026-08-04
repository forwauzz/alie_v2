"""Stage 4a — structured read: boxes, tables, sections (PRD §4.2).

In: blocks. Out: structured fields. Fails by: wrong template revision. Proven when:
checkbox agreement on templated forms (§14.2).

4a runs before 4b, and 4b fills only what remains. Three consequences: cost collapses on
the highest-value, highest-frequency, most template-stable class; a checkbox read cites a
bounding box and a crop image; and a conflict detector appears free — when 4a says
`[x] Non` and 4b extracts "atteinte permanente reconnue", that is a high-signal flag
neither path catches alone.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..models import Block, BlockType, ReportUnit
from ..packs import Pack, Template, UnknownRevision, lookup
from ..packs import load as load_pack
from ..provenance import Producer
from ..stores import audit, cases, manifest
from ..stores import blocks as blocks_store
from ..stores.records import Record, replace_for_unit


@dataclass(frozen=True)
class StructuredResult:
    unit_id: str
    template: str | None
    fields_read: int
    fell_back: bool
    fallback_reason: str | None


def run_unit(
    conn: sqlite3.Connection, unit_id: str, *, run_id: str | None = None
) -> StructuredResult:
    unit = manifest.get_unit(conn, unit_id)
    if unit is None:
        raise KeyError(f"unknown unit: {unit_id}")
    case = cases.get_case(conn, unit.case_id)
    pack = load_pack(unit.regime or case["primary_pack"])

    try:
        template = lookup(pack, unit.form_serial, unit.form_revision)
        reason = None
    except UnknownRevision as exc:
        template, reason = None, str(exc)

    if template is None:
        replace_for_unit(conn, unit_id, "4a", [], Producer())
        if reason:
            audit.record(
                conn, subject_type="unit", subject_id=unit_id, action="template_fallback",
                run_id=run_id, rule="parse.templates", detail={"reason": reason},
            )
        return StructuredResult(unit_id, None, 0, fell_back=True, fallback_reason=reason)

    unit_blocks = blocks_store.for_pages(conn, unit.bundle_id, unit.pages)
    records = read_fields(unit, unit_blocks, template, pack)
    replace_for_unit(conn, unit_id, "4a", records, Producer(extra={"template": template.key}))

    audit.record(
        conn, subject_type="unit", subject_id=unit_id, action="structured_read",
        run_id=run_id, rule=f"template.{template.key}", epistemic_tag=template.tag,
        detail={"template": template.key, "fields": [r.field for r in records]},
    )
    return StructuredResult(unit_id, template.key, len(records), False, None)


def read_fields(
    unit: ReportUnit, blocks: list[Block], template: Template, pack: Pack
) -> list[Record]:
    records: list[Record] = []
    for spec in template.fields:
        kind = spec.get("kind")
        if kind == "checkbox":
            records.extend(_read_checkbox(unit, blocks, spec, template))
        elif kind == "table_rows":
            records.extend(_read_table_rows(unit, blocks, spec, template))
        elif kind == "section":
            records.extend(_read_section(unit, blocks, spec, template))
    return records


def _record(
    unit: ReportUnit, spec: dict, template: Template, field: str, value: str | None,
    block: Block | None, span: tuple[int, int] | None, confidence: float,
    *, derived: bool = False,
) -> Record:
    return Record(
        unit_id=unit.id,
        field=field,
        value=value,
        stage="4a",
        confidence=confidence,
        block_id=block.id if block else None,
        span_start=span[0] if span else None,
        span_end=span[1] if span else None,
        derived=derived,
        rule=f"template.{template.key}.{spec['id']}",
        epistemic_tag=spec.get("tag", template.tag),
    )


def _read_checkbox(
    unit: ReportUnit, blocks: list[Block], spec: dict, template: Template
) -> list[Record]:
    anchor = re.compile(spec["anchor"], re.IGNORECASE)
    candidates = [b for b in blocks if b.type is BlockType.CHECKBOX]

    # A checkbox belongs to the nearest anchor above or on its own line.
    for block in candidates:
        if anchor.search(block.text) or _anchor_above(blocks, block, anchor):
            for state, pattern in spec.get("states", {}).items():
                if m := re.search(pattern, block.text):
                    return [
                        _record(unit, spec, template, spec["id"], state, block,
                                (m.start(), m.end()), block.confidence)
                    ]
            # Field present, no box ticked. Not the same as the field being absent.
            return [
                _record(unit, spec, template, spec["id"],
                        spec.get("blank_state", "trop_tot"), block, (0, len(block.text)),
                        block.confidence)
            ]

    # The field is absent from the document. There is no text to cite — that is the
    # finding — so this is derived, not an uncited transcription.
    return [
        _record(unit, spec, template, spec["id"], spec.get("absent_state", "absent"),
                None, None, 1.0, derived=True)
    ]


def _anchor_above(blocks: list[Block], block: Block, anchor: re.Pattern[str]) -> bool:
    """The nearest preceding block on the same page carrying the anchor text."""
    same_page = [b for b in blocks if b.pdf_index == block.pdf_index and b.order < block.order]
    for b in reversed(same_page):
        if anchor.search(b.text):
            return True
        if b.type is BlockType.HEADING:
            break  # a new section began; stop looking
    return False


def _read_table_rows(
    unit: ReportUnit, blocks: list[Block], spec: dict, template: Template
) -> list[Record]:
    rows = _blocks_under_heading(blocks, spec["under_heading"])
    row_pattern = re.compile(spec["row"], re.IGNORECASE)
    prior_pattern = re.compile(spec["prior_pct"], re.IGNORECASE) if spec.get("prior_pct") else None

    records: list[Record] = []
    matched = 0
    for block in rows:
        m = row_pattern.search(block.text)
        if not m:
            continue
        matched += 1
        code = re.sub(r"\s+", " ", m.group("code")).strip()
        value: dict[str, Any] = {"code": code, "pct": m.group("pct")}
        if prior_pattern and (pm := prior_pattern.search(block.text)):
            value["prior_pct"] = pm.group("prior")
        # JSON rather than a flat string: barème codes are stored individually with their
        # own percentages (§8.6), and the pack's line template addresses them by name.
        records.append(
            _record(unit, spec, template, f"{spec['id']}.{matched}",
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    block, (m.start(), m.end()), block.confidence)
        )

    if spec.get("emit_expected_count"):
        # The count that makes silent output truncation detectable downstream (§12).
        records.append(
            _record(unit, spec, template, f"{spec['id']}.expected_count", str(matched),
                    None, None, 1.0, derived=True)
        )
    return records


def _read_section(
    unit: ReportUnit, blocks: list[Block], spec: dict, template: Template
) -> list[Record]:
    body = _blocks_under_heading(blocks, spec["under_heading"])[: spec.get("max_lines", 4)]
    body = [b for b in body if b.is_body_text]
    if not body:
        return []
    first = body[0]
    text = " ".join(b.text for b in body)
    return [
        _record(unit, spec, template, spec["id"], text, first, (0, len(first.text)),
                min(b.confidence for b in body))
    ]


def _blocks_under_heading(blocks: list[Block], heading_pattern: str) -> list[Block]:
    """Blocks following a matching heading, up to the next heading of the same or higher
    level. Section boundaries are the document's own structure, never a character count
    (§12)."""
    pattern = re.compile(heading_pattern, re.IGNORECASE)
    out: list[Block] = []
    collecting = False
    start_level = "9"
    for b in blocks:
        if b.type is BlockType.HEADING:
            level = b.attrs.get("level", "2")
            if collecting and level <= start_level:
                break
            if pattern.search(b.text):
                collecting, start_level = True, level
                continue
        if collecting:
            out.append(b)
    return out
