"""Stage 6 — render (PRD §4.2).

In: rows. Out: chronology + review queue. Fails by: uncited text. Proven when: uncited = 0
and coverage = 100% (§14.2, §11.3).

Code renders the row. No `write_row` tool exists and the model never writes one (§3.2).
Packs control citation *display* only; storage is an engine invariant (§8.1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..models import Row, RowStatus
from ..packs import Pack
from ..packs import load as load_pack
from ..stores import cases, manifest


@dataclass
class Validation:
    """Release gates, not warnings. An uncited string is a validation failure (§3.5)."""

    uncited_bullets: list[str] = field(default_factory=list)
    unlocated_rows: list[str] = field(default_factory=list)
    pages_total: int = 0
    pages_covered: int = 0
    rows_without_printed_label: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.pages_covered / self.pages_total if self.pages_total else 1.0

    @property
    def passes(self) -> bool:
        return not self.uncited_bullets and not self.unlocated_rows and self.coverage == 1.0

    def as_dict(self) -> dict:
        return {
            "uncited": len(self.uncited_bullets),
            "unlocated_rows": len(self.unlocated_rows),
            "coverage": round(self.coverage, 6),
            "pages_total": self.pages_total,
            "pages_covered": self.pages_covered,
            "rows_without_printed_label": len(self.rows_without_printed_label),
            "passes": self.passes,
        }


def validate(conn: sqlite3.Connection, case_id: str, rows: list[Row]) -> Validation:
    v = Validation()

    for row in rows:
        if not row.locators:
            v.unlocated_rows.append(row.id)
        for bullet in row.bullets:
            if bullet.citation.span is None:
                v.uncited_bullets.append(f"{row.id}:{bullet.text[:40]}")
        if any(c.needs_flag for c in row.locators):
            # Display falls back to pdf_index and the row is flagged (§8.1).
            v.rows_without_printed_label.append(row.id)

    # Coverage counts pages the manifest accounts for — including pages in excluded and
    # illegible units. Nothing is dropped; missing information is a status (§3.4).
    covered: set[tuple[str, int]] = set()
    for unit in manifest.units_for_case(conn, case_id):
        covered.update((unit.bundle_id, p) for p in unit.pages)
    bundles = cases.bundles_for_case(conn, case_id)
    v.pages_total = sum(b["page_count"] for b in bundles)
    v.pages_covered = len(covered)
    return v


def locator_text(pack: Pack, folder_label: str, printed_label: str, needs_flag: bool) -> str:
    spec = pack.output.get("locator", {})
    prefix = spec.get("page_prefix", "p. ")
    joiner = "\n" if spec.get("line_break_before_page", True) else spec.get("separator", " / ")
    text = f"{folder_label}{joiner}{prefix}{printed_label}"
    return f"{text} [pdf]" if needs_flag else text


def to_markdown(conn: sqlite3.Connection, case_id: str, rows: list[Row]) -> str:
    case = cases.get_case(conn, case_id)
    pack = load_pack(case["primary_pack"])
    folders = {b["id"]: b["folder_label"] for b in cases.bundles_for_case(conn, case_id)}
    headers = [c["header"] for c in pack.output.get("columns", [])] or [
        "Date", "Document", "Contenu",
    ]

    undated = [r for r in rows if r.is_undated]
    dated = [r for r in rows if not r.is_undated]

    out: list[str] = []
    if undated:
        heading = pack.output.get("rows", {}).get(
            "undated_heading", "SANS DATE — {n} documents à dater"
        )
        out.append(f"**{heading.format(n=len(undated))}**\n")

    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in undated + dated:
        out.append(_row_markdown(row, pack, folders))
    return "\n".join(out) + "\n"


def _row_markdown(row: Row, pack: Pack, folders: dict[str, str]) -> str:
    rows_spec = pack.output.get("rows", {})
    date_cell = row.row_date.render() if not row.is_undated else "—"
    if row.row_date.status is RowStatus.AMBIGUOUS:
        marker = rows_spec.get("ambiguous_date_marker", " (?)")
        if not date_cell.endswith(marker.strip()):
            date_cell += marker

    locators = " ; ".join(
        locator_text(
            pack, folders.get(c.bundle_id, c.bundle_id), c.display_page, c.needs_flag
        ).replace("\n", "<br>")
        for c in row.locators
    )

    parts = [f"**{row.title}**"]
    if row.illegible_reason:
        parts.append(f"_{rows_spec.get('illegible_label', 'Illisible')}_ — {row.illegible_reason}")
    parts.extend(f"• {b.text}" for b in row.bullets)
    if row.warns:
        parts.append(f"⚠ confiance {row.confidence:.2f}")
    content = "<br>".join(parts)

    return f"| {date_cell} | {locators} | {content} |"


def to_json(conn: sqlite3.Connection, case_id: str, rows: list[Row]) -> list[dict]:
    folders = {b["id"]: b["folder_label"] for b in cases.bundles_for_case(conn, case_id)}
    return [
        {
            "id": row.id,
            "date": row.row_date.value.isoformat() if row.row_date.value else None,
            "date_status": str(row.row_date.status),
            "date_explanation": row.row_date.explanation,
            "date_alternatives": [d.isoformat() for d in row.row_date.alternatives],
            "title": row.title,
            "author": row.author,
            "doc_class": row.doc_class,
            "regime": row.regime,
            "confidence": round(row.confidence, 4),
            "warns": row.warns,
            "illegible_reason": row.illegible_reason,
            "second_hand": row.second_hand,
            "unit_ids": row.unit_ids,
            "locators": [
                {
                    "folder": folders.get(c.bundle_id, c.bundle_id),
                    "bundle_id": c.bundle_id,
                    "pdf_index": c.pdf_index,
                    "printed_label": c.printed_label,
                    "display_page": c.display_page,
                    "flagged": c.needs_flag,
                    "unit_id": c.unit_id,
                }
                for c in row.locators
            ],
            "bullets": [
                {
                    "text": b.text,
                    "confidence": b.confidence,
                    "rule": b.rule,
                    "citation": {
                        "bundle_id": b.citation.bundle_id,
                        "pdf_index": b.citation.pdf_index,
                        "printed_label": b.citation.printed_label,
                        "unit_id": b.citation.unit_id,
                        "block_id": b.citation.span.block_id if b.citation.span else None,
                        "start": b.citation.span.start if b.citation.span else None,
                        "end": b.citation.span.end if b.citation.span else None,
                    },
                }
                for b in row.bullets
            ],
        }
        for row in rows
    ]
