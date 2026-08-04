"""Statuses that are mandatory on every row (PRD §8.4) and the legibility gate (§4.4f)."""

from __future__ import annotations

from enum import StrEnum


class RowStatus(StrEnum):
    RESOLVED = "resolved"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"
    UNDATED = "undated"
    ILLEGIBLE = "illegible"
    MANUAL = "manual"  # a human correction; survives re-runs (§10.2)


#: Statuses whose units must never be sent to the model. Given noise a model produces
#: fluent French clinical bullets that appear nowhere in the source (§8.5). This is a
#: safety invariant, not a flag (§9).
MODEL_FORBIDDEN = frozenset({RowStatus.ILLEGIBLE})


class Legibility(StrEnum):
    LEGIBLE = "legible"
    DEGRADED = "degraded"  # readable, worth flagging
    ILLEGIBLE = "illegible"  # gated from the model


class EpistemicTag(StrEnum):
    """Every pack rule carries its tag (PRD §6.2). This is a confidence model, not a
    documentation convention: it lets the review panel say "this diverges from the gold
    deliberately, per rule X [PROP]" instead of looking like a defect."""

    KEY = "KEY"  # directly evidenced in the answer key
    INF_H = "INF-H"  # inferred, high confidence
    INF_L = "INF-L"  # inferred, low confidence
    PROP = "PROP"  # a deliberate proposed improvement over the gold
    GAP = "GAP"  # known unknown
