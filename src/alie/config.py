"""Settings and paths. Config and secrets come from the environment, never code (PRD §13.4).

Ports are fixed and never auto-increment. If one is occupied, `make dev` fails loudly
naming the holder rather than silently moving (§13.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> list[str]:
    """Read `.env` into the process environment. Returns the names it set, never values.

    Secrets still come from the environment, never from code (§13.4) — this only spares a
    developer from exporting them by hand in every shell. `.env` is gitignored, and no
    value in it is ever read by ALIE itself: `ANTHROPIC_API_KEY` is consumed by the
    Anthropic SDK, which reads the environment directly.

    **An existing environment variable always wins.** A file on disk must never silently
    override a key exported for one run, or you get a run that used a different credential
    than the one you set and no way to tell from the output.
    """
    env_file = path or (REPO_ROOT / ".env")
    if not env_file.exists():
        return []

    applied: list[str] = []
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name or name in os.environ:
            continue
        os.environ[name] = value.strip().strip('"').strip("'")
        applied.append(name)
    return applied


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
    # Before anything reads the environment, so `.env` can supply ALIE_* paths and ports
    # as well as credentials. Anything already exported still wins.
    load_dotenv()
    var = _env_path("ALIE_VAR_DIR", REPO_ROOT / "var")
    return Settings(
        var_dir=var,
        db_path=_env_path("ALIE_DB_PATH", var / "alie.db"),
        blob_dir=_env_path("ALIE_BLOB_DIR", var / "blobs"),
        log_dir=_env_path("ALIE_LOG_DIR", var / "logs"),
        packs_dir=_env_path("ALIE_PACKS_DIR", REPO_ROOT / "packs"),
        fixtures_dir=_env_path("ALIE_FIXTURES_DIR", REPO_ROOT / "fixtures"),
        # Distinctive rather than conventional. 5173 is the Vite default and 8000 the
        # uvicorn one, so both collide with whatever else is running on the machine —
        # and a fixed port that fails loudly is only useful if it is usually free.
        api_port=_env_int("ALIE_API_PORT", 8471),
        mlflow_port=_env_int("ALIE_MLFLOW_PORT", 5471),
        web_port=_env_int("ALIE_WEB_PORT", 5472),
        actor=os.environ.get("ALIE_ACTOR", "local"),
    )


SETTINGS = load_settings()


def ensure_dirs() -> None:
    for d in (SETTINGS.var_dir, SETTINGS.blob_dir, SETTINGS.log_dir):
        d.mkdir(parents=True, exist_ok=True)
