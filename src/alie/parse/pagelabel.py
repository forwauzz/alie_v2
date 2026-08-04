"""Printed page labels — `page_label` blocks (PRD §4.3, §8.1).

`printed_label` is what renders; `pdf_index` is what the system navigates by. For
`Clinique mère et monde`, 2023-05-09 is EMR page 44 but PDF pages 39-40, and the answer
key says `p. 44`. Rendering the PDF index would have produced `p. 39` and nobody would
have noticed until the firm did.

All patterns are whitespace-tolerant: OCR eats spaces (`RAPPORTD'IMAGERIE` was observed)
and doubles them (§4.4).
"""

from __future__ import annotations

import re

#: Fraction of page height treated as header/footer. Labels outside this band are almost
#: always body text that happens to look like a label.
BAND = 0.12

_WS = r"[\s ]*"

#: Ordered by specificity. The first match wins and its `label` group is the printed label.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "page_x_of_y",
        re.compile(
            rf"^{_WS}(?:p(?:age)?\.?{_WS})?(?P<label>\d{{1,4}}){_WS}"
            rf"(?:de|of|/|sur){_WS}\d{{1,4}}{_WS}$",
            re.IGNORECASE,
        ),
    ),
    (
        "page_n",
        re.compile(rf"^{_WS}p(?:age)?\.{_WS}(?P<label>\d{{1,4}}){_WS}$", re.IGNORECASE),
    ),
    ("bare_number", re.compile(rf"^{_WS}(?P<label>\d{{1,4}}){_WS}$")),
)


def match(text: str) -> tuple[str, str] | None:
    """Return `(rule_name, label)` when `text` reads as a printed page label."""
    for name, pattern in PATTERNS:
        m = pattern.match(text)
        if m:
            return name, m.group("label")
    return None


def in_band(y0: float, y1: float, page_height: float) -> bool:
    """True when the block sits in the header or footer band (top-left origin)."""
    margin = page_height * BAND
    return y1 <= margin or y0 >= page_height - margin


def detect(text: str, y0: float, y1: float, page_height: float) -> tuple[str, str] | None:
    """A label must both look like one and sit where one sits."""
    if not in_band(y0, y1, page_height):
        return None
    return match(text)
