"""Parse layer (PRD §4.3). Blocks are the truth; markdown is a rendering of blocks."""

from ..seams import parser as _seam
from .textlayer import TextLayerParser


def register_default_tiers() -> None:
    """Phase 1 registers the free tier only. `parse.ocr` and `parse.vision` ship off and
    exist to be measured against what this covers (§9.2)."""
    _seam.clear()
    _seam.register(TextLayerParser())


__all__ = ["TextLayerParser", "register_default_tiers"]
