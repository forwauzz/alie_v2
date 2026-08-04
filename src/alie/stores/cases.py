"""Cases, bundles and pages.

The folder label a bundle is uploaded under becomes the locator name in column 2 of the
chronology; it is not cosmetic (PRD §4.1).
"""

from __future__ import annotations

import sqlite3

from ..provenance import Producer
from .db import new_id, now


def create_case(conn: sqlite3.Connection, name: str, primary_pack: str) -> str:
    case_id = new_id("case")
    conn.execute(
        "INSERT INTO cases (id, name, primary_pack, created_at) VALUES (?,?,?,?)",
        (case_id, name, primary_pack, now()),
    )
    return case_id


def get_case(conn: sqlite3.Connection, case_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return dict(r) if r else None


def list_cases(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM cases ORDER BY created_at, id")]


def add_bundle(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    filename: str,
    folder_label: str,
    content_hash: str,
    page_count: int,
) -> str:
    bundle_id = new_id("bun")
    conn.execute(
        """INSERT INTO bundles (id, case_id, filename, folder_label, content_hash,
                                page_count, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (bundle_id, case_id, filename, folder_label, content_hash, page_count, now()),
    )
    return bundle_id


def get_bundle(conn: sqlite3.Connection, bundle_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM bundles WHERE id = ?", (bundle_id,)).fetchone()
    return dict(r) if r else None


def bundles_for_case(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM bundles WHERE case_id = ? ORDER BY created_at, id", (case_id,)
        )
    ]


def replace_pages(conn: sqlite3.Connection, bundle_id: str, pages: list[dict], p: Producer) -> None:
    conn.execute("DELETE FROM pages WHERE bundle_id = ?", (bundle_id,))
    conn.executemany(
        """INSERT INTO pages (bundle_id, pdf_index, printed_label, width, height,
                              char_count, parse_source, producer)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            (
                bundle_id,
                pg["pdf_index"],
                pg.get("printed_label"),
                pg["width"],
                pg["height"],
                pg["char_count"],
                pg["parse_source"],
                p.to_json(),
            )
            for pg in pages
        ],
    )


def pages_for_bundle(conn: sqlite3.Connection, bundle_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM pages WHERE bundle_id = ? ORDER BY pdf_index", (bundle_id,)
        )
    ]


def printed_labels(conn: sqlite3.Connection, bundle_id: str) -> dict[int, str | None]:
    return {p["pdf_index"]: p["printed_label"] for p in pages_for_bundle(conn, bundle_id)}
