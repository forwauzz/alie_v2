"""Row model (PRD §8.5) and the confidence rule (§8.7).

Confidence rides with the string, not the row. A row unioned across two bundles carries
bullets from both; a score stored on the row is destroyed by that merge, and "which page
said this" must survive it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .citation import Citation
from .dates import RowDate
from .status import RowStatus

#: Rows at or below this render a warning marker (§8.7).
WARN_AT = 0.75

#: Multiplier applied to the minimum bullet confidence, by date status.
DATE_STATUS_FACTOR: dict[RowStatus, float] = {
    RowStatus.RESOLVED: 1.0,
    RowStatus.MANUAL: 1.0,
    RowStatus.INFERRED: 0.9,
    RowStatus.AMBIGUOUS: 0.7,
    RowStatus.UNDATED: 0.6,
    RowStatus.ILLEGIBLE: 0.3,
}


@dataclass(frozen=True)
class Bullet:
    """One transcribed line. Every string is cited; an uncited string is a validation
    failure, not a warning (§3.5)."""

    text: str
    citation: Citation
    confidence: float = 1.0
    rule: str | None = None  # the pack rule that produced it, for the why-panel


@dataclass
class Row:
    id: str
    case_id: str
    row_date: RowDate

    title: str  # the title sub-block
    author: str | None  # optional author sub-block

    #: Ordered, and *may be empty*. Zero-content documents survive as title-only rows —
    #: evidentiary completeness (§8.5).
    bullets: list[Bullet] = field(default_factory=list)

    #: One per contributing bundle. Cross-bundle union retains both (§8.5).
    locators: list[Citation] = field(default_factory=list)

    unit_ids: list[str] = field(default_factory=list)
    doc_class: str = ""
    regime: str = ""

    illegible_reason: str | None = None
    second_hand: bool = False

    @property
    def confidence(self) -> float:
        base = min((b.confidence for b in self.bullets), default=1.0)
        return base * DATE_STATUS_FACTOR.get(self.row_date.status, 1.0)

    @property
    def warns(self) -> bool:
        return self.confidence <= WARN_AT

    @property
    def is_undated(self) -> bool:
        return self.row_date.value is None

    def sort_key(self) -> tuple:
        """Stable ordering. Two rows on the same date with no author need a deterministic
        tie-break, or every re-run produces spurious diffs (§10.4)."""
        d = self.row_date.value
        return (
            d.toordinal() if d else 0,
            self.author or "",
            self.doc_class,
            min((c.pdf_index for c in self.locators), default=0),
            self.id,
        )
