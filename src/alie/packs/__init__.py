"""Regime packs (PRD §6). Regimes are data, not branches — the engine holds zero regime
knowledge, and there is no `if regime == "SAAQ"` anywhere in it."""

from . import skills  # noqa: F401
from .loader import Pack, available, load, versions
from .templates import Template, UnknownRevision, known_forms, lookup, registry

__all__ = [
    "Pack",
    "Template",
    "UnknownRevision",
    "available",
    "known_forms",
    "load",
    "lookup",
    "registry",
    "versions",
]
