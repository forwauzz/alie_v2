"""Fax transmission banners (PRD §4.3 `stamp`, §10.1 transmission axis).

Every page of a faxed bundle carries a banner naming the transmission and the page's
position inside it:

    4/1/2025, 6:35 AM PDT TO: +14503042733 FROM: 14505000776 PAGE 4/15
    12/3/2024 15:37:52 EST To: 145050009776 Page: 1/1 From: Radiologix Fax

On scanned bundles this is often the *only* reliable boundary signal. Headings arrive OCR
damaged and mastheads repeat on every sheet, but a change of transmission is unambiguous,
and so is a gap in the page count — which is also a finding in its own right: `7/15`
followed by `10/15` means two pages of that fax are not in the file the firm was sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `PAGE 4/15`, `Page: 1/1`, `p. 3/8`.
PAGE_OF = re.compile(r"\bp(?:age)?\.?\s*:?\s*(\d{1,3})\s*/\s*(\d{1,3})\b", re.IGNORECASE)

#: The leading timestamp identifies the transmission. Two faxes from the same clinic on
#: the same day differ by their send time.
SENT_AT = re.compile(
    r"\b(\d{1,2}/\d{1,2}/\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?)\s*(AM|PM)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Transmission:
    """One page's position within one fax transmission."""

    sent: str
    position: int
    total: int

    @property
    def key(self) -> str:
        """Identity of the transmission this page belongs to."""
        return f"{self.sent}#{self.total}"

    def follows(self, other: Transmission | None) -> bool:
        """True when this page is the next sheet of the same transmission."""
        if other is None:
            return False
        return self.key == other.key and self.position == other.position + 1

    @property
    def opens(self) -> bool:
        return self.position == 1


def parse_banner(text: str) -> Transmission | None:
    page = PAGE_OF.search(text)
    if not page:
        return None
    position, total = int(page.group(1)), int(page.group(2))
    if position > total or total == 0:
        return None

    sent = SENT_AT.search(text)
    stamp = ""
    if sent:
        stamp = " ".join(p for p in (sent.group(1), sent.group(2), sent.group(3)) if p)
    return Transmission(sent=stamp, position=position, total=total)


def first_in(texts: list[str]) -> Transmission | None:
    for text in texts:
        found = parse_banner(text)
        if found:
            return found
    return None


def missing_pages(previous: Transmission | None, current: Transmission | None) -> int:
    """Sheets of the same transmission that are absent between two pages.

    Zero when the pages are consecutive, or when they belong to different transmissions —
    a gap can only be asserted within one fax.
    """
    if previous is None or current is None or previous.key != current.key:
        return 0
    return max(0, current.position - previous.position - 1)
