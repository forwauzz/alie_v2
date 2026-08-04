"""Step (c) — classify: serial, zones, model fallback (PRD §4.4).

Signals are tried cheapest-first. A form serial is printed by the issuing body rather than
typed by a clinician, so it outranks any heading. Below the pack's minimum confidence the
classifier fallback runs — one of the four places judgement is irreducible (§5). Phase 1
configures no model, so a low-confidence unit stays `unknown` and is flagged rather than
guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Block, BlockType
from ..packs import Pack


@dataclass(frozen=True)
class Classification:
    doc_class: str
    confidence: float
    source: str  # "serial" | "zones" | "model" | "manual"
    matched: tuple[str, ...]
    needs_fallback: bool


def _search(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def classify(blocks: list[Block], pack: Pack, *, serial: str | None = None) -> Classification:
    if not blocks:
        return Classification(pack.unknown_class, 0.0, "zones", (), needs_fallback=False)

    if serial:
        for spec in pack.class_list:
            if serial in [str(s) for s in spec.get("serials", [])]:
                return Classification(
                    spec["id"], spec.get("confidence", 0.95), "serial", (f"serial:{serial}",),
                    needs_fallback=False,
                )

    heading_text = "\n".join(b.text for b in blocks if b.type is BlockType.HEADING)
    body_text = "\n".join(b.text for b in blocks if b.is_body_text)

    best: Classification | None = None
    for spec in pack.class_list:
        heading_hits = _search(spec.get("headings", []), heading_text)
        body_hits = _search(spec.get("body", []), body_text)
        if not heading_hits and not body_hits:
            continue

        # A heading match is worth far more than a body keyword: `conclusion` appears in
        # every imaging report and in plenty of documents that are not one. A clean
        # heading match must clear the pack's own threshold on its own, or every
        # well-formed document falls through to the model fallback.
        if heading_hits and body_hits:
            strength = 1.0
        elif heading_hits:
            strength = 0.9
        else:
            strength = 0.5
        confidence = round(spec.get("confidence", 0.8) * strength, 4)
        candidate = Classification(
            spec["id"],
            confidence,
            "zones",
            tuple(heading_hits + body_hits),
            needs_fallback=False,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    if best is None or best.confidence < pack.min_class_confidence:
        return Classification(
            pack.unknown_class,
            best.confidence if best else 0.0,
            "zones",
            best.matched if best else (),
            needs_fallback=True,
        )
    return best
