"""Feature flags (PRD §9).

Two kinds, and they behave differently. **Behaviour** flags are safe mid-case, instantly
reversible, no recompute. **Implementation** flags invalidate work — a case whose pages
were parsed by two engines with no record of which is which is worse than never having had
the flag.

Everything in the register is built. Unproven features ship **off**, each paired with the
metric that answers whether it should be on. A flag without a defined metric is a
preference, not an experiment, and should not be added (§9.2).

Safety invariants are **not flags** and appear read-only (§9): illegible units never reach
the model; no uncited string in an export; only strictly identical duplicates are
auto-removable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class FlagKind(StrEnum):
    BEHAVIOUR = "behaviour"  # reversible mid-case, no recompute
    IMPLEMENTATION = "implementation"  # invalidates derived work; carries a re-run badge


@dataclass(frozen=True)
class Flag:
    id: str
    kind: FlagKind
    default: Any
    question: str
    metric: str

    @property
    def needs_rerun(self) -> bool:
        return self.kind is FlagKind.IMPLEMENTATION


#: The §9.2 register. Every row states the question the flag answers and the metric that
#: judges it, defined at the moment the flag is defined.
REGISTER: tuple[Flag, ...] = (
    Flag("parse.ocr", FlagKind.IMPLEMENTATION, False,
         "how much of a real bundle the free path misses",
         "% pages queued as unparseable"),
    Flag("parse.vision", FlagKind.IMPLEMENTATION, False,
         "how much OCR still fails",
         "% pages OCR queues that vision resolves; block confidence delta"),
    Flag("parse.templates", FlagKind.IMPLEMENTATION, True,
         "—",
         "checkbox agreement vs gold on templated forms"),
    Flag("extract.structured_first", FlagKind.IMPLEMENTATION, True,
         "does 4a-before-4b actually reduce model work",
         "% fields resolved without the model; cost per unit"),
    Flag("dedupe.enabled", FlagKind.BEHAVIOUR, False,
         "how much duplication exists",
         "candidate pairs; verdict distribution; NOT row recall"),
    Flag("dedupe.auto_remove_identical", FlagKind.BEHAVIOUR, False,
         "is auto-removal ever safe",
         "human agreement rate on `identical` verdicts"),
    Flag("manifest.orphan_rejoin", FlagKind.IMPLEMENTATION, False,
         "how common non-contiguous units are",
         "units changed by the pass; boundary precision delta"),
    Flag("screen.per_unit_regime", FlagKind.IMPLEMENTATION, False,
         "is mixed-regime real or a one-off",
         "units whose regime differs from the case default"),
    Flag("render.health_narrative", FlagKind.BEHAVIOUR, False,
         "—",
         "composer groundedness; RAGAS context precision"),
    Flag("render.doctype_code", FlagKind.BEHAVIOUR, False,
         "display preference",
         "none — behaviour flag"),
)

BY_ID: dict[str, Flag] = {f.id: f for f in REGISTER}

#: Read-only in every surface. Disabling one produces no data point, only a bad
#: chronology (§9.3).
SAFETY_INVARIANTS: tuple[str, ...] = (
    "illegible units never reach the model",
    "no uncited string in an export",
    "only strictly identical duplicates are auto-removable",
)


def defaults() -> dict[str, Any]:
    return {f.id: f.default for f in REGISTER}


def resolve(
    *,
    global_flags: dict[str, Any] | None = None,
    case_flags: dict[str, Any] | None = None,
    run_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve global -> case -> run (§9 rule 3). A run is immutable and records the
    resolved set; changing a flag creates a new run."""
    resolved = defaults()
    for layer in (global_flags, case_flags, run_flags):
        for key, value in (layer or {}).items():
            if key not in BY_ID and not key.startswith(("model.", "prompt.")):
                raise KeyError(f"unknown flag: {key}")
            resolved[key] = value
    return resolved


def output_affecting(resolved: dict[str, Any]) -> list[str]:
    """Flags that carry a re-run badge and write to the audit log (§9 rule 4)."""
    return sorted(
        key
        for key, value in resolved.items()
        if key in BY_ID and BY_ID[key].needs_rerun and value != BY_ID[key].default
    )


def diff_requires_rerun(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    return sorted(k for k in changed if k not in BY_ID or BY_ID[k].needs_rerun)
