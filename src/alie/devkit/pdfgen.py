"""Minimal PDF page painter for synthetic fixtures.

Fixtures are synthetic. Real case files never enter the repo (PRD §13.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

WIDTH, HEIGHT = LETTER
MARGIN = 62
LINE = 15


@dataclass
class Page:
    """One synthetic page. `lines` are drawn top-down from the margin."""

    lines: list[str] = field(default_factory=list)
    #: Printed page label, drawn in the footer. This is what column 2 renders (§8.1).
    printed_label: str | None = None
    #: Draw a fax banner across the top — the dedupe transmission axis (§10.1).
    fax_banner: str | None = None
    #: Draw noise instead of text: an image-only scan with no usable text layer.
    image_only: bool = False


def _draw_noise(c: canvas.Canvas) -> None:
    c.setFillGray(0.72)
    y = HEIGHT - MARGIN
    row = 0
    while y > MARGIN:
        x = MARGIN
        while x < WIDTH - MARGIN:
            w = 6 + ((row * 7 + int(x)) % 23)
            c.rect(x, y, w, 4.5, stroke=0, fill=1)
            x += w + 5
        y -= 13
        row += 1
    c.setFillGray(0)


def write_pdf(path: Path, pages: list[Page]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    for page in pages:
        if page.image_only:
            _draw_noise(c)
        else:
            y = HEIGHT - MARGIN
            if page.fax_banner:
                c.setFont("Helvetica-Oblique", 8)
                c.drawString(MARGIN, HEIGHT - 34, page.fax_banner)
                y -= 6
            c.setFont("Helvetica", 10)
            for line in page.lines:
                if line.startswith("# "):
                    c.setFont("Helvetica-Bold", 13)
                    c.drawString(MARGIN, y, line[2:])
                    c.setFont("Helvetica", 10)
                    y -= LINE + 5
                elif line.startswith("## "):
                    c.setFont("Helvetica-Bold", 11)
                    c.drawString(MARGIN, y, line[3:])
                    c.setFont("Helvetica", 10)
                    y -= LINE + 2
                else:
                    c.drawString(MARGIN, y, line)
                    y -= LINE
        if page.printed_label:
            c.setFont("Helvetica", 8)
            c.drawCentredString(WIDTH / 2, MARGIN - 26, page.printed_label)
        c.showPage()
    c.save()
    return path
