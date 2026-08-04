"""Block store. Immutable; reparse replaces wholesale (PRD §4.5)."""

from __future__ import annotations

import json
import sqlite3

from ..models import BBox, Block, BlockSource, BlockType
from ..provenance import Producer


def _to_block(r: sqlite3.Row) -> Block:
    return Block(
        id=r["id"],
        bundle_id=r["bundle_id"],
        pdf_index=r["pdf_index"],
        type=BlockType(r["type"]),
        text=r["text"],
        bbox=BBox(r["x0"], r["y0"], r["x1"], r["y1"]),
        source=BlockSource(r["source"]),
        confidence=r["confidence"],
        order=r["ord"],
        attrs=json.loads(r["attrs"]),
    )


def replace_bundle(
    conn: sqlite3.Connection, bundle_id: str, blocks: list[Block], producer: Producer
) -> None:
    conn.execute("DELETE FROM blocks WHERE bundle_id = ?", (bundle_id,))
    conn.executemany(
        """INSERT INTO blocks (id, bundle_id, pdf_index, type, text, x0, y0, x1, y1,
                               source, confidence, ord, attrs, producer)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                b.id,
                b.bundle_id,
                b.pdf_index,
                str(b.type),
                b.text,
                b.bbox.x0,
                b.bbox.y0,
                b.bbox.x1,
                b.bbox.y1,
                str(b.source),
                b.confidence,
                b.order,
                json.dumps(b.attrs, ensure_ascii=False, sort_keys=True),
                producer.to_json(),
            )
            for b in blocks
        ],
    )


def for_bundle(conn: sqlite3.Connection, bundle_id: str) -> list[Block]:
    rows = conn.execute(
        "SELECT * FROM blocks WHERE bundle_id = ? ORDER BY pdf_index, ord", (bundle_id,)
    ).fetchall()
    return [_to_block(r) for r in rows]


def for_pages(conn: sqlite3.Connection, bundle_id: str, pages: tuple[int, ...]) -> list[Block]:
    if not pages:
        return []
    marks = ",".join("?" for _ in pages)
    rows = conn.execute(
        f"SELECT * FROM blocks WHERE bundle_id = ? AND pdf_index IN ({marks}) "
        "ORDER BY pdf_index, ord",
        (bundle_id, *pages),
    ).fetchall()
    return [_to_block(r) for r in rows]


def by_id(conn: sqlite3.Connection, block_id: str) -> Block | None:
    r = conn.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()
    return _to_block(r) if r else None


def count_for_bundle(conn: sqlite3.Connection, bundle_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM blocks WHERE bundle_id = ?", (bundle_id,)
    ).fetchone()["n"]
