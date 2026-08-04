"""Regime packs (PRD §6). Regimes are data, not branches — the engine holds zero regime
knowledge, and there is no `if regime == "SAAQ"` anywhere in it."""

from .loader import Pack, available, load, versions

__all__ = ["Pack", "available", "load", "versions"]
