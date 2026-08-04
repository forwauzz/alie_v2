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
from pathlib import Path
from typing import Any

from ..config import SETTINGS
from .harness import EvalReport


def available() -> bool:
    try:
        import mlflow  # noqa: F401
    except ImportError:
        return False
    return True


def log(report: EvalReport, *, run_group: str | None = None) -> bool:
    """Log one gold's run. Returns False when MLflow is not installed.

    One MLflow run per fixture, tagged with a shared run group (§11.1).
    """
    artifacts = _write_artifacts(report)
    if not available():
        return False

    import mlflow

    mlflow.set_tracking_uri(f"http://127.0.0.1:{SETTINGS.mlflow_port}")
    mlflow.set_experiment("alie")
    with mlflow.start_run(run_name=f"{report.gold_id}@v{report.gold_version}"):
        if run_group:
            mlflow.set_tag("run_group", run_group)
        mlflow.set_tag("holds", str(report.holds))
        mlflow.log_params(_flatten(report.params))
        mlflow.log_metrics(report.scores)
        for path in artifacts:
            mlflow.log_artifact(str(path))
    return True


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
