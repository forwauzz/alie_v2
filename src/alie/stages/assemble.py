"""Stage 5 — assemble, legal only (PRD §4.2, §8.5).

In: records. Out: rows. Fails by: wrong merge/split. Proven when: 2022-12-14 stays three
rows (§14.2).

Merge by encounter, split by study. One clinician in one sitting merges; an independent
study with its own report keeps its own row. Same `(date, author, class)` in two bundles
becomes one row with content unioned and **both locators retained**.

Undated rows lead the document, so they are the first thing reviewed rather than the last
thing discovered. Illegible units still get a row.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..models import (
    Block,
    BlockType,
    Bullet,
    Citation,
    Legibility,
    ReportUnit,
    Row,
    RowStatus,
    Span,
)
from ..packs import Pack
from ..packs import load as load_pack
from ..provenance import hash_text
from ..stores import blocks as blocks_store
from ..stores import cases, manifest
from ..stores.records import Record
from ..stores.records import for_unit as records_for_unit

#: Block types that do not become bullets.
#:
#: Handwriting is excluded because a reviewer's private note must never reach a document
#: destined for opposing counsel (§4.3); stamps belong to the dedupe transmission axis;
#: headings and signature lines are already carried by the row title, which is built from
#: the class label and the author.
#:
#: Nothing is dropped by this: the blocks stay in the block store and every citation still
#: resolves to them. The row is a projection of the manifest, not a replacement for it
#: (§3.1, §3.4).
FURNITURE = frozenset(
    {
        BlockType.PAGE_LABEL,
        BlockType.STAMP,
        BlockType.HANDWRITING,
        BlockType.EMPTY,
        BlockType.HEADING,
        BlockType.SIGNATURE,
    }
)


@dataclass(frozen=True)
class AssembleResult:
    case_id: str
    rows: list[Row]
    merged_encounters: int
    split_studies: int
    cross_bundle_unions: int
    excluded_units: int

    @property
    def undated(self) -> int:
        return sum(1 for r in self.rows if r.is_undated)


def run(conn: sqlite3.Connection, case_id: str, *, run_id: str | None = None) -> AssembleResult:
    case = cases.get_case(conn, case_id)
    pack = load_pack(case["primary_pack"])
    units = manifest.units_for_case(conn, case_id)
    labels = {
        b["id"]: cases.printed_labels(conn, b["id"]) for b in cases.bundles_for_case(conn, case_id)
    }
    toggles = pack.toggles()

    included = [u for u in units if u.excluded_by is None and toggles.get(u.doc_class, True)]
    excluded = len(units) - len(included)

    buckets: dict[tuple, list[ReportUnit]] = {}
    for unit in included:
        buckets.setdefault(_bucket_key(unit, pack), []).append(unit)

    rows: list[Row] = []
    merged = unions = studies = 0
    for group in buckets.values():
        row = _build_row(conn, case_id, group, pack, labels)
        rows.append(row)
        if len(group) > 1:
            merged += 1
            if len({u.bundle_id for u in group}) > 1:
                unions += 1
        if pack.is_diagnostic_study(group[0].doc_class):
            studies += 1

    rows.sort(key=lambda r: (0 if r.is_undated else 1, r.sort_key()))
    return AssembleResult(case_id, rows, merged, studies, unions, excluded)


def _bucket_key(unit: ReportUnit, pack: Pack) -> tuple:
    """Merge by encounter; split by study.

    A diagnostic study keeps its class in the key so it never folds into the encounter
    that ordered it, while still unioning with the same study arriving in a second bundle.
    An undated or illegible unit keys on itself: there is no date to merge on, and
    collapsing them would hide documents behind one another.
    """
    status = unit.row_date.status if unit.row_date else RowStatus.UNDATED
    if status in (RowStatus.UNDATED, RowStatus.ILLEGIBLE) or unit.row_date.value is None:
        return ("solo", unit.id)
    date_value = unit.row_date.value
    if pack.is_diagnostic_study(unit.doc_class):
        return ("study", date_value, unit.author or "", unit.doc_class)
    return ("encounter", date_value, unit.author or "")


def _build_row(
    conn: sqlite3.Connection,
    case_id: str,
    group: list[ReportUnit],
    pack: Pack,
    labels: dict[str, dict[int, str | None]],
) -> Row:
    group = sorted(group, key=lambda u: (u.bundle_id, u.pages))
    lead = group[0]
    row_id = f"row_{hash_text(case_id + '|' + '|'.join(u.id for u in group))[:20]}"

    illegible = any(u.legibility is Legibility.ILLEGIBLE for u in group)
    classes = sorted({pack.class_label(u.doc_class) for u in group})
    author = next((u.author for u in group if u.author), None)

    row = Row(
        id=row_id,
        case_id=case_id,
        row_date=lead.row_date,
        title=_title(pack, classes, author),
        author=author,
        doc_class=lead.doc_class,
        regime=lead.regime,
        unit_ids=[u.id for u in group],
        illegible_reason=(
            lead.attrs.get("legibility_reason") if illegible else None
        ),
        second_hand=all(u.kind.value == "referenced" for u in group),
    )
    row.locators = [
        Citation(
            bundle_id=u.bundle_id,
            pdf_index=u.pages[0] if u.pages else 0,
            printed_label=labels.get(u.bundle_id, {}).get(u.pages[0] if u.pages else 0),
            unit_id=u.id,
        )
        for u in group
    ]
    if not illegible:
        row.bullets = _bullets(conn, group, labels, pack)
    return row


def _title(pack: Pack, classes: list[str], author: str | None) -> str:
    """Code renders the row; the model never writes one (§3.2)."""
    template = pack.output.get("title_line", {})
    label = " + ".join(classes)
    if author:
        suffix = template.get("author_suffix", " — {author}").format(author=author)
        return f"{label}{suffix}"
    return label


def _bullets(
    conn: sqlite3.Connection, group: list[ReportUnit],
    labels: dict[str, dict[int, str | None]], pack: Pack,
) -> list[Bullet]:
    """Selected lines transcribed into the row shape — never a summary (§1.1).

    Every bullet carries the span it came from, so the citation invariant holds by
    construction rather than by later validation.

    Content is **unioned**, not concatenated: the same note arriving in two bundles
    contributes its lines once while both locators are retained on the row (§8.5).
    """
    bullets: list[Bullet] = []
    for unit in group:
        page_labels = labels.get(unit.bundle_id, {})
        structured = [r for r in records_for_unit(conn, unit.id) if not r.derived and r.value]
        blocks = blocks_store.for_pages(conn, unit.bundle_id, unit.pages)
        by_id = {b.id: b for b in blocks}

        for record in structured:
            block = by_id.get(record.block_id or "")
            if block is None:
                continue
            bullets.append(_bullet_from_record(record, block, unit, page_labels, pack))

        cited_blocks = {r.block_id for r in structured}
        for block in blocks:
            if block.type in FURNITURE or block.id in cited_blocks:
                continue
            bullets.append(_bullet_from_block(block, unit, page_labels))
    return _union(bullets)


def _union(bullets: list[Bullet]) -> list[Bullet]:
    """Collapse identical lines, keeping the first citation and the lowest confidence.

    Taking the *minimum* matters: if one bundle's copy is a degraded refax, the row must
    not inherit the cleaner copy's confidence. Confidence rides with the string (§8.7).
    """
    seen: dict[str, int] = {}
    out: list[Bullet] = []
    for bullet in bullets:
        key = " ".join(bullet.text.split()).casefold()
        if key in seen:
            existing = out[seen[key]]
            if bullet.confidence < existing.confidence:
                out[seen[key]] = Bullet(
                    text=existing.text,
                    citation=existing.citation,
                    confidence=bullet.confidence,
                    rule=existing.rule,
                )
            continue
        seen[key] = len(out)
        out.append(bullet)
    return out


def _record_line(record: Record, pack: Pack) -> str:
    """Render a deterministically-read field through the pack's line template.

    Falls back to the field name only when the pack has nothing to say — the storage form
    (`{"code": "204 219", "pct": "2"}`) must never appear in the deliverable.
    """
    template = pack.field_line(record.field)
    raw = record.value or ""
    fields: dict[str, str] = {}
    if raw.startswith("{"):
        try:
            fields = {k: str(v) for k, v in json.loads(raw).items()}
        except json.JSONDecodeError:
            fields = {}

    if template:
        if fields and "prior_pct" in fields:
            template = pack.field_line(f"{record.field.split('.')[0]}_with_prior") or template
        try:
            return template.format(value=raw, **fields)
        except (KeyError, IndexError):
            pass  # a template naming a field this record does not carry
    if fields:
        return ", ".join(f"{k} {v}" for k, v in fields.items())
    return f"{record.field.replace('_', ' ')} : {raw}"


def _bullet_from_record(
    record: Record, block: Block, unit: ReportUnit, page_labels: dict[int, str | None],
    pack: Pack,
) -> Bullet:
    return Bullet(
        text=_record_line(record, pack),
        confidence=record.confidence,
        rule=record.rule,
        citation=Citation(
            bundle_id=unit.bundle_id,
            pdf_index=block.pdf_index,
            printed_label=page_labels.get(block.pdf_index),
            unit_id=unit.id,
            span=Span(block.id, record.span_start or 0, record.span_end or len(block.text)),
        ),
    )


def _bullet_from_block(
    block: Block, unit: ReportUnit, page_labels: dict[int, str | None]
) -> Bullet:
    return Bullet(
        text=block.text,
        confidence=block.confidence,
        rule="assemble.transcribe_line",
        citation=Citation(
            bundle_id=unit.bundle_id,
            pdf_index=block.pdf_index,
            printed_label=page_labels.get(block.pdf_index),
            unit_id=unit.id,
            span=Span(block.id, 0, len(block.text)),
        ),
    )
