"""Gold fixtures (PRD §11.1, §11.4).

A gold is an answer key with a **version**. ~30 of the 74 CNESST rows were never
reverse-engineered and some `[PROP]` divergences are deliberate, so golds get corrected —
and a metric jump from editing the answer key must be distinguishable from one from
improving the system. Every scored run records which gold version it ran against.

Raw PDFs and answer keys are never logged to the recording surface. A content hash proves
which gold a run scored against without duplicating patient files into a second store
(§11.1, §16).

One gold per regime. The 74-row CNESST key is one fixture, not a universal key (§11.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import SETTINGS
from ..provenance import hash_text


@dataclass(frozen=True)
class GoldUnit:
    pages: tuple[int, ...]
    doc_class: str | None = None
    row_date: str | None = None
    status: str | None = None
    excluded_by: str | None = None
    cue_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Gold:
    id: str
    version: str
    #: Content hash of the key itself. Proves which gold was scored against without
    #: copying patient files anywhere (§11.1).
    digest: str
    bundles: dict[str, str]
    units: tuple[GoldUnit, ...]
    printed_labels: dict[int, str] = field(default_factory=dict)
    #: Rows the key marks as deliberately divergent. These go to human adjudication, never
    #: automatic failure — a metric that punishes correct-but-different trains the system
    #: to reproduce the answer key's errors (§11.4).
    proposed: tuple[int, ...] = ()
    #: Rows the key never reverse-engineered. Scored separately so an unresolved gold does
    #: not read as a system failure.
    unresolved: tuple[int, ...] = ()

    @property
    def scored_units(self) -> tuple[GoldUnit, ...]:
        return self.units


def _to_unit(raw: dict[str, Any]) -> GoldUnit:
    return GoldUnit(
        pages=tuple(raw.get("pages", ())),
        doc_class=raw.get("class"),
        row_date=raw.get("row_date"),
        status=raw.get("status"),
        excluded_by=raw.get("excluded_by"),
        cue_fields=raw.get("cue_fields", {}),
    )


def load(name: str, root: Path | None = None) -> Gold:
    """Read a gold from the fixtures tree.

    `fixtures/<name>/expected.json` is the in-repo gold for synthetic fixtures.
    `fixtures/gold-cnesst/` points at real files and is not in the repo (§13.3).
    """
    path = (root or SETTINGS.fixtures_dir) / name / "expected.json"
    if not path.exists():
        raise FileNotFoundError(f"no gold for {name!r} at {path}")

    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    return Gold(
        id=name,
        # Absent means version 1: the synthetic fixtures were authored with the engine and
        # have no correction history yet. A real key states its own.
        version=str(raw.get("gold_version", "1")),
        digest=hash_text(text)[:16],
        bundles=raw.get("bundles", {}),
        units=tuple(_to_unit(u) for u in raw.get("units", [])),
        printed_labels={int(k): v for k, v in (raw.get("printed_labels") or {}).items()},
        proposed=tuple(raw.get("proposed", ())),
        unresolved=tuple(raw.get("unresolved", ())),
    )


def available(root: Path | None = None) -> list[str]:
    base = root or SETTINGS.fixtures_dir
    if not base.is_dir():
        return []
    return sorted(p.parent.name for p in base.glob("*/expected.json"))
