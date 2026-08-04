"""Pack loading (PRD §6). A pack's rules are data — lookups, never read by an agent.

Resolution order is base -> pack -> firm -> case -> unit (§6.3). The firm layer exists
because style is per-firm and arguably per-paralegal; without it, onboarding firm #2 means
forking a pack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import SETTINGS

_FILES = ("pack", "classes", "dates", "filters", "output")


@dataclass(frozen=True)
class Pack:
    id: str
    version: str
    root: Path
    pack: dict[str, Any] = field(default_factory=dict)
    classes: dict[str, Any] = field(default_factory=dict)
    dates: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)

    @property
    def class_list(self) -> list[dict[str, Any]]:
        return self.classes.get("classes", [])

    def class_by_id(self, class_id: str) -> dict[str, Any] | None:
        return next((c for c in self.class_list if c["id"] == class_id), None)

    def class_label(self, class_id: str) -> str:
        c = self.class_by_id(class_id)
        return c["label"] if c else class_id

    @property
    def date_roles(self) -> dict[str, Any]:
        return self.dates.get("roles", {})

    @property
    def date_rule_table(self) -> dict[str, Any]:
        return self.dates.get("rule_table", {})

    @property
    def min_class_confidence(self) -> float:
        return self.classes.get("defaults", {}).get("min_confidence", 0.7)

    @property
    def unknown_class(self) -> str:
        return self.classes.get("defaults", {}).get("unknown_class", "unknown")

    def toggles(self) -> dict[str, bool]:
        return self.pack.get("unit_toggles", {})

    def is_diagnostic_study(self, class_id: str) -> bool:
        c = self.class_by_id(class_id)
        return bool(c and c.get("is_diagnostic_study"))


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=16)
def load(pack_id: str, packs_dir: str | None = None) -> Pack:
    root = Path(packs_dir) if packs_dir else SETTINGS.packs_dir
    pack_root = root / pack_id
    if not pack_root.is_dir():
        raise KeyError(f"unknown pack: {pack_id} (looked in {root})")
    parts = {name: _read(pack_root / f"{name}.yaml") for name in _FILES}
    meta = parts["pack"]
    return Pack(
        id=meta.get("id", pack_id),
        version=str(meta.get("version", "0")),
        root=pack_root,
        **parts,
    )


def available(packs_dir: Path | None = None) -> list[str]:
    root = packs_dir or SETTINGS.packs_dir
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "pack.yaml").exists())


def versions(packs_dir: Path | None = None) -> dict[str, str]:
    """Logged as an MLflow param on every run (§11.1)."""
    return {p: load(p, str(packs_dir) if packs_dir else None).version for p in available(packs_dir)}
