"""MLflow recording surface (PRD §11.1).

MLflow is a **recording surface, not a source of truth**. The app owns raw PDFs, answer
keys and prompts; a prompt living in both would drift within a week.

**Raw PDFs and answer keys are never logged.** The gold id and its content hash prove which
key a run scored against without duplicating patient files into a second store (§11.1,
§16). This module refuses to write a file path from the case store for that reason.

Absent MLflow is not an error. The harness's numbers are the product; logging them
elsewhere is convenience, and a machine without the optional extra still gets a full
report.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..config import SETTINGS
from .harness import EvalReport

log_ = logging.getLogger("alie.eval")

#: Seconds any single tracking call may take before it is abandoned.
HTTP_TIMEOUT = 20

#: Why the last log attempt failed, or None. Read by the CLI so a sweep that recorded
#: nothing says why rather than just reporting a count of zero.
last_error: str | None = None


def available() -> bool:
    """Whether a run *can* be logged: the library is installed and the server answers.

    Both halves matter. MLflow being importable says nothing about whether anything is
    listening, and a sink that blocks `make eval` because a recording surface is down has
    inverted the relationship — the app is the source of truth, MLflow is the record
    (§11.1).
    """
    from . import tracking

    return tracking.available() and tracking.alive(SETTINGS.mlflow_port)


def log(report: EvalReport, *, run_group: str | None = None) -> bool:
    """Log one gold's run. Returns False when there is nowhere to log to.

    One MLflow run per fixture, tagged with a shared run group (§11.1). Artifacts are
    written to disk either way, so a run scored while the server was down is not lost —
    it can be logged later, or simply read where it lies.
    """
    global last_error

    artifacts = _write_artifacts(report)
    if not available():
        last_error = "no tracking server is answering"
        return False

    # A bounded wait on every HTTP call the client makes. `log_artifact` once hung with no
    # error and no timeout, and a sweep that never finishes is worse than one that fails:
    # the failure is visible, the hang looks like slowness. The recording surface gets a
    # deadline because the measurement is the product (§11.1).
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", str(HTTP_TIMEOUT))
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "2")

    import mlflow

    mlflow.set_tracking_uri(f"http://127.0.0.1:{SETTINGS.mlflow_port}")
    try:
        mlflow.set_experiment(experiment_id=_experiment_id(mlflow))
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        log_.warning("mlflow experiment setup failed: %s", last_error)
        return False

    try:
        with mlflow.start_run(run_name=f"{report.gold_id}@v{report.gold_version}"):
            if run_group:
                mlflow.set_tag("run_group", run_group)
            mlflow.set_tag("holds", str(report.holds))
            mlflow.log_params(_flatten(report.params))
            mlflow.log_metrics(report.scores)
            for path in artifacts:
                mlflow.log_artifact(str(path))
    except Exception as exc:
        # The scores are the product; logging them elsewhere is convenience, so a tracking
        # server that dies mid-sweep must not take the measurement with it. But swallowing
        # the reason makes "logged nothing" indistinguishable from "logged everything but
        # returned False" — the caller gets the reason, and it reaches the log file.
        last_error = f"{type(exc).__name__}: {exc}"
        log_.warning("mlflow logging failed for %s: %s", report.gold_id, last_error)
        return False
    last_error = None
    return True


#: The experiment's name. Its *artifact location* is fixed at creation and cannot be
#: changed afterwards, which is why it is set here rather than left to a server flag.
EXPERIMENT = "alie"


def _experiment_id(mlflow: Any) -> str:
    """Get or create the experiment with an explicit filesystem artifact location.

    MLflow's default is `mlflow-artifacts:/<id>` — a *proxied* location the client uploads
    through the tracking server. That path returned 500s here and, before a timeout was
    added, hung outright.

    Everything runs on one machine, so the artifacts belong on the filesystem and the
    server only needs to record where they are. Artifact location is immutable once the
    experiment exists, so it has to be right at creation; an experiment created by an
    earlier server config keeps its old proxied root forever.
    """
    from . import tracking

    existing = mlflow.get_experiment_by_name(EXPERIMENT)
    if existing is not None:
        return existing.experiment_id
    root = tracking.config().artifacts
    root.mkdir(parents=True, exist_ok=True)
    return mlflow.create_experiment(EXPERIMENT, artifact_location=root.resolve().as_uri())


def _flatten(params: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, dict):
            out.update({f"{key}.{k}": str(v) for k, v in value.items()})
        else:
            out[key] = str(value)
    return out


def _write_artifacts(report: EvalReport) -> list[Path]:
    """The generated chronology, the failure list with locators, and the resolved prompt
    text — the actual text, not a pointer. In six months `v12` may not be reconstructible
    (§11.1)."""
    out_dir = SETTINGS.var_dir / "eval" / report.gold_id
    out_dir.mkdir(parents=True, exist_ok=True)

    chronology = out_dir / "chronology.json"
    chronology.write_text(
        json.dumps(report.rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    failures = out_dir / "failures.txt"
    failures.write_text(
        "\n".join(
            f"[{stage.stage}] {line}"
            for stage in report.stages
            for line in (*stage.failures, *(f"ADJUDICATE {a}" for a in stage.adjudicate))
        )
        or "none\n",
        encoding="utf-8",
    )

    prompts = out_dir / "prompts.txt"
    prompts.write_text(_prompt_snapshot(), encoding="utf-8")
    return [chronology, failures, prompts]


def _prompt_snapshot() -> str:
    """Every registered prompt's resolved text, verbatim."""
    from ..packs import load as load_pack
    from ..packs.prompts import available as available_prompts
    from ..packs.prompts import resolve

    pack = load_pack("cnesst")
    chunks = []
    for prompt_id, versions in sorted(available_prompts(pack).items()):
        for version in versions:
            prompt = resolve(pack, prompt_id, doc_class="*", version=version)
            chunks.append(
                f"===== {prompt.ref} =====\n--- system ---\n{prompt.system}\n"
                f"--- user ---\n{prompt.user}\n"
            )
    return "\n".join(chunks)
