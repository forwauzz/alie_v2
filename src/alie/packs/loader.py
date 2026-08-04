"""Pack loading (PRD §6). A pack's rules are data — lookups, never read by an agent.

Resolution order is base -> pack -> firm -> case -> unit (§6.3). The firm layer exists
because style is per-firm and arguably per-paralegal; without it, onboarding firm #2 means
forking a pack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import SETTINGS

_FILES = ("pack", "classes", "dates", "filters", "fields", "output")

#: `confounder.cont1` — the tail of a sentence that ran past its own block. Each part keeps
#: its own span, because a record carries one span and the citation invariant is per string
#: (§8.1).
_CONTINUATION = re.compile(r"\.cont\d+$")


@dataclass(frozen=True)
class Pack:
    id: str
    version: str
    root: Path
    pack: dict[str, Any] = field(default_factory=dict)
    classes: dict[str, Any] = field(default_factory=dict)
    dates: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)

    @property
    def class_list(self) -> list[dict[str, Any]]:
        return self.classes.get("classes", [])

    def class_by_id(self, class_id: str) -> dict[str, Any] | None:
        return next((c for c in self.class_list if c["id"] == class_id), None)

    def class_label(self, class_id: str) -> str:
        """Human label for a class. Never falls through to the raw id — an unclassified
        unit still gets a row, and that row is read by the firm (§3.4)."""
        c = self.class_by_id(class_id)
        if c:
            return c["label"]
        defaults = self.classes.get("defaults", {})
        if class_id == defaults.get("unknown_class", "unknown"):
            return defaults.get("unknown_label", "Document non classé")
        return class_id

    def field_line(self, field: str) -> str | None:
        """Line template for an extracted field. Code renders the row; the model never
        writes one (§3.2), and the wording is the pack's business, not the engine's."""
        # The tail of a sentence that ran past its block. It is the same finding continued,
        # so it must not re-announce its label — "Facteur confondant :" twice reads as two
        # confounders where the document states one.
        if _CONTINUATION.search(field):
            return self.output.get("continuation_line", "{value}")
        base = field.split(".")[0]
        return self.output.get("field_lines", {}).get(base)

    def is_index_field(self, field: str) -> bool:
        """A field stored for querying, not for the chronology.

        The trajectory enum sits beside the sentence it was derived from; rendering both
        prints the finding twice, once in her words and once in the engine's. The enum
        stays in the record store where a query can reach it (§8.6).
        """
        return field in set(self.output.get("index_fields", []))

    def state_label(self, field: str, state: str) -> str:
        """How a first-class state is worded in the deliverable.

        An internal state id (`trop_tot`) must never reach a document the firm hands to
        opposing counsel — the same rule as `unknown_label`. Unmapped states fall through
        unchanged rather than becoming blank: a missing label is a pack gap to notice, not
        a value to lose (§8.6).
        """
        base = field.split(".")[0]
        return self.output.get("state_labels", {}).get(base, {}).get(state, state)

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

    @property
    def admin_classes(self) -> set[str]:
        """Classes the pack calls administrative. Used by the zero-content filter, which
        must never fire on a clinical document (§8.5)."""
        return {c["id"] for c in self.class_list if c.get("is_admin")}

    @property
    def cue_fields(self) -> list[dict[str, Any]]:
        """§8.6 fields read by cue from any document, template or not."""
        return self.fields.get("fields", [])

    def evidence_weight(self, procured_by: str) -> str | None:
        """What a procurement source is worth **under this regime**.

        IVAC and SAAQ have no binding-treating-opinion tier (§8.6), so this is the pack's
        answer and not a constant — an engine that hardcodes the CNESST rule is wrong on
        two of three regimes.
        """
        return self.fields.get("evidence_weight", {}).get(procured_by)

    @property
    def first_class_values(self) -> dict[str, Any]:
        """Values that must survive as themselves, never collapsed to null or false
        (§8.6). `aucune` is not `trop tôt` is not absent."""
        return self.pack.get("first_class_values", {})

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
