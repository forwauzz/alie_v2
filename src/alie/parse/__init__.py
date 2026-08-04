"""Parse layer (PRD §4.3). Blocks are the truth; markdown is a rendering of blocks.

Pages route by type, cost rising left to right: text layer (free) → OCR → vision. A page
is claimed by the cheapest tier that can honestly read it, which is not the same as the
cheapest tier that returns something.
"""

from typing import Any

from ..models import Block
from ..seams import parser as _seam
from .ocr import OcrParser
from .ocr import available as ocr_available
from .textlayer import READABLE_QUALITY, TextLayerParser
from .textquality import word_likeness
from .vision import VisionParser
from .vision import available as vision_available
from .vision import register_if_configured as register_vision_backend

#: A tier's output is accepted when it reads like language. Below this the page escalates
#: to a more expensive tier, or — if there is none — is kept as the best available attempt
#: and counted unparseable. Real bundles arrive pre-OCR'd by whoever scanned them and that
#: pass often failed while still emitting characters, so "returned something" is not the
#: same as "read it" (§4.3).
#:
#: Deliberately the *same* threshold the text-layer tier uses to claim a page. Two
#: different definitions of "readable" in one pipeline means a page can be simultaneously
#: good enough to claim and not good enough to keep, and which one wins depends on call
#: order. Escalation now fires exactly when a tier produced something it would itself have
#: declined.
ACCEPTABLE_QUALITY = READABLE_QUALITY


def _acceptable(blocks: list[Block]) -> bool:
    if not blocks:
        return False
    text = " ".join(b.text for b in blocks if b.is_body_text)
    return bool(text.strip()) and word_likeness(text) >= ACCEPTABLE_QUALITY


def register_tiers(flags: dict[str, Any] | None = None) -> list[str]:
    """Register the parse tiers this run is allowed to use.

    `parse.ocr` and `parse.vision` ship off (§9.2). With OCR off, a page whose text layer
    is noise is claimed by nobody and is counted as unparseable — which is exactly the
    metric that decides whether OCR earns its place.
    """
    flags = flags or {}
    _seam.clear()
    _seam.set_quality_gate(_acceptable)
    _seam.register(TextLayerParser())
    if flags.get("parse.ocr") and ocr_available():
        _seam.register(OcrParser())
    if flags.get("parse.vision"):
        # Registration is attempted, not assumed. With no credential the tier is absent
        # and pages fall through exactly as when the flag was off (§9.2).
        register_vision_backend()
        if vision_available():
            _seam.register(VisionParser())
    return _seam.registered_tiers()


def register_default_tiers() -> list[str]:
    """Phase 1 default: the free tier only."""
    return register_tiers({})


__all__ = [
    "OcrParser",
    "TextLayerParser",
    "VisionParser",
    "ocr_available",
    "register_default_tiers",
    "register_tiers",
    "vision_available",
]
