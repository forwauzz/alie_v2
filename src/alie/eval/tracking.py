"""The MLflow tracking server (PRD §11.1, §13.2).

MLflow is a **recording surface, not a source of truth**. The app owns raw PDFs, answer
keys and prompts; this process owns nothing except the record of what was scored when.
That asymmetry is why the store lives under `var/` next to everything else disposable: if
this directory were deleted, no case data would be lost — only the history of measurements,
which can be recomputed by re-running the golds.

Started with the same discipline as the API (§13.2): fixed port, never auto-increment,
idempotent, and loud about who holds the port when it is not us.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..config import SETTINGS

#: How long to wait for the server to answer before giving up. MLflow's first start builds
#: its schema, which is slower than a health check's patience would otherwise allow.
READY_TIMEOUT = 60.0


@dataclass(frozen=True)
class Tracking:
    port: int
    store_uri: str
    artifacts: Path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def config() -> Tracking:
    """Where the tracking store lives. Under `var/`, with the rest of the disposable state."""
    root = SETTINGS.var_dir / "mlflow"
    return Tracking(
        port=SETTINGS.mlflow_port,
        store_uri=f"sqlite:///{(root / 'mlflow.db').as_posix()}",
        artifacts=root / "artifacts",
    )


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def alive(port: int, timeout: float = 1.0) -> bool:
    """MLflow's own health endpoint. Answering means the schema is built and it will
    accept a run — a bound socket alone does not."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def available() -> bool:
    try:
        import mlflow  # noqa: F401
    except ImportError:
        return False
    return True


def start(*, wait: bool = True) -> tuple[subprocess.Popen | None, Tracking]:
    """Start the tracking server, or report that it is already up.

    Returns `(process, config)`; the process is None when something was already serving,
    which is what makes a second `make dev` a no-op rather than a port collision (§13.2).
    """
    cfg = config()
    if alive(cfg.port):
        return None, cfg

    if not port_free(cfg.port):
        raise RuntimeError(
            f"port {cfg.port} is occupied and is not MLflow. Ports are fixed and never "
            f"auto-increment (§13.2); free it or set ALIE_MLFLOW_PORT."
        )
    if not available():
        raise RuntimeError(
            'MLflow is not installed. `uv pip install -e ".[eval]"` — the harness runs '
            "without it and simply does not log (§11.1)."
        )

    cfg.artifacts.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "mlflow", "server",
            "--host", "127.0.0.1",
            "--port", str(cfg.port),
            "--backend-store-uri", cfg.store_uri,
            # `--default-artifact-root file://…`, not `--artifacts-destination`. The
            # latter turns the server into an artifact *proxy* that clients upload
            # through, and `log_artifact` hung there indefinitely — no error, no timeout,
            # just a sweep that never finished. Everything here is on one machine, so the
            # client writes straight to disk and the server only records where (§13.2).
            "--default-artifact-root", cfg.artifacts.resolve().as_uri(),
            "--no-serve-artifacts",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if wait and not _await_ready(cfg.port, process):
        stop(process)
        raise RuntimeError(
            f"MLflow did not answer on :{cfg.port} within {READY_TIMEOUT:.0f}s. "
            "Readiness is a health check, not a sleep (§13.2)."
        )
    return process, cfg


def stop(process: subprocess.Popen) -> None:
    """Stop the server *and its workers*.

    MLflow serves through waitress with multiprocessing workers, so terminating the parent
    leaves children holding the socket. Those orphans then answer health checks, which
    makes `start()` report "already running" and silently hand back a server built from
    the *previous* configuration — a change to the server arguments appears to have no
    effect, which is a long way to chase.
    """
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        # /T for the tree, /F because waitress workers ignore a polite request.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:  # pragma: no cover - this project is Windows-first
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        process.kill()


def _await_ready(port: int, process: subprocess.Popen) -> bool:
    """The command does not return until the service answers a health check (§13.2)."""
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if alive(port):
            return True
        time.sleep(0.4)
    return False
