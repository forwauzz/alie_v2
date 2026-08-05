"""FastAPI surface (PRD §4.5, §13.2).

No auth, no login, no user table. One implicit actor. Jobs never run inside a request —
every route that starts work enqueues it and returns (§13.4).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import flags as flag_registry
from ..config import SETTINGS, ensure_dirs
from ..devkit import fixtures as fixture_kit
from ..packs import available as available_packs
from ..packs import versions as pack_versions
from ..pipeline import enqueue_case, intake_run
from ..stages import ingest, render
from ..stores import audit, cases, corrections, db, manifest, rows, runs
from ..stores import blocks as blocks_store
from ..worker import BackgroundWorker
from . import plan as plan_builder
from . import why as why_panel

log = logging.getLogger("alie.api")
worker = BackgroundWorker()


def _configure_logging() -> None:
    """Logs to a file, not only stdout (§13.2)."""
    ensure_dirs()
    handler = logging.FileHandler(SETTINGS.log_dir / "alie.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    db.migrate()
    _seed_if_empty()
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="ALIE", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{SETTINGS.web_port}", f"http://127.0.0.1:{SETTINGS.web_port}"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _seed_if_empty() -> None:
    """Fresh clone to testable app in one step (§13.2)."""
    with db.session() as conn:
        if not db.is_empty(conn):
            return
    fixture_kit.build()
    with db.session() as conn:
        for name, spec in fixture_kit.EXPECTED.items():
            case_id = cases.create_case(conn, name, "cnesst")
            for folder, filename in spec["bundles"].items():
                path = fixture_kit.fixture_path(name, filename)
                if path.exists():
                    bundle_id = ingest.add_pdf_path(
                        conn, case_id=case_id, path=path, folder_label=folder
                    )
                    intake_run(conn, case_id, bundle_id)
    log.info("seeded fixtures: %s", ", ".join(fixture_kit.EXPECTED))


# --------------------------------------------------------------------------- health


@app.get("/health")
def health() -> dict:
    """Deterministic readiness: `make dev` does not return until this answers (§13.2)."""
    with db.read_only() as conn:
        case_count = len(cases.list_cases(conn))
    return {
        "status": "ok",
        "version": app.version,
        "worker_running": worker.running,
        "cases": case_count,
        "packs": pack_versions(),
    }


# ----------------------------------------------------------------------------- cases


class CaseCreate(BaseModel):
    name: str
    pack: str = "cnesst"


@app.get("/cases")
def list_cases() -> list[dict]:
    with db.read_only() as conn:
        return cases.list_cases(conn)


@app.post("/cases", status_code=201)
def create_case(body: CaseCreate) -> dict:
    if body.pack not in available_packs():
        raise HTTPException(400, f"unknown pack: {body.pack}")
    with db.session() as conn:
        case_id = cases.create_case(conn, body.name, body.pack)
        return cases.get_case(conn, case_id)


@app.post("/cases/{case_id}/bundles", status_code=201)
async def upload_bundle(
    case_id: str,
    file: UploadFile = File(...),  # noqa: B008 — FastAPI declares dependencies this way
    folder_label: str = Form(...),  # noqa: B008
) -> dict:
    data = await file.read()
    with db.session() as conn:
        if cases.get_case(conn, case_id) is None:
            raise HTTPException(404, f"unknown case: {case_id}")
        bundle_id = ingest.add_pdf(
            conn, case_id=case_id, data=data, filename=file.filename or "upload.pdf",
            folder_label=folder_label,
        )
        # Parse starts immediately; the user does not wait for approval to learn what is
        # in the file (§4.1).
        intake_run(conn, case_id, bundle_id)
        return cases.get_bundle(conn, bundle_id)


@app.get("/cases/{case_id}/plan")
def get_plan(case_id: str) -> dict:
    with db.read_only() as conn:
        try:
            return plan_builder.build(conn, case_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.get("/cases/{case_id}/units")
def list_units(case_id: str) -> list[dict]:
    with db.read_only() as conn:
        return [
            {
                "id": u.id,
                "bundle_id": u.bundle_id,
                "pages": list(u.pages),
                "contiguous": u.is_contiguous,
                "doc_class": u.doc_class,
                "class_confidence": u.class_confidence,
                "class_source": u.class_source,
                "regime": u.regime,
                "legibility": str(u.legibility),
                "author": u.author,
                "form_serial": u.form_serial,
                "form_revision": u.form_revision,
                "row_date": (
                    u.row_date.value.isoformat()
                    if u.row_date and u.row_date.value
                    else None
                ),
                "date_status": str(u.row_date.status) if u.row_date else None,
            }
            for u in manifest.units_for_case(conn, case_id)
        ]


# ------------------------------------------------------------------------------ runs


class RunCreate(BaseModel):
    flags: dict[str, Any] = {}


@app.post("/cases/{case_id}/runs", status_code=201)
def create_run(case_id: str, body: RunCreate) -> dict:
    """Approval converts a plan into a job (§4.1). A run is immutable and records its
    resolved flag set; changing a flag creates a new run (§9 rule 3)."""
    with db.session() as conn:
        case = cases.get_case(conn, case_id)
        if case is None:
            raise HTTPException(404, f"unknown case: {case_id}")
        try:
            resolved = flag_registry.resolve(run_flags=body.flags)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc

        run_id = runs.create_run(
            conn, case_id=case_id, flags=resolved, pack_versions=pack_versions(),
            plan=plan_builder.build(conn, case_id),
        )
        superseded = runs.supersede_unfinished(conn, case_id, run_id)
        bundles = enqueue_case(conn, run_id, case_id)
        rerun_flags = flag_registry.output_affecting(resolved)
        audit.record(
            conn, subject_type="run", subject_id=run_id, action="approve", run_id=run_id,
            rule="run.create",
            detail={
                "flags": resolved, "bundles": bundles, "output_affecting": rerun_flags,
                "superseded_runs": superseded,
            },
        )
        return runs.get_run(conn, run_id) | {
            "output_affecting_flags": rerun_flags,
            "superseded_runs": superseded,
        }


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    with db.read_only() as conn:
        run = runs.get_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        return run | {
            "stage_progress": runs.stage_progress(conn, run_id),
            "jobs": runs.jobs_for_run(conn, run_id),
        }


@app.get("/runs/{run_id}/rows")
def get_rows(run_id: str) -> dict:
    with db.read_only() as conn:
        run = runs.get_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        stored = rows.for_run(conn, run_id)
        return {
            "run_id": run_id,
            "rows": render.to_json(conn, run["case_id"], stored),
            "validation": render.validate(conn, run["case_id"], stored).as_dict(),
        }


@app.get("/runs/{run_id}/export.md", response_class=PlainTextResponse)
def export_markdown(run_id: str) -> str:
    with db.read_only() as conn:
        run = runs.get_run(conn, run_id)
        if run is None:
            raise HTTPException(404, f"unknown run: {run_id}")
        return render.to_markdown(conn, run["case_id"], rows.for_run(conn, run_id))


@app.get("/runs/{run_id}/audit")
def get_audit(run_id: str) -> list[dict]:
    with db.read_only() as conn:
        return audit.for_run(conn, run_id)


@app.get("/runs/{run_id}/product")
def get_product_metrics(run_id: str) -> dict:
    """Whether the firm keeps using the thing (§11.5).

    Extraction metrics cannot see this failure: a system can score perfectly on
    groundedness while flagging so much that nobody reads the flags, which silently
    disables the main safety mechanism.
    """
    from ..eval import product

    with db.read_only() as conn:
        try:
            metrics = product.for_run(conn, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "case_id": metrics.case_id,
        "rows": metrics.rows,
        "accepted_unedited": metrics.accepted_unedited,
        "accept_rate": metrics.accept_rate,
        "reviewed": metrics.reviewed,
        "flagged": metrics.flag_precision.flagged,
        "flag_precision": metrics.flag_precision.precision,
        "flag_recall": metrics.flag_precision.recall,
        "corrections_not_flagged": metrics.flag_precision.missed,
        "seconds_to_draft": metrics.seconds_to_draft,
        "summary": metrics.summary(),
    }


# ------------------------------------------------------------- review and corrections


@app.get("/units/{unit_id}/why")
def get_why(unit_id: str) -> dict:
    with db.read_only() as conn:
        try:
            return why_panel.for_unit(conn, unit_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.get("/blocks/{block_id}")
def get_block(block_id: str) -> dict:
    with db.read_only() as conn:
        try:
            return why_panel.source_crop(conn, block_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc


class CorrectionIn(BaseModel):
    subject_type: str = "unit"
    subject_id: str
    field: str
    new_value: str | None
    old_value: str | None = None
    rule: str | None = None
    block_id: str | None = None
    span: tuple[int, int] | None = None


@app.post("/corrections", status_code=201)
def post_correction(body: CorrectionIn) -> dict:
    """Corrections write to the manifest, not the output (§10.2). The chronology
    regenerates; editing the output directly would be discarded by the next re-run."""
    with db.session() as conn:
        unit = manifest.get_unit(conn, body.subject_id) if body.subject_type == "unit" else None
        if body.subject_type == "unit" and unit is None:
            raise HTTPException(404, f"unknown unit: {body.subject_id}")
        case_id = unit.case_id if unit else ""
        correction_id = corrections.apply(
            conn, case_id=case_id, subject_type=body.subject_type, subject_id=body.subject_id,
            field=body.field, new_value=body.new_value, old_value=body.old_value,
            rule=body.rule, block_id=body.block_id, span=body.span,
        )
        return {"id": correction_id, "case_id": case_id, "requires_rerun": True}


# ----------------------------------------------------------------------------- flags


@app.get("/flags")
def get_flags() -> dict:
    """Everything in the register is built. Unproven features ship off, each paired with
    the metric that decides whether it earns its place (§9.2)."""
    return {
        "flags": [
            {
                "id": f.id,
                "kind": str(f.kind),
                "default": f.default,
                "question": f.question,
                "metric": f.metric,
                "requires_rerun": f.needs_rerun,
            }
            for f in flag_registry.REGISTER
        ],
        # Read-only. Disabling one produces no data point, only a bad chronology (§9.3).
        "safety_invariants": list(flag_registry.SAFETY_INVARIANTS),
    }


# ------------------------------------------------------------------------------- dev


@app.post("/dev/reset")
def dev_reset() -> dict:
    """Restores known state (§13.2)."""
    with db.session() as conn:
        db.reset(conn)
    _seed_if_empty()
    with db.read_only() as conn:
        return {"reset": True, "cases": cases.list_cases(conn)}


@app.get("/dev/state")
def dev_state() -> dict:
    """Job status, stage progress and counts as JSON, so QA asserts on facts instead of
    scraping a progress bar or sleeping (§13.2)."""
    with db.read_only() as conn:
        case_rows = cases.list_cases(conn)
        out = []
        for case in case_rows:
            case_runs = runs.runs_for_case(conn, case["id"])
            bundles = cases.bundles_for_case(conn, case["id"])
            units = manifest.units_for_case(conn, case["id"])
            out.append(
                {
                    "case_id": case["id"],
                    "name": case["name"],
                    "pack": case["primary_pack"],
                    "bundles": len(bundles),
                    "pages": sum(b["page_count"] for b in bundles),
                    "blocks": sum(blocks_store.count_for_bundle(conn, b["id"]) for b in bundles),
                    "units": len(units),
                    "corrections": len(corrections.for_case(conn, case["id"])),
                    "runs": [
                        {
                            "id": r["id"],
                            "status": r["status"],
                            "stage_progress": runs.stage_progress(conn, r["id"]),
                            "rows": len(rows.for_run(conn, r["id"])),
                        }
                        for r in case_runs
                    ],
                }
            )
        return {"worker_running": worker.running, "actor": SETTINGS.actor, "cases": out}


@app.get("/dev/fixtures")
def dev_fixtures() -> dict:
    return {
        "root": str(SETTINGS.fixtures_dir),
        "expected": fixture_kit.EXPECTED,
        "files": sorted(
            str(Path(p).relative_to(SETTINGS.fixtures_dir))
            for p in SETTINGS.fixtures_dir.rglob("*")
            if Path(p).is_file()
        ),
    }
