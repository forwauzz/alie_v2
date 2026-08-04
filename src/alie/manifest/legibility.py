"""Step (f) — assess legibility: the gate before the model (PRD §4.4).

Illegible units get a row marked `Illisible` with a reason, and are **never sent to the
model** — given noise a model produces fluent French clinical bullets that appear nowhere
in the source (§8.5). This is a safety invariant, not a flag (§9).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Block, Legibility
from ..parse.textquality import word_likeness

#: Below this many characters per page, a page carries no readable content.
MIN_CHARS_PER_PAGE = 40

#: Word-likeness thresholds, set against the 139-page reference bundle (see
#: `parse.textquality`). A unit below the first is noise wearing a text layer and must
#: never reach a model; below the second it is readable enough to review but not to trust.
ILLEGIBLE_QUALITY = 0.35
DEGRADED_QUALITY = 0.60

#: Mean block confidence below this reads as degraded — worth flagging, still legible.
DEGRADED_CONFIDENCE = 0.8

#: Proportion of blocks flagged as degraded numbers that tips a unit to degraded.
DEGRADED_NUMBER_SHARE = 0.15


@dataclass(frozen=True)
class LegibilityAssessment:
    level: Legibility
    reason: str

    @property
    def gated_from_model(self) -> bool:
        return self.level is Legibility.ILLEGIBLE


def assess(blocks: list[Block], page_count: int) -> LegibilityAssessment:
    if not blocks:
        return LegibilityAssessment(
            Legibility.ILLEGIBLE,
            "No text layer on any page of the unit; nothing was extracted.",
        )

    chars = sum(len(b.text) for b in blocks)
    if page_count and chars / page_count < MIN_CHARS_PER_PAGE:
        return LegibilityAssessment(
            Legibility.ILLEGIBLE,
            f"Only {chars} characters across {page_count} page(s); below the readable floor.",
        )

    # Quantity is not quality. A page can be dense with characters and still be noise —
    # this is the check that keeps a pre-OCR'd scan out of the model.
    quality = word_likeness(" ".join(b.text for b in blocks))
    if quality < ILLEGIBLE_QUALITY:
        return LegibilityAssessment(
            Legibility.ILLEGIBLE,
            f"Text layer present but only {quality:.0%} of it reads as words; "
            "the scan's own OCR pass failed.",
        )
    if quality < DEGRADED_QUALITY:
        return LegibilityAssessment(
            Legibility.DEGRADED,
            f"{quality:.0%} of the text reads as words; legible enough to review, "
            "not to trust unread.",
        )

    mean_confidence = sum(b.confidence for b in blocks) / len(blocks)
    degenerate = sum(1 for b in blocks if b.attrs.get("degenerate_number"))

    if mean_confidence < DEGRADED_CONFIDENCE:
        return LegibilityAssessment(
            Legibility.DEGRADED,
            f"Mean block confidence {mean_confidence:.2f} is below {DEGRADED_CONFIDENCE}.",
        )
    if degenerate and degenerate / len(blocks) >= DEGRADED_NUMBER_SHARE:
        return LegibilityAssessment(
            Legibility.DEGRADED,
            f"{degenerate} block(s) contain a malformed number; a wrong barème "
            "percentage is a legal error, not a typo.",
        )
    if degenerate:
        return LegibilityAssessment(
            Legibility.LEGIBLE,
            f"{degenerate} block(s) flagged for a malformed number; review those values.",
        )
    return LegibilityAssessment(Legibility.LEGIBLE, "Text layer read cleanly.")
