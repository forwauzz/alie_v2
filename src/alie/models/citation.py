"""Citation — an engine invariant. Packs may not change storage, only display (PRD §8.1).

    citation = bundle_id + pdf_index + printed_label + unit_id + span

Both page numbers, on every page, always. `printed_label` is what renders; `pdf_index` is
what the system navigates by. For `Clinique mère et monde`, 2023-05-09 is EMR page 44 but
PDF pages 39-40, and the answer key says `p. 44`.

When no printed label exists, display falls back to `pdf_index` and the row is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    """Character offsets into the text of one block."""

    block_id: str
    start: int
    end: int

    def slice(self, text: str) -> str:
        return text[self.start : self.end]


@dataclass(frozen=True)
class Citation:
    bundle_id: str
    pdf_index: int  # 1-based position in the uploaded PDF
    printed_label: str | None  # the number printed on the page, e.g. "44"
    unit_id: str
    span: Span | None = None

    @property
    def has_printed_label(self) -> bool:
        return bool(self.printed_label)

    @property
    def display_page(self) -> str:
        """What renders. Falls back to pdf_index, and the row is flagged when it does."""
        return self.printed_label or str(self.pdf_index)

    @property
    def needs_flag(self) -> bool:
        return not self.has_printed_label
