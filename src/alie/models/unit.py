"""Report unit — one logical report. A set of pages, not a range (PRD §2, §8.3).

2022-08-03's consult note is pages 125 *and* 128, wrapping around the IRM at 126-127.
Boundary detection needs a second pass re-joining orphan continuation pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .dates import DateFact, RowDate
from .status import Legibility


class UnitKind(StrEnum):
    PRIMARY = "primary"  # the document itself is in the bundle
    REFERENCED = "referenced"  # exists only as a citation inside another note (§8.3)


@dataclass
class ReportUnit:
    id: str
    bundle_id: str
    case_id: str

    #: A *set* of 1-based pdf page indices, stored sorted. Non-contiguous is normal.
    pages: tuple[int, ...]

    doc_class: str  # pack-defined; the engine holds no taxonomy of its own
    class_confidence: float
    class_source: str  # "serial" | "zones" | "model" | "manual"

    regime: str  # per-unit, not per-case (§6.1)
    legibility: Legibility

    dates: tuple[DateFact, ...] = ()
    row_date: RowDate | None = None

    author: str | None = None
    form_serial: str | None = None
    form_revision: str | None = None
    kind: UnitKind = UnitKind.PRIMARY

    #: For REFERENCED units, the unit whose text cites this one. Its locator is the
    #: citing document, and the row is flagged second-hand.
    cited_by_unit_id: str | None = None

    excluded_by: str | None = None  # filter rule id, if excluded — still reaches the manifest
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def is_contiguous(self) -> bool:
        return self.pages == tuple(range(self.pages[0], self.pages[-1] + 1)) if self.pages else True

    @property
    def first_page(self) -> int | None:
        return self.pages[0] if self.pages else None

    @property
    def gated_from_model(self) -> bool:
        """Illegible units are never sent to the model. Safety invariant, not a flag."""
        return self.legibility is Legibility.ILLEGIBLE
