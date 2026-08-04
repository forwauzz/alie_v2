"""Command line entry points (PRD §13.2).

`alie dev` is idempotent: a second run reports "already running" rather than failing on a
port collision, and if some other process holds the port it fails loudly naming the
holder. Ports are fixed and never auto-increment.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import SETTINGS, ensure_dirs
from .devkit import fixtures as fixture_kit


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _health(port: int, timeout: float = 0.7) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _port_holder(port: int) -> str:
    """Best effort: name what is on the port so the failure is actionable."""
    import subprocess

    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown process"
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            return f"pid {line.split()[-1]}"
    return "unknown process"


def cmd_dev(args: argparse.Namespace) -> int:
    port = SETTINGS.api_port
    if not _port_free(port):
        existing = _health(port)
        if existing:
            print(f"already running on :{port} — {existing['status']}, "
                  f"{existing['cases']} case(s), worker={existing['worker_running']}")
            return 0
        print(
            f"port {port} is occupied by {_port_holder(port)} and it is not ALIE.\n"
            f"Ports are fixed and never auto-increment (PRD §13.2). Free it or set "
            f"ALIE_API_PORT.",
            file=sys.stderr,
        )
        return 1

    import uvicorn

    ensure_dirs()
    print(f"alie api    http://127.0.0.1:{port}")
    print(f"logs        {SETTINGS.log_dir / 'alie.log'}")
    uvicorn.run(
        "alie.api.app:app", host="127.0.0.1", port=port, reload=args.reload, log_level="info"
    )
    return 0


def cmd_fixtures(args: argparse.Namespace) -> int:
    root = Path(args.out) if args.out else None
    for path in fixture_kit.build(root):
        print(path)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run one fixture end to end without the server, and print the chronology."""
    from . import flags as flag_registry
    from .api import plan as plan_builder
    from .packs import versions as pack_versions
    from .pipeline import enqueue_case
    from .stages import ingest, render
    from .stores import cases, db, rows, runs
    from .worker import drain

    db.migrate()
    spec = fixture_kit.EXPECTED.get(args.fixture)
    if spec is None:
        print(f"unknown fixture: {args.fixture} (have {', '.join(fixture_kit.EXPECTED)})",
              file=sys.stderr)
        return 1
    fixture_kit.build()

    with db.session() as conn:
        case_id = cases.create_case(conn, f"{args.fixture}-cli", args.pack)
        for folder, filename in spec["bundles"].items():
            ingest.add_pdf_path(
                conn, case_id=case_id, path=fixture_kit.fixture_path(args.fixture, filename),
                folder_label=folder,
            )
        resolved = flag_registry.resolve(
            run_flags={"manifest.orphan_rejoin": True} if args.rejoin else {}
        )
        run_id = runs.create_run(
            conn, case_id=case_id, flags=resolved, pack_versions=pack_versions(),
            plan=plan_builder.build(conn, case_id),
        )
        enqueue_case(conn, run_id, case_id)

    drain()

    with db.read_only() as conn:
        run = runs.get_run(conn, run_id)
        stored = rows.for_run(conn, run_id)
        validation = render.validate(conn, case_id, stored)
        print(f"run {run_id} status={run['status']}")
        print(f"validation {validation.as_dict()}")
        print()
        print(render.to_markdown(conn, case_id, stored))
    return 0 if run["status"] == runs.DONE else 1


def cmd_eval(args: argparse.Namespace) -> int:
    """Score golds end to end (§11). Exit non-zero when a must-hold metric does not hold —
    groundedness, uncited, coverage and truncation are release-blocking, not diagnostic."""
    from . import eval as eval_kit
    from .eval import mlflow_sink
    from .stores import db

    db.migrate()
    fixture_kit.build()

    names = [args.gold] if args.gold else eval_kit.available()
    if not names:
        print("no golds found; run `alie fixtures` first", file=sys.stderr)
        return 1

    flags = {"manifest.orphan_rejoin": True} if args.rejoin else {}
    group = args.group or f"eval-{len(names)}-golds"
    failed = []
    for name in names:
        with db.session() as conn:
            report = eval_kit.run(conn, eval_kit.load(name), flags=flags)
        print(report.summary())
        print()
        mlflow_sink.log(report, run_group=group)
        if not report.holds:
            failed.append(name)

    if failed:
        print(f"must-hold metrics failed: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


def cmd_shadow(args: argparse.Namespace) -> int:
    """Run a candidate beside the incumbent over the same golds (§9.1).

    Reports the comparison. Promotion is a human decision and this command does not make
    it — a candidate that scores better may still be changing what gets processed rather
    than how well (§9.3).
    """
    import json

    from . import eval as eval_kit
    from .stores import db

    db.migrate()
    fixture_kit.build()

    value = json.loads(args.value) if args.value else True
    names = [args.gold] if args.gold else eval_kit.available()
    unsafe = []
    for name in names:
        with db.session() as conn:
            try:
                shadow = eval_kit.compare_flag(
                    conn, eval_kit.load(name), flag=args.flag, candidate=value
                )
            except eval_kit.NotOneVariable as exc:
                print(str(exc), file=sys.stderr)
                return 2
        print(shadow.summary())
        print()
        if not shadow.safe:
            unsafe.append(name)

    if unsafe:
        print(f"candidate breaks a must-hold on: {', '.join(unsafe)}", file=sys.stderr)
    return 1 if unsafe else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alie", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    dev = sub.add_parser("dev", help="start the API (idempotent, fixed port)")
    dev.add_argument("--reload", action="store_true")
    dev.set_defaults(func=cmd_dev)

    fx = sub.add_parser("fixtures", help="regenerate the synthetic fixtures")
    fx.add_argument("--out")
    fx.set_defaults(func=cmd_fixtures)

    run = sub.add_parser("run", help="run a fixture end to end and print the chronology")
    run.add_argument("fixture", choices=sorted(fixture_kit.EXPECTED))
    run.add_argument("--pack", default="cnesst")
    run.add_argument("--rejoin", action="store_true", help="enable manifest.orphan_rejoin")
    run.set_defaults(func=cmd_run)

    ev = sub.add_parser("eval", help="score golds end to end and log to MLflow (§11)")
    ev.add_argument("gold", nargs="?", help="one gold id; omit to score every gold")
    ev.add_argument("--rejoin", action="store_true", help="enable manifest.orphan_rejoin")
    ev.add_argument("--group", help="MLflow run group tag shared by this sweep")
    ev.set_defaults(func=cmd_eval)

    sh = sub.add_parser("shadow", help="run one flag change beside the baseline (§9.1)")
    sh.add_argument("flag", help="the single flag to vary")
    sh.add_argument("--value", help="candidate value as JSON (default: true)")
    sh.add_argument("--gold", help="one gold id; omit to compare across every gold")
    sh.set_defaults(func=cmd_shadow)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
