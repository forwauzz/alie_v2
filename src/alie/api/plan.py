"""The plan — a request produces a *plan*, not an answer (PRD §4.1).

    142 report units across 4 bundles · regime CNESST, 3 units flagged as a possible SAAQ
    track · 11 duplicate candidates · billing and consent excluded by rule · est. 6 min

The plan is the manifest summary in readable form. It is the first moment a scoping error
can be caught, before cost is spent.
"""

from __future__ import annotations

import sqlite3

from ..models import Legibility, RowStatus
from ..packs import load as load_pack
from ..stores import cases, manifest

#: Rough wall-clock per unit for the deterministic path, used only for the estimate line.
SECONDS_PER_UNIT = 0.4


def build(conn: sqlite3.Connection, case_id: str) -> dict:
    case = cases.get_case(conn, case_id)
    if case is None:
        raise KeyError(f"unknown case: {case_id}")
    pack = load_pack(case["primary_pack"])
    bundles = cases.bundles_for_case(conn, case_id)
    units = manifest.units_for_case(conn, case_id)
    toggles = pack.toggles()

    off_by_toggle = [u for u in units if not toggles.get(u.doc_class, True)]
    excluded = [u for u in units if u.excluded_by]
    other_regime = [u for u in units if u.regime != case["primary_pack"]]

    counts = {
        "undated": sum(1 for u in units if u.row_date and u.row_date.status is RowStatus.UNDATED),
        "ambiguous": sum(
            1 for u in units if u.row_date and u.row_date.status is RowStatus.AMBIGUOUS
        ),
        "illegible": sum(1 for u in units if u.legibility is Legibility.ILLEGIBLE),
        "unclassified": sum(1 for u in units if u.doc_class == pack.unknown_class),
        "non_contiguous": sum(1 for u in units if not u.is_contiguous),
    }

    return {
        "case_id": case_id,
        "case_name": case["name"],
        "pack": pack.id,
        "pack_version": pack.version,
        "bundles": [
            {"id": b["id"], "folder": b["folder_label"], "pages": b["page_count"]}
            for b in bundles
        ],
        "pages": sum(b["page_count"] for b in bundles),
        "units": len(units),
        "units_by_class": _by_class(units, pack),
        "flagged": counts,
        "excluded_by_rule": len(excluded),
        "off_by_toggle": len(off_by_toggle),
        "units_in_other_regime": len(other_regime),
        "estimate_seconds": round(len(units) * SECONDS_PER_UNIT, 1),
        # What this pack states it cannot read yet, and why (§6.2). Surfaced in the plan
        # because the plan is where a scoping error gets caught before cost is approved
        # (§4.1) — and "the pack cannot read consolidation on this regime" is exactly the
        # kind of thing a paralegal must know before, not after.
        "pack_gaps": dict(sorted(pack.pack.get("known_gaps", {}).items())),
        "summary": _summary(case, pack, bundles, units, counts, excluded, off_by_toggle),
    }


def _by_class(units, pack) -> dict[str, int]:
    out: dict[str, int] = {}
    for u in units:
        out[pack.class_label(u.doc_class)] = out.get(pack.class_label(u.doc_class), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _summary(case, pack, bundles, units, counts, excluded, off_by_toggle) -> str:
    parts = [
        f"{len(units)} report units across {len(bundles)} bundle"
        f"{'s' if len(bundles) != 1 else ''}",
        f"regime {pack.id.upper()}",
    ]
    flags = [f"{n} {name}" for name, n in counts.items() if n]
    if flags:
        parts.append(", ".join(flags))
    if excluded or off_by_toggle:
        parts.append(f"{len(excluded) + len(off_by_toggle)} excluded by rule or toggle")
    parts.append(f"est. {round(len(units) * SECONDS_PER_UNIT)} s")
    return " · ".join(parts)
