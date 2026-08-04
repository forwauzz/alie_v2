"""Data model (PRD §8). Engine-level and regime-free — packs supply vocabulary, not types."""

from .block import BBox, Block, BlockSource, BlockType
from .citation import Citation, Span
from .dates import ELIGIBLE_ROLES, INELIGIBLE_ROLES, DateFact, DateRole, RowDate
from .row import DATE_STATUS_FACTOR, WARN_AT, Bullet, Row
from .status import MODEL_FORBIDDEN, EpistemicTag, Legibility, RowStatus
from .unit import ReportUnit, UnitKind

__all__ = [
    "BBox",
    "Block",
    "BlockSource",
    "BlockType",
    "Bullet",
    "Citation",
    "DATE_STATUS_FACTOR",
    "DateFact",
    "DateRole",
    "ELIGIBLE_ROLES",
    "EpistemicTag",
    "INELIGIBLE_ROLES",
    "Legibility",
    "MODEL_FORBIDDEN",
    "ReportUnit",
    "Row",
    "RowDate",
    "RowStatus",
    "Span",
    "UnitKind",
    "WARN_AT",
]
