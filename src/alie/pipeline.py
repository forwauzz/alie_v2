"""Job handlers and the stage DAG (PRD §4.2, §13.4).

Jobs never run inside an HTTP request — they go through the job table even locally. Each
handler is a thin wrapper over a stage, which is itself a pure function over ids, so a
job can be retried or resumed without special-casing (§3.8).

Stages enqueue their own successors rather than the caller scheduling the whole graph up
front: `manifest` cannot know its unit ids until it has run.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .stages import assemble, extract, manifest_build, parse, render, structured
from .stores import audit, cases, manifest, runs
from .stores import records as records_store
from .stores import rows as rows_store

PARSE, MANIFEST, STRUCTURED, ASSEMBLE, RENDER = (
    "parse", "manifest", "structured", "assemble", "render",
)
STAGES = (PARSE, MANIFEST, STRUCTURED, ASSEMBLE, RENDER)


#: Marker on an intake run's plan. Intake parses and manifests so the plan is real before
#: any cost is approved (§4.1); it stops short of assembling a chronology, which is what
#: approval is for.
INTAKE = "intake"


def enqueue_case(conn: sqlite3.Connection, run_id: str, case_id: str) -> int:
    """Seed the graph. Everything downstream is enqueued by the stage before it."""
    bundles = cases.bundles_for_case(conn, case_id)
    for bundle in bundles:
        runs.enqueue(conn, run_id, PARSE, {"bundle_id": bundle["id"], "case_id": case_id})
    return len(bundles)


def intake_run(conn: sqlite3.Connection, case_id: str, bundle_id: str) -> str:
    """Parse starts immediately on upload — the user does not wait for approval to learn
    what is in the file (§4.1).

    Intake resolves the *default* flag set rather than running flagless. An empty dict is
    not "the defaults": it turned `parse.ocr` off, so intake read none of a scanned bundle
    and the plan announced 51 illegible units for a file the pipeline reads fine. The plan
    is where a scoping error gets caught, so it has to describe the run the user is about
    to approve.
    """
    from . import flags as flag_registry

    existing = next(
        (r for r in runs.runs_for_case(conn, case_id) if r["plan"].get(INTAKE)), None
    )
    run_id = existing["id"] if existing else runs.create_run(
        conn,
        case_id=case_id,
        flags=flag_registry.defaults(),
        pack_versions={},
        plan={INTAKE: True},
    )
    runs.enqueue(conn, run_id, PARSE, {"bundle_id": bundle_id, "case_id": case_id})
    return run_id


def is_intake(conn: sqlite3.Connection, run_id: str) -> bool:
    run = runs.get_run(conn, run_id)
    return bool(run and run["plan"].get(INTAKE))


def handle(conn: sqlite3.Connection, job: dict, flags: dict[str, Any]) -> dict:
    stage, payload, run_id = job["stage"], job["payload"], job["run_id"]

    if stage == PARSE:
        result = parse.run(conn, payload["bundle_id"], flags=flags, run_id=run_id)
        runs.enqueue(conn, run_id, MANIFEST, payload)
        return {"blocks": result.blocks, "unparseable": len(result.unparseable_pages)}

    if stage == MANIFEST:
        result = manifest_build.run(conn, payload["bundle_id"], flags=flags, run_id=run_id)
        for unit in manifest.units_for_bundle(conn, payload["bundle_id"]):
            runs.enqueue(
                conn, run_id, STRUCTURED, {"unit_id": unit.id, "case_id": payload["case_id"]}
            )
        return {
            "units": result.units,
            "rejoined": result.rejoined,
            "missing_sheets": sum(g["missing_sheets"] for g in result.missing_sheets),
        }

    if stage == STRUCTURED:
        # 4a runs before 4b, and 4b fills only what remains (§4.2). The flag makes that
        # orderable rather than assumed: with it off, 4b sees an empty unit and does the
        # whole job, which is the comparison its metric asks for — "% fields resolved
        # without the model; cost per unit".
        structured_first = flags.get("extract.structured_first", True)
        detail: dict[str, Any] = {"structured_first": structured_first}
        resolved: frozenset[str] = frozenset()

        if structured_first:
            result = structured.run_unit(conn, payload["unit_id"], run_id=run_id)
            resolved = records_store.fields_for_unit(conn, payload["unit_id"], stage="4a")
            detail |= {"template": result.template, "fields": result.fields_read}

        if flags.get("extract.model"):
            extracted = extract.run_unit(
                conn, payload["unit_id"], run_id=run_id, already_resolved=resolved
            )
            detail |= {
                "extracted": extracted.kept,
                "groundedness": extracted.groundedness,
                "skipped": extracted.skipped,
            }
        return detail

    if stage == ASSEMBLE:
        result = assemble.run(conn, payload["case_id"], run_id=run_id)
        rows_store.replace_for_run(conn, run_id, result.rows)
        runs.enqueue(conn, run_id, RENDER, payload)
        return {
            "rows": len(result.rows),
            "merged": result.merged_encounters,
            "unions": result.cross_bundle_unions,
            "undated": result.undated,
        }

    if stage == RENDER:
        stored = rows_store.for_run(conn, run_id)
        validation = render.validate(conn, payload["case_id"], stored)
        audit.record(
            conn, subject_type="run", subject_id=run_id, action="render",
            run_id=run_id, rule="stage.render", detail=validation.as_dict(),
        )
        if not validation.passes:
            # Release gates, not warnings: uncited = 0 and coverage = 100% (§11.3).
            raise ValueError(
                f"render validation failed: {validation.as_dict()}"
            )
        return validation.as_dict()

    raise ValueError(f"unknown stage: {stage}")


def advance(conn: sqlite3.Connection, run_id: str, case_id: str) -> bool:
    """Enqueue assemble once every unit-level job for the run has settled.

    Cross-bundle union needs the whole case in hand, so this is one of the few genuine
    barriers in the graph (§8.5). Called by the worker *after* a job leaves RUNNING —
    calling it from inside a handler would see that handler's own job still pending and
    the barrier would never fire.
    """
    jobs = runs.jobs_for_run(conn, run_id)
    pending = any(
        j["stage"] in (PARSE, MANIFEST, STRUCTURED) and j["status"] in (runs.QUEUED, runs.RUNNING)
        for j in jobs
    )
    if pending or any(j["stage"] in (ASSEMBLE, RENDER) for j in jobs):
        return False
    runs.enqueue(conn, run_id, ASSEMBLE, {"case_id": case_id})
    return True


def is_finished(conn: sqlite3.Connection, run_id: str) -> bool:
    return all(j["status"] in runs.TERMINAL for j in runs.jobs_for_run(conn, run_id))
