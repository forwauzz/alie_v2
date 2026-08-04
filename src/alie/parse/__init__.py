"""Parse layer (PRD §4.3). Blocks are the truth; markdown is a rendering of blocks.

Pages route by type, cost rising left to right: text layer (free) → OCR → vision. A page
is claimed by the cheapest tier that can honestly read it, which is not the same as the
cheapest tier that returns something.
"""

from typing import Any

from ..seams import parser as _seam
from .ocr import OcrParser
from .ocr import available as ocr_available
from .textlayer import TextLayerParser


def register_tiers(flags: dict[str, Any] | None = None) -> list[str]:
    """Register the parse tiers this run is allowed to use.

    `parse.ocr` and `parse.vision` ship off (§9.2). With OCR off, a page whose text layer
    is noise is claimed by nobody and is counted as unparseable — which is exactly the
    metric that decides whether OCR earns its place.
    """
    flags = flags or {}
    _seam.clear()
    _seam.register(TextLayerParser())
    if flags.get("parse.ocr") and ocr_available():
        _seam.register(OcrParser())
    return _seam.registered_tiers()


def register_default_tiers() -> list[str]:
    """Phase 1 default: the free tier only."""
    return register_tiers({})


__all__ = [
    "OcrParser",
    "TextLayerParser",
    "ocr_available",
    "register_default_tiers",
    "register_tiers",
]
