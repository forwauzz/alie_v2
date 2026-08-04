"""Settings and paths. Config and secrets come from the environment, never code (PRD §13.4).

Ports are fixed and never auto-increment. If one is occupied, `make dev` fails loudly
naming the holder rather than silently moving (§13.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).resolve() if raw else default


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # Local state. Everything under here is disposable; `make dev` recreates it.
    var_dir: Path
    db_path: Path
    blob_dir: Path
    log_dir: Path

    # Repo content.
    packs_dir: Path
    fixtures_dir: Path

    # Fixed ports.
    api_port: int
    mlflow_port: int
    web_port: int

    # The single implicit actor. No auth, no user table (§13.2); the field exists so
    # multi-user becomes a value change rather than a migration (§16).
    actor: str

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"


def load_settings() -> Settings:
    var = _env_path("ALIE_VAR_DIR", REPO_ROOT / "var")
    return Settings(
        var_dir=var,
        db_path=_env_path("ALIE_DB_PATH", var / "alie.db"),
        blob_dir=_env_path("ALIE_BLOB_DIR", var / "blobs"),
        log_dir=_env_path("ALIE_LOG_DIR", var / "logs"),
        packs_dir=_env_path("ALIE_PACKS_DIR", REPO_ROOT / "packs"),
        fixtures_dir=_env_path("ALIE_FIXTURES_DIR", REPO_ROOT / "fixtures"),
        api_port=_env_int("ALIE_API_PORT", 8471),
        mlflow_port=_env_int("ALIE_MLFLOW_PORT", 5471),
        web_port=_env_int("ALIE_WEB_PORT", 5173),
        actor=os.environ.get("ALIE_ACTOR", "local"),
    )


SETTINGS = load_settings()


def ensure_dirs() -> None:
    for d in (SETTINGS.var_dir, SETTINGS.blob_dir, SETTINGS.log_dir):
        d.mkdir(parents=True, exist_ok=True)
