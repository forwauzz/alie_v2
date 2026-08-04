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

#: `p?age` rather than `page`: OCR drops the leading capital often enough that the
#: reference bundle yields `age 1 de 2` and `age | de`. §4.4 asks these patterns to survive
#: exactly that damage, and the cost of missing one is a citation rendering a pdf index
#: because the real label was a glyph short.
_PAGE_WORD = r"(?:p?age|p)\.?"

#: Ordered by specificity. The first match wins and its `label` group is the printed label.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "page_x_of_y",
        re.compile(
            rf"^{_WS}(?:{_PAGE_WORD}{_WS})?(?P<label>\d{{1,4}}){_WS}"
            rf"(?:de|of|/|sur){_WS}\d{{1,4}}{_WS}$",
            re.IGNORECASE,
        ),
    ),
    # The total is often lost to the same damage: `age 1 de` with nothing following.
    (
        "page_x_of_truncated",
        re.compile(
            rf"^{_WS}{_PAGE_WORD}{_WS}(?P<label>\d{{1,4}}){_WS}(?:de|of|sur){_WS}$",
            re.IGNORECASE,
        ),
    ),
    (
        "page_n",
        re.compile(rf"^{_WS}{_PAGE_WORD}{_WS}(?P<label>\d{{1,4}}){_WS}$", re.IGNORECASE),
    ),
    # A bare number in the footer band is only a *candidate*. Real bundles put reference
    # numbers, birth years and dossier ids there too — the reference bundle yields `1937`,
    # `780` and `140`, none of which are page numbers. Rendering `p. 1937` into a citation
    # would be visibly wrong to the firm, so these are confirmed at bundle level by
    # `confirm_bare_labels` before anything relies on them.
    ("bare_number", re.compile(rf"^{_WS}(?P<label>\d{{1,4}}){_WS}$")),
)

#: Rules whose match is self-evidencing: `p. 2 de 4` says what it is.
TRUSTED_RULES = frozenset({"page_x_of_y", "page_x_of_truncated", "page_n"})

#: A four-digit number in this span is a year, not a page. Medico-legal footers carry
#: birth years and claim years; the reference bundle prints `1937` on four separate pages.
YEAR_RANGE = (1900, 2100)

#: Above this, a value repeating across pages is a reference number rather than a page
#: count. Small numbers legitimately repeat — every two-page fax has a page `2`.
RECURRING_MIN = 20


def confirm_bare_labels(labels: dict[int, tuple[str, str]]) -> dict[int, str]:
    """Decide which bare footer numbers are really page labels.

    Input maps `pdf_index -> (rule, label)`. This runs once per bundle because the
    evidence is cross-page: the same number on many sheets is a masthead, not a counter.

    Two rejections, both from what real bundles actually print:

    - **Years.** `1937` in a footer is a date of birth. Citing `p. 1937` would be visibly
      wrong to the firm.
    - **Large recurring numbers.** A dossier or reference number repeats unchanged; a page
      number does not.

    An isolated three-digit number is *kept*. It cannot be told apart from a genuine EMR
    page number without more context, and the §8.1 case the whole field exists for is
    exactly that: `Clinique mère et monde` prints `44` on a sheet whose neighbours print
    nothing, and the answer key cites `p. 44`. Dropping it to be safe would lose the one
    example that proves the point.

    A page with no confirmed label is not an error — display falls back to the pdf index
    and the row is flagged (§8.1).
    """
    bare = {i: int(v) for i, (rule, v) in labels.items() if rule == "bare_number" and v.isdigit()}
    occurrences: dict[int, int] = {}
    for value in bare.values():
        occurrences[value] = occurrences.get(value, 0) + 1

    out: dict[int, str] = {}
    for index, (rule, text) in labels.items():
        if rule in TRUSTED_RULES:
            out[index] = text
            continue
        value = bare.get(index)
        if value is None:
            continue
        if YEAR_RANGE[0] <= value <= YEAR_RANGE[1]:
            continue
        if value > RECURRING_MIN and occurrences.get(value, 0) > 1:
            continue
        out[index] = text
    return out


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
