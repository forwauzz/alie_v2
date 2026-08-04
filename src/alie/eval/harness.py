"""The eval harness (PRD §11).

Runs a gold end to end through the real pipeline and scores what came out. The app owns
raw PDFs, answer keys and prompts; the harness reads from the app's stores and logs to the
recording surface. A prompt living in both would drift within a week (§11.1).

Four metrics are release-blocking, not diagnostic (§11.3):

    groundedness  = 100%    every model-selected string verified against the source
    uncited       = 0       no string in an export without a citation
    coverage      = 100%    every page accounted for
    truncation    = 0       the failure is silent and fluent

The rest are measurements. A run that fails a must-hold is a failure whatever else it
scored.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .. import flags as flag_registry
from ..devkit import fixtures
from ..packs import load as load_pack
from ..stages import assemble, ingest, manifest_build, parse, structured
from ..stages import fields as fields_stage
from ..stores import audit, cases, manifest, records
from .gold import Gold
from .scoring import Score, StageReport, date_accuracy, matches, page_overlap


@dataclass
class EvalReport:
    gold_id: str
    gold_version: str
    gold_digest: str
    params: dict[str, Any] = field(default_factory=dict)
    stages: list[StageReport] = field(default_factory=list)
    #: The generated chronology, for the row-by-row diff artifact (§11.1).
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return all(s.holds for s in self.stages)

    @property
    def scores(self) -> dict[str, float]:
        return {f"{s.stage}.{sc.name}": sc.value for s in self.stages for sc in s.scores}

    def summary(self) -> str:
        head = f"{self.gold_id} (gold v{self.gold_version} · {self.gold_digest})"
        lines = [head, "=" * len(head)]
        for stage in self.stages:
            lines.append(f"\n{stage.stage}")
            lines.extend(f"  {s}" for s in stage.scores)
            lines.extend(f"    FAIL {f}" for f in stage.failures[:8])
            lines.extend(f"    ADJ  {a}" for a in stage.adjudicate[:8])
            if len(stage.failures) > 8:
                lines.append(f"    ... {len(stage.failures) - 8} more")
        lines.append("\nPASS" if self.holds else "\nFAIL - a must-hold metric did not hold")
        return "\n".join(lines)


def run(
    conn: sqlite3.Connection,
    gold: Gold,
    *,
    flags: dict[str, Any] | None = None,
    fixtures_root: Path | None = None,
) -> EvalReport:
    resolved = flag_registry.resolve(run_flags=flags or {})
    pack_id = "cnesst"
    pack = load_pack(pack_id)

    case_id = cases.create_case(conn, f"eval-{gold.id}", pack_id)
    for folder, filename in gold.bundles.items():
        bundle_id = ingest.add_pdf_path(
            conn,
            case_id=case_id,
            path=fixtures.fixture_path(gold.id, filename, fixtures_root),
            folder_label=folder,
        )
        parse.run(conn, bundle_id, flags=resolved)
        manifest_build.run(conn, bundle_id, flags=resolved)
        for unit in manifest.units_for_bundle(conn, bundle_id):
            structured.run_unit(conn, unit.id)
            fields_stage.run_unit(conn, unit.id)

    result = assemble.run(conn, case_id)
    units = manifest.units_for_case(conn, case_id)

    report = EvalReport(
        gold_id=gold.id,
        gold_version=gold.version,
        gold_digest=gold.digest,
        # Params, not metrics: what produced this number (§11.1). The gold id and digest
        # sit here too, and the raw files never leave the app.
        params={
            "pack": pack_id,
            "pack_version": pack.version,
            "flags": {k: v for k, v in sorted(resolved.items())},
            "gold_id": gold.id,
            "gold_version": gold.version,
            "gold_digest": gold.digest,
            "bundles": len(gold.bundles),
        },
    )
    report.stages.append(_score_manifest(units, gold))
    report.stages.append(_score_fields(conn, units, gold))
    report.stages.append(_score_render(conn, case_id, result))
    report.stages.append(_score_run(conn, case_id))
    report.rows = [
        {
            "date": r.row_date.value.isoformat() if r.row_date.value else None,
            "status": str(r.row_date.status),
            "title": r.title,
            "bullets": [b.text for b in r.bullets],
            "claim_event": r.claim_event.isoformat() if r.claim_event else None,
        }
        for r in result.rows
    ]
    return report


def _match_units(units, gold: Gold):
    """Pair engine units to gold units by page overlap, best first.

    Not by index: a boundary error shifts every later unit, and scoring by position would
    report one mistake as N.
    """
    pairs, used = [], set()
    for want in gold.units:
        best, best_overlap = None, 0.0
        for unit in units:
            if unit.id in used:
                continue
            overlap = page_overlap(unit.pages, want.pages)
            if overlap > best_overlap:
                best, best_overlap = unit, overlap
        if best is not None and best_overlap > 0:
            used.add(best.id)
        pairs.append((want, best, best_overlap))
    return pairs, [u for u in units if u.id not in used]


def _score_manifest(units, gold: Gold) -> StageReport:
    """Unit boundary precision/recall vs the page map; date accuracy by status (§11.3)."""
    stage = StageReport("manifest")
    pairs, spurious = _match_units(units, gold)

    exact_boundary = sum(1 for _w, u, o in pairs if u is not None and o == 1.0)
    stage.scores.append(Score("boundary_exact", exact_boundary, len(gold.units)))

    classified = [(w, u) for w, u, _o in pairs if u is not None and w.doc_class]
    stage.scores.append(
        Score(
            "class_agreement",
            sum(1 for w, u in classified if u.doc_class == w.doc_class),
            len(classified),
        )
    )

    dated = [(w, u) for w, u, _o in pairs if u is not None and w.row_date is not None]
    verdicts = [
        date_accuracy(u.row_date.value if u.row_date else None, w.row_date) for w, u in dated
    ]
    stage.scores.append(Score("date_exact", verdicts.count("exact"), len(dated)))
    stage.scores.append(
        Score("date_within_3d", verdicts.count("exact") + verdicts.count("near"), len(dated))
    )

    excluded = [(w, u) for w, u, _o in pairs if u is not None and w.excluded_by]
    if excluded:
        stage.scores.append(
            Score(
                "exclusion_agreement",
                sum(1 for w, u in excluded if u.excluded_by == w.excluded_by),
                len(excluded),
            )
        )

    for want, unit, overlap in pairs:
        if unit is None:
            stage.failures.append(f"pages {list(want.pages)}: no unit produced")
        elif overlap < 1.0:
            stage.failures.append(
                f"pages {list(want.pages)}: engine produced {list(unit.pages)}"
            )
    for unit in spurious:
        stage.failures.append(f"pages {list(unit.pages)}: unit not in the gold")
    return stage


def _score_fields(conn, units, gold: Gold) -> StageReport:
    """Field recall vs gold (§11.3). Fuzzy, never exact (§11.4)."""
    stage = StageReport("extract")
    pairs, _ = _match_units(units, gold)

    hits = total = 0
    for want, unit, _overlap in pairs:
        if unit is None or not want.cue_fields:
            continue
        got = {r.field: (r.value or "") for r in records.for_unit(conn, unit.id)}
        for name, expected in want.cue_fields.items():
            total += 1
            actual = got.get(name)
            ok = (
                actual is not None
                and (matches(actual, expected) or (expected == "present" and actual != "absent"))
            )
            hits += 1 if ok else 0
            if not ok:
                stage.failures.append(
                    f"pages {list(want.pages)} {name}: want {expected!r}, got {actual!r}"
                )
    stage.scores.append(Score("field_recall", hits, total))
    return stage


def _score_render(conn, case_id: str, result) -> StageReport:
    """Uncited strings and page coverage. Both are must-holds (§11.3)."""
    stage = StageReport("render")

    bullets = [b for r in result.rows for b in r.bullets]
    cited = sum(1 for b in bullets if b.citation and b.citation.span is not None)
    # Uncited = 0. Every string in an export carries a citation; this is a safety
    # invariant, not a flag (§3.5, §9).
    stage.scores.append(Score("cited", cited, len(bullets), must_hold=1.0))
    for bullet in bullets:
        if not (bullet.citation and bullet.citation.span):
            stage.failures.append(f"uncited string: {bullet.text[:70]!r}")

    pages = sum(b["page_count"] for b in cases.bundles_for_case(conn, case_id))
    # Keyed by (bundle, page). A page index is only unique within its bundle — two
    # bundles of three pages each are six pages, not three, and keying on the index alone
    # reported 50% coverage on a case where every page was read.
    covered = {
        (u.bundle_id, p) for u in manifest.units_for_case(conn, case_id) for p in u.pages
    }
    # Coverage = 100%. A page absent from every unit is a page nobody read (§3.4).
    stage.scores.append(Score("page_coverage", len(covered), pages, must_hold=1.0))
    return stage


def _score_run(conn, case_id: str) -> StageReport:
    """Truncation and groundedness, across every stage that called a model (§11.3)."""
    stage = StageReport("all")

    truncated = grounded = selected = calls = 0
    entries = [
        e
        for unit in manifest.units_for_case(conn, case_id)
        for e in audit.for_subject(conn, "unit", unit.id)
    ]
    for entry in entries:
        if entry["action"] != "extract" or entry["detail"].get("skipped"):
            continue
        detail = entry["detail"]
        calls += 1
        if detail.get("stop_reason") not in (None, "end_turn", "stop", "stop_sequence"):
            truncated += 1
        selected += detail.get("selected", 0)
        grounded += detail.get("kept", 0)

    # Truncation = 0. The failure is silent and fluent (§12).
    stage.scores.append(Score("not_truncated", calls - truncated, calls, must_hold=1.0))
    # Groundedness = 100%. Every model-selected span verified against the source (§11.3).
    stage.scores.append(Score("groundedness", grounded, selected, must_hold=1.0))
    return stage


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
