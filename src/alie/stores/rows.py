"""Rendered rows, their bullets and their locators.

Rows are a projection of the manifest (PRD §3.1). They are written per run and never
edited in place — corrections go to the manifest and the chronology regenerates (§10.2).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from ..models import Bullet, Citation, Row, RowDate, RowStatus, Span
from .db import new_id


def replace_for_run(conn: sqlite3.Connection, run_id: str, rows: list[Row]) -> None:
    old = [r["id"] for r in conn.execute("SELECT id FROM rows_out WHERE run_id = ?", (run_id,))]
    for rid in old:
        conn.execute("DELETE FROM row_bullets WHERE row_id = ?", (rid,))
        conn.execute("DELETE FROM row_locators WHERE row_id = ?", (rid,))
    conn.execute("DELETE FROM rows_out WHERE run_id = ?", (run_id,))

    for ord_, row in enumerate(rows):
        rd = row.row_date
        conn.execute(
            """INSERT INTO rows_out (id, run_id, case_id, date_value, date_status,
                                     date_rule, date_explanation, date_alternatives,
                                     title, author, doc_class, regime, unit_ids,
                                     illegible_reason, second_hand, ord)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row.id,
                run_id,
                row.case_id,
                rd.value.isoformat() if rd.value else None,
                str(rd.status),
                rd.rule,
                rd.explanation,
                json.dumps([d.isoformat() for d in rd.alternatives]),
                row.title,
                row.author,
                row.doc_class,
                row.regime,
                json.dumps(row.unit_ids),
                row.illegible_reason,
                1 if row.second_hand else 0,
                ord_,
            ),
        )
        conn.executemany(
            """INSERT INTO row_bullets (id, row_id, ord, text, confidence, rule, bundle_id,
                                        pdf_index, printed_label, unit_id, block_id,
                                        span_start, span_end)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    new_id("bul"),
                    row.id,
                    i,
                    b.text,
                    b.confidence,
                    b.rule,
                    b.citation.bundle_id,
                    b.citation.pdf_index,
                    b.citation.printed_label,
                    b.citation.unit_id,
                    b.citation.span.block_id if b.citation.span else None,
                    b.citation.span.start if b.citation.span else None,
                    b.citation.span.end if b.citation.span else None,
                )
                for i, b in enumerate(row.bullets)
            ],
        )
        conn.executemany(
            """INSERT INTO row_locators (row_id, ord, bundle_id, folder_label, pdf_index,
                                         printed_label, unit_id)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (
                    row.id,
                    i,
                    c.bundle_id,
                    _folder_label(conn, c.bundle_id),
                    c.pdf_index,
                    c.printed_label,
                    c.unit_id,
                )
                for i, c in enumerate(row.locators)
            ],
        )


def _folder_label(conn: sqlite3.Connection, bundle_id: str) -> str:
    r = conn.execute("SELECT folder_label FROM bundles WHERE id = ?", (bundle_id,)).fetchone()
    return r["folder_label"] if r else ""


def for_run(conn: sqlite3.Connection, run_id: str) -> list[Row]:
    out: list[Row] = []
    for r in conn.execute("SELECT * FROM rows_out WHERE run_id = ? ORDER BY ord", (run_id,)):
        row = Row(
            id=r["id"],
            case_id=r["case_id"],
            row_date=RowDate(
                value=date.fromisoformat(r["date_value"]) if r["date_value"] else None,
                status=RowStatus(r["date_status"]),
                role=None,
                rule=r["date_rule"],
                explanation=r["date_explanation"],
                alternatives=tuple(
                    date.fromisoformat(s) for s in json.loads(r["date_alternatives"])
                ),
            ),
            title=r["title"],
            author=r["author"],
            doc_class=r["doc_class"],
            regime=r["regime"],
            unit_ids=json.loads(r["unit_ids"]),
            illegible_reason=r["illegible_reason"],
            second_hand=bool(r["second_hand"]),
        )
        row.bullets = [
            Bullet(
                text=b["text"],
                confidence=b["confidence"],
                rule=b["rule"],
                citation=Citation(
                    bundle_id=b["bundle_id"],
                    pdf_index=b["pdf_index"],
                    printed_label=b["printed_label"],
                    unit_id=b["unit_id"],
                    span=(
                        Span(b["block_id"], b["span_start"], b["span_end"])
                        if b["block_id"] is not None
                        else None
                    ),
                ),
            )
            for b in conn.execute(
                "SELECT * FROM row_bullets WHERE row_id = ? ORDER BY ord", (r["id"],)
            )
        ]
        row.locators = [
            Citation(
                bundle_id=lo["bundle_id"],
                pdf_index=lo["pdf_index"],
                printed_label=lo["printed_label"],
                unit_id=lo["unit_id"],
            )
            for lo in conn.execute(
                "SELECT * FROM row_locators WHERE row_id = ? ORDER BY ord", (r["id"],)
            )
        ]
        out.append(row)
    return out


def locator_labels(conn: sqlite3.Connection, row_id: str) -> list[dict]:
    return [
        dict(lo)
        for lo in conn.execute(
            "SELECT * FROM row_locators WHERE row_id = ? ORDER BY ord", (row_id,)
        )
    ]
