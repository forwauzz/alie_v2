"""Stage 2 — manifest (PRD §4.2, §4.4).

In: blocks. Out: report units. Fails by: wrong boundaries, wrong date. Proven when: units
and dates match the page map (§14.2).

Runs a -> f: boundary detection, orphan re-join, classify, label every date, select the
row date, assess legibility. Nothing is dropped — undated, illegible, excluded and
zero-content units all reach the manifest with a status (§3.4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..manifest import (
    boundaries,
    classify,
    datefind,
    dateselect,
    filters,
    legibility,
    rejoin,
    screen,
)
from ..models import Block, Legibility, ReportUnit, RowStatus, UnitKind
from ..packs import Pack
from ..packs import load as load_pack
from ..provenance import Producer, hash_text
from ..stores import audit, cases, corrections, manifest
from ..stores import blocks as blocks_store


@dataclass(frozen=True)
class ManifestResult:
    bundle_id: str
    units: int
    non_contiguous: int
    rejoined: int
    undated: int
    ambiguous: int
    illegible: int
    unclassified: int
    needs_fallback: int
    #: Units whose regime differs from the case default. The `screen.per_unit_regime`
    #: metric verbatim: is mixed-regime real, or a one-off (§9.2)?
    off_regime: int = 0
    #: Units a filter rule removed from the deliverable. They remain in the manifest with
    #: `excluded_by` naming the rule (§3.4).
    excluded: int = 0
    #: Filter rules the engine could not evaluate because the feature they rest on is not
    #: built. Surfaced rather than silently treated as "did not fire" (§6).
    filters_unavailable: tuple[str, ...] = ()
    #: Sheets absent from a fax transmission. Not a parse failure — a fact about the
    #: bundle the firm was sent, and one a paralegal needs before certifying a chronology
    #: as complete (§3.4).
    missing_sheets: tuple[dict, ...] = ()


def _unit_id(bundle_id: str, pages: list[int]) -> str:
    """Derived from content, so a re-run over unchanged input reproduces the same ids and
    approved rows stay sticky (§10.3, §10.4)."""
    return f"unit_{hash_text(bundle_id + ':' + ','.join(map(str, pages)))[:20]}"


def run(
    conn: sqlite3.Connection,
    bundle_id: str,
    *,
    flags: dict[str, Any],
    run_id: str | None = None,
) -> ManifestResult:
    bundle = cases.get_bundle(conn, bundle_id)
    if bundle is None:
        raise KeyError(f"unknown bundle: {bundle_id}")
    case = cases.get_case(conn, bundle["case_id"])
    pack = load_pack(case["primary_pack"])

    all_blocks = blocks_store.for_bundle(conn, bundle_id)
    page_rows = cases.pages_for_bundle(conn, bundle_id)
    heights = {p["pdf_index"]: p["height"] for p in page_rows}
    labels = {p["pdf_index"]: p["printed_label"] for p in page_rows}

    by_page: dict[int, list[Block]] = {p["pdf_index"]: [] for p in page_rows}
    for b in all_blocks:
        by_page.setdefault(b.pdf_index, []).append(b)

    groups, signals = boundaries.group_pages(by_page, heights, pack)

    gaps = boundaries.transmission_gaps(signals)
    if gaps:
        audit.record(
            conn, subject_type="bundle", subject_id=bundle_id, action="missing_sheets",
            run_id=run_id, rule="transmission.sequence",
            detail={"gaps": gaps, "total": sum(g["missing_sheets"] for g in gaps)},
        )

    rejoined = 0
    if flags.get("manifest.orphan_rejoin"):
        outcome = rejoin.rejoin(groups, signals)
        groups, rejoined = outcome.groups, outcome.changed
        if outcome.merges:
            audit.record(
                conn, subject_type="bundle", subject_id=bundle_id, action="orphan_rejoin",
                run_id=run_id, rule="manifest.orphan_rejoin",
                detail={"merges": [list(m) for m in outcome.merges]},
            )

    anchors = datefind.file_anchors([b.text for b in all_blocks])
    role_for = datefind.RoleResolver(pack.date_roles)
    producer = Producer()

    manifest.delete_units_for_bundle(conn, bundle_id)
    counts = dict(
        undated=0, ambiguous=0, illegible=0, unclassified=0, fallback=0, split=0,
        excluded=0, off_regime=0
    )
    unavailable_filters: set[str] = set()

    for pages in groups:
        unit_blocks = [b for p in pages for b in by_page.get(p, [])]
        unit = _build_unit(
            conn, bundle, case, pack, pages, unit_blocks, signals, labels,
            anchors, role_for, producer, run_id, flags,
        )
        _tally(unit, counts)
        unavailable_filters.update(
            f for f in unit.attrs.get("filters_unavailable", "").split(",") if f
        )

    result = ManifestResult(
        bundle_id=bundle_id,
        units=len(groups),
        non_contiguous=sum(1 for g in groups if g != list(range(g[0], g[-1] + 1))),
        rejoined=rejoined,
        undated=counts["undated"],
        ambiguous=counts["ambiguous"],
        illegible=counts["illegible"],
        unclassified=counts["unclassified"],
        needs_fallback=counts["fallback"],
        missing_sheets=tuple(gaps),
        excluded=counts["excluded"],
        off_regime=counts["off_regime"],
        filters_unavailable=tuple(sorted(unavailable_filters)),
    )
    audit.record(
        conn, subject_type="bundle", subject_id=bundle_id, action="manifest",
        run_id=run_id, rule="stage.manifest",
        detail={
            k: getattr(result, k)
            for k in result.__dataclass_fields__
            if k not in ("bundle_id", "missing_sheets", "filters_unavailable")
        }
        | {
            "missing_sheets": len(result.missing_sheets),
            "filters_unavailable": list(result.filters_unavailable),
        },
    )
    return result


def _build_unit(
    conn, bundle, case, pack: Pack, pages, unit_blocks, signals, labels,
    anchors, role_for, producer, run_id, flags,
) -> ReportUnit:
    unit_id = _unit_id(bundle["id"], pages)
    page_signals = [signals[p] for p in pages if p in signals]
    serial = next((s.serial for s in page_signals if s.serial), None)
    revision = next((s.revision for s in page_signals if s.revision), None)
    author = next((s.author for s in page_signals if s.author), None)

    assessment = legibility.assess(unit_blocks, len(pages))
    classification = classify.classify(unit_blocks, pack, serial=serial)

    facts = [
        f
        for b in unit_blocks
        for f in datefind.find_in_text(
            b.text, block_id=b.id, pdf_index=b.pdf_index, role_for=role_for,
            anchors=anchors, confidence=b.confidence,
        )
    ]
    row_date = dateselect.select(
        facts, doc_class=classification.doc_class, pack=pack, legibility=assessment.level
    )

    # Excluded units still reach the manifest with a status; nothing is dropped (§3.4).
    verdict, unavailable = filters.evaluate(
        unit_blocks, pack,
        doc_class=classification.doc_class, admin_classes=pack.admin_classes,
    )

    # Regime is a property of the unit, not the case (§6.1). Off by default: its flag asks
    # whether mixed-regime files are real or a one-off, and the metric is exactly the count
    # this produces (§9.2).
    regime = case["primary_pack"]
    screened = None
    if flags.get("screen.per_unit_regime"):
        screened = screen.screen(
            unit_id, unit_blocks,
            default_pack=case["primary_pack"], form_serial=serial,
        )
        regime = screened.regime

    unit = ReportUnit(
        id=unit_id,
        bundle_id=bundle["id"],
        case_id=case["id"],
        pages=tuple(pages),
        doc_class=classification.doc_class,
        class_confidence=classification.confidence,
        class_source=classification.source,
        regime=regime,
        legibility=assessment.level,
        author=author,
        form_serial=serial,
        form_revision=revision,
        kind=UnitKind.PRIMARY,
        excluded_by=verdict.rule_id,
        attrs={
            "legibility_reason": assessment.reason,
            "class_matched": ";".join(classification.matched),
            "needs_class_fallback": str(classification.needs_fallback).lower(),
            "unlabelled_pages": ",".join(str(p) for p in pages if not labels.get(p)),
        }
        | ({"excluded_reason": verdict.reason or ""} if verdict.excluded else {})
        | ({"excluded_evidence": verdict.evidence or ""} if verdict.evidence else {})
        # Filters the engine could not judge because the feature they depend on is not
        # built. Recorded per unit so "0 excluded by rule" is never mistaken for "every
        # rule ran and none fired".
        | ({"filters_unavailable": ",".join(unavailable)} if unavailable else {})
        # The override is only useful with its evidence: the why-panel has to show what
        # the unit said to earn a regime other than the case's (§7.1).
        | (
            {
                "regime_source": "screened" if screened.differs else "case_default",
                "regime_confidence": f"{screened.confidence:.2f}",
                "regime_matched": ";".join(screened.matched[:4]),
            }
            if screened
            else {}
        ),
    )
    unit = _apply_corrections(conn, unit, row_date)

    manifest.upsert_unit(conn, unit, producer)
    manifest.replace_dates(conn, unit_id, facts)
    manifest.set_row_date(conn, unit_id, unit.row_date)
    audit.record(
        conn, subject_type="unit", subject_id=unit_id, action="manifest_unit",
        run_id=run_id, rule=unit.row_date.rule,
        detail={
            "pages": list(pages),
            "class": unit.doc_class,
            "class_confidence": classification.confidence,
            "class_source": classification.source,
            "date": unit.row_date.value.isoformat() if unit.row_date.value else None,
            "date_status": str(unit.row_date.status),
            "date_explanation": unit.row_date.explanation,
            "legibility": str(unit.legibility),
            "dates_found": len(facts),
        },
    )
    return unit


def _apply_corrections(conn, unit: ReportUnit, row_date) -> ReportUnit:
    """Corrections write to the manifest and win over anything computed (§10.2)."""
    unit.row_date = row_date
    fixes = corrections.latest_by_field(conn, "unit", unit.id)
    if not fixes:
        return unit

    if "doc_class" in fixes:
        unit.doc_class = fixes["doc_class"]["new_value"]
        unit.class_source = "manual"
        unit.class_confidence = 1.0
    if "regime" in fixes:
        unit.regime = fixes["regime"]["new_value"]
    if "row_date" in fixes:
        from datetime import date as _date

        raw = fixes["row_date"]["new_value"]
        unit.row_date = row_date.__class__(
            value=_date.fromisoformat(raw) if raw else None,
            status=RowStatus.MANUAL,
            role=row_date.role,
            rule="correction.manual",
            explanation=f"Set by {fixes['row_date']['actor']}.",
        )
    return unit


def _tally(unit: ReportUnit, counts: dict[str, int]) -> None:
    status = unit.row_date.status
    if status is RowStatus.UNDATED:
        counts["undated"] += 1
    elif status is RowStatus.AMBIGUOUS:
        counts["ambiguous"] += 1
    if unit.legibility is Legibility.ILLEGIBLE:
        counts["illegible"] += 1
    if unit.doc_class == "unknown":
        counts["unclassified"] += 1
    if unit.attrs.get("needs_class_fallback") == "true":
        counts["fallback"] += 1
    if unit.excluded_by:
        counts["excluded"] += 1
    if unit.attrs.get("regime_source") == "screened":
        counts["off_regime"] += 1
