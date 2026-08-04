"""OCR parse tier — Tesseract (PRD §4.3, §9.2 `parse.ocr`).

The tier the free path hands off to. Measured on case 1, the free path reads 9% of report
units and 55% of pages carry no text layer at all, so this is not an enhancement over a
working baseline — it is most of the bundle.

Tesseract is called as a binary rather than through a wrapper library: the TSV output
gives word-level boxes and per-word confidence, which is what a block needs. A library
that returns a string would throw away the anchoring that makes citations possible (§4.3).

Config and secrets never live in code (§13.4):
  ALIE_TESSERACT_EXE   path to the binary
  ALIE_TESSDATA_DIR    directory holding `fra.traineddata`
  ALIE_OCR_LANG        language, default `fra`
  ALIE_OCR_SCALE       render scale relative to 72 dpi, default 3 (216 dpi)
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import REPO_ROOT
from ..models import BBox, Block, BlockSource, BlockType
from ..provenance import hash_text
from ..seams.parser import PageInput
from . import blocktype, pagelabel
from . import pdfium as pdfium_io
from .textquality import word_likeness

#: Where Tesseract usually lands on Windows when installed by the standard installer.
_FALLBACK_EXES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)

DEFAULT_SCALE = 3.0
DEFAULT_LANG = "fra"

#: Words Tesseract is this unsure of are dropped. It reports -1 for non-text regions and
#: single digits for hallucinated marks on speckle; keeping those would recreate the
#: problem the legibility gate exists to solve.
MIN_WORD_CONFIDENCE = 30.0

#: Heading threshold for this tier. The text layer reports a real font size; OCR has only
#: the line's bounding box, whose height moves with capitals, accents and descenders — a
#: line of `Nom du Patient HUARD, Eric Date de naïissance` measures taller than plain
#: lowercase prose without being a heading. A stricter ratio keeps body text out of the
#: heading class, which matters because headings do not become bullets: misreading one
#: would silently drop clinical content from the chronology.
OCR_HEADING_RATIO = 1.35


class TesseractMissing(RuntimeError):
    """The OCR tier is enabled but the binary or language data is not available."""


@dataclass(frozen=True)
class OcrConfig:
    exe: str
    tessdata_dir: str | None
    lang: str
    scale: float

    @property
    def version_tag(self) -> str:
        """Stamped onto every block this tier produces, so switching engines recomputes
        only the pages this one touched (§9 rule 2)."""
        return f"tesseract-{self.lang}@{self.scale:g}"


def load_config() -> OcrConfig:
    exe = os.environ.get("ALIE_TESSERACT_EXE") or shutil.which("tesseract")
    if not exe:
        exe = next((p for p in _FALLBACK_EXES if Path(p).exists()), "")
    tessdata = os.environ.get("ALIE_TESSDATA_DIR")
    if not tessdata:
        local = REPO_ROOT / ".tessdata"
        tessdata = str(local) if local.is_dir() else None
    return OcrConfig(
        exe=exe,
        tessdata_dir=tessdata,
        lang=os.environ.get("ALIE_OCR_LANG", DEFAULT_LANG),
        scale=float(os.environ.get("ALIE_OCR_SCALE", DEFAULT_SCALE)),
    )


def available(config: OcrConfig | None = None) -> bool:
    config = config or load_config()
    return bool(config.exe) and Path(config.exe).exists()


class OcrParser:
    """Implements the `PageParser` protocol. Registered only when `parse.ocr` is on."""

    tier = BlockSource.OCR

    def __init__(self, config: OcrConfig | None = None) -> None:
        self.config = config or load_config()

    def can_handle(self, page: PageInput) -> bool:
        return available(self.config)

    def parse(self, page: PageInput) -> list[Block]:
        return ocr_page(page, self.config)


def _run_tesseract(image_png: bytes, config: OcrConfig) -> str:
    env = dict(os.environ)
    if config.tessdata_dir:
        env["TESSDATA_PREFIX"] = config.tessdata_dir

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "page.png"
        src.write_bytes(image_png)
        base = Path(tmp) / "out"
        # `-c tessedit_create_tsv=1` rather than the `tsv` config file. The config file
        # lives in the install's own tessdata, and TESSDATA_PREFIX repoints Tesseract at
        # our language directory, where it does not exist — Tesseract then reports
        # `Can't open tsv` on stderr, exits 0, and silently emits plain text instead.
        # Losing the word boxes that way would cost every citation its anchor.
        result = subprocess.run(
            [
                config.exe, str(src), str(base),
                "-l", config.lang, "--psm", "3",
                "-c", "tessedit_create_tsv=1",
            ],
            capture_output=True,
            env=env,
            timeout=180,
        )
        if result.returncode != 0:
            raise TesseractMissing(
                f"tesseract exited {result.returncode}: "
                f"{result.stderr.decode('utf-8', 'replace')[:400]}"
            )
        tsv = base.with_suffix(".tsv")
        if not tsv.exists():
            raise TesseractMissing(
                "tesseract produced no TSV; word boxes are required for citation anchors. "
                f"stderr: {result.stderr.decode('utf-8', 'replace')[:300]}"
            )
        return tsv.read_text(encoding="utf-8", errors="replace")


def _lines_from_tsv(tsv: str) -> list[tuple[str, tuple[int, int, int, int], float]]:
    """Group Tesseract's word rows into lines: `(text, pixel_bbox, confidence)`.

    Lines rather than words, so this tier's blocks have the same granularity as the text
    layer's and everything downstream stays indifferent to which tier produced them.
    """
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if row.get("level") != "5":  # 5 = word
            continue
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf", -1))
        except ValueError:
            continue
        if not text or conf < MIN_WORD_CONFIDENCE:
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        grouped.setdefault(key, []).append(
            {
                "text": text,
                "left": int(row["left"]),
                "top": int(row["top"]),
                "right": int(row["left"]) + int(row["width"]),
                "bottom": int(row["top"]) + int(row["height"]),
                "conf": conf,
            }
        )

    lines = []
    for words in grouped.values():
        words.sort(key=lambda w: w["left"])
        text = " ".join(w["text"] for w in words)
        bbox = (
            min(w["left"] for w in words),
            min(w["top"] for w in words),
            max(w["right"] for w in words),
            max(w["bottom"] for w in words),
        )
        confidence = sum(w["conf"] for w in words) / len(words) / 100.0
        lines.append((text, bbox, confidence))
    lines.sort(key=lambda line: (line[1][1], line[1][0]))
    return lines


def ocr_page(page: PageInput, config: OcrConfig | None = None) -> list[Block]:
    config = config or load_config()
    if not available(config):
        raise TesseractMissing(
            "parse.ocr is enabled but tesseract was not found. Set ALIE_TESSERACT_EXE."
        )

    image = pdfium_io.render_page(page.pdf_path, page.pdf_index, config.scale)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    lines = _lines_from_tsv(_run_tesseract(buffer.getvalue(), config))
    if not lines:
        return []

    # Tesseract works in image pixels; blocks are anchored in PDF points so a citation
    # means the same thing whatever tier produced it (§4.3).
    scale = config.scale
    sizes = [
        max(1.0, (bbox[3] - bbox[1]) / scale) for _, bbox, _ in lines
    ]
    body_size = sorted(sizes)[len(sizes) // 2]

    blocks: list[Block] = []
    for order, (text, (left, top, right, bottom), confidence) in enumerate(lines):
        bbox = BBox(x0=left / scale, y0=top / scale, x1=right / scale, y1=bottom / scale)
        blocks.append(
            _make_block(page, order, text, bbox, bbox.height, body_size, confidence, config)
        )
    return blocks


def _make_block(
    page: PageInput,
    order: int,
    text: str,
    bbox: BBox,
    font_size: float,
    body_size: float,
    confidence: float,
    config: OcrConfig,
) -> Block:
    label = pagelabel.detect(text, bbox.y0, bbox.y1, page.height)
    if label:
        rule, value = label
        btype, attrs = BlockType.PAGE_LABEL, {"label": value, "rule": rule}
    else:
        btype, attrs = blocktype.infer(
            text,
            font_size=font_size,
            body_size=body_size,
            # An all-caps *line* is common on a scanned form and is not a heading.
            is_upper_dense=blocktype.upper_density(text) > 0.8 and len(text) <= 40,
            heading_ratio=OCR_HEADING_RATIO,
        )

    attrs = attrs | {"font_size": f"{font_size:g}", "engine": config.version_tag}
    if blocktype.is_degenerate_number(text):
        confidence = min(confidence, 0.55)
        attrs = attrs | {"degenerate_number": "true"}

    return Block(
        id=f"blk_{hash_text(f'{page.bundle_id}|{page.pdf_index}|ocr|{order}|{text}')[:20]}",
        bundle_id=page.bundle_id,
        pdf_index=page.pdf_index,
        type=btype,
        text=text.strip(),
        bbox=bbox,
        source=BlockSource.OCR,
        confidence=round(confidence, 4),
        order=order,
        attrs=attrs,
    )


def page_needs_ocr(page: PageInput, threshold: float) -> bool:
    """True when the free tier cannot honestly claim this page.

    Covers both an absent text layer and a present one that is noise — the second is the
    case the reference bundle is full of, and the one that silently produced confident
    garbage before the quality measure existed.
    """
    text = pdfium_io.page_text(page.pdf_path, page.pdf_index)
    return not text.strip() or word_likeness(text) < threshold
