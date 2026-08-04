"""Required fields and first-class values (PRD §8.6).

Non-negotiable, from the framework's data-model checklist. The distinction this module
exists to hold:

- **absent** is a *value*. The field was looked for and is not in the document.
- **missing** means the engine never looked — no template covered it, no rule fired.

Collapsing those two is the failure §8.6 is written against. `aucune` ≠ `trop tôt` ≠
absent, and none of the three is `null` or `false`. A three-state field stored as a boolean
has already lost the case.

The pack declares which values are first-class and what states they may take. The engine
enforces that the declaration and the readers agree — a value declared first-class that no
reader ever produces is a silent gap, and the whole point of declaring it was to notice.
"""

from __future__ import annotations

from dataclasses import dataclass

from .loader import Pack


@dataclass(frozen=True)
class FieldReport:
    """What the engine knows about one required field on one unit."""

    field: str
    #: The state read, e.g. `oui` / `trop_tot` / `absent`. None means *missing*.
    state: str | None
    declared_states: tuple[str, ...]
    #: True when a reader ran and returned a value. False means nothing looked.
    observed: bool

    @property
    def missing(self) -> bool:
        """Nothing looked. Distinct from a state of `absent`, which is a finding."""
        return not self.observed

    @property
    def valid(self) -> bool:
        return self.observed and self.state in self.declared_states


@dataclass(frozen=True)
class Validation:
    """Whether a pack's first-class declarations and its readers actually agree."""

    unreadable: tuple[str, ...] = ()
    """Declared first-class, but no template field or prompt field can produce it. A
    declaration nothing reads is a promise the export cannot keep."""

    def __bool__(self) -> bool:
        return not self.unreadable


def declared(pack: Pack) -> dict[str, tuple[str, ...]]:
    """First-class value -> the states it may take. Never null, never false (§8.6)."""
    return {
        name: tuple(spec.get("states", ()))
        for name, spec in pack.first_class_values.items()
    }


def readable_fields(pack: Pack) -> set[str]:
    """Field ids any registered template can produce.

    A first-class value the templates cannot emit is not a data-model decision, it is a
    gap — and it looks identical to a field that is simply absent from every document.
    """
    from .templates import registry

    out: set[str] = set()
    for template in registry(pack).values():
        out.update(spec["id"] for spec in template.fields)
    return out


def validate(pack: Pack) -> Validation:
    """Check the pack against itself. Called at load time in dev and by the pack test."""
    return Validation(unreadable=tuple(sorted(set(declared(pack)) - readable_fields(pack))))


def report(pack: Pack, values: dict[str, str | None]) -> list[FieldReport]:
    """Grade what a unit produced against what the pack requires.

    `values` maps field id -> state as read. A field absent from the mapping was never
    looked for, which is reported as `missing` rather than quietly becoming `absent`.
    """
    return [
        FieldReport(
            field=name,
            state=values.get(name),
            declared_states=states,
            observed=name in values,
        )
        for name, states in sorted(declared(pack).items())
    ]
