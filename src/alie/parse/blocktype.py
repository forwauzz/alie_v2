"""Block-type inference for the text-layer tier (PRD §4.3).

The adopted markup vocabulary is `[x]` / `[ ]`, `<signature>`, `<empty>`, `<b> <i> <u>`,
tables and heading hierarchy. `<empty>` is load-bearing: it distinguishes *field present
but blank* from *field absent*, which is exactly the three-state consolidation and
four-state APIPP distinction (§8.6).

Handwriting is deliberately absent here. It has no text layer, so this tier cannot see it;
detection belongs to the vision tier. Emitting a guess would be worse than emitting
nothing — a reviewer's private note leaking into a document destined for opposing counsel
is the failure this block type exists to prevent (§4.3).
"""

from __future__ import annotations

import re

from ..models import BlockType

#: A field label with no value after the colon: `Dossier:` with nothing following.
EMPTY_FIELD = re.compile(r"^\s*([^:\n]{1,60}?)\s*:\s*$")

CHECKBOX = re.compile(r"\[\s*[xX✓]?\s*\]")

SIGNATURE = re.compile(r"^\s*(sign[ée]|signature|dr[e]?\.?\s|<signature>)", re.IGNORECASE)

#: Fax banners and mailroom marks — isolated for the dedupe transmission axis (§10.1).
STAMP = re.compile(
    r"(^|\s)(de\s*:|from\s*:|à\s*:|to\s*:|fax|t[ée]l[ée]copie|p\.\s*\d{3}\b|re[çc]u\s+le)",
    re.IGNORECASE,
)

#: A tabular row: a label followed by two or more numeric groups, as in a séquelles table
#: (`Code 204 219    2 %    1 % sur antérieur`).
#:
#: This marks a line as *belonging to* a table. It does not recover cell structure — that
#: needs column geometry, and pdfium collapses runs of spaces so whitespace cannot stand
#: in for it. Cell-level reads on templated forms come from the template registry (4a),
#: which crops known coordinates; untemplated tables are the medium tier in §4.3 and are
#: not claimed as done here.
TABLE_ROW = re.compile(r"^\s*\S.*?(?:\s\d[\d\s.,%°/-]*){2,}\s*$")

#: A percentage or code that picked up a non-numeric interloper — the `2°2` observed in a
#: séquelles table. A wrong barème percentage is a legal error, not a typo (§4.3).
DEGENERATE_NUMBER = re.compile(r"\d[^\d\s.,%/-]\d")

#: Headings are set larger than body text. Relative to the page's own body size, so it
#: survives any base font size. Glyph-box height is *not* usable here — accents and
#: descenders make it a noisy proxy that promotes ordinary body lines to headings.
HEADING_SIZE_RATIO = 1.05
HEADING_L1_RATIO = 1.25
HEADING_MAX_CHARS = 90


def is_degenerate_number(text: str) -> bool:
    return bool(DEGENERATE_NUMBER.search(text))


def infer(
    text: str,
    *,
    font_size: float,
    body_size: float,
    is_upper_dense: bool,
    heading_ratio: float = HEADING_SIZE_RATIO,
) -> tuple[BlockType, dict[str, str]]:
    """Return the block type and any attributes the type carries."""
    stripped = text.strip()

    if not stripped:
        return BlockType.EMPTY, {}

    m = EMPTY_FIELD.match(stripped)
    if m:
        return BlockType.EMPTY, {"field": m.group(1)}

    if CHECKBOX.search(stripped):
        return BlockType.CHECKBOX, {"raw": stripped}

    if STAMP.search(stripped):
        return BlockType.STAMP, {}

    if SIGNATURE.match(stripped):
        return BlockType.SIGNATURE, {}

    larger = body_size > 0 and font_size >= body_size * heading_ratio
    if (larger or is_upper_dense) and len(stripped) <= HEADING_MAX_CHARS:
        if body_size > 0 and font_size >= body_size * HEADING_L1_RATIO:
            level = "1"
        elif larger:
            level = "2"
        else:
            level = "3"  # body-size, all-caps: a masthead or section rule on a form
        return BlockType.HEADING, {"level": level}

    if TABLE_ROW.match(stripped):
        return BlockType.TABLE, {"structure": "row_only"}

    return BlockType.PARAGRAPH, {}


def upper_density(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)
