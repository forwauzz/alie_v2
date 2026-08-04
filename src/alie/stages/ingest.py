"""Upload — files land in a case with a folder label (PRD §4.1).

The label (`Médical`, `CHUM`, `TAT`) becomes the locator name in column 2 of the
chronology. It is not cosmetic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..parse import pdfium as pdfium_io
from ..provenance import hash_bytes
from ..stores import audit, blobs, cases


def add_pdf(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    data: bytes,
    filename: str,
    folder_label: str,
) -> str:
    content_hash = hash_bytes(data)
    blobs.put(data, key=content_hash)

    page_count = pdfium_io.page_count(str(blobs.path_for(content_hash)))

    bundle_id = cases.add_bundle(
        conn,
        case_id=case_id,
        filename=filename,
        folder_label=folder_label,
        content_hash=content_hash,
        page_count=page_count,
    )
    audit.record(
        conn,
        subject_type="bundle",
        subject_id=bundle_id,
        action="upload",
        rule="stage.ingest",
        detail={
            "filename": filename,
            "folder_label": folder_label,
            "pages": page_count,
            "content_hash": content_hash,
        },
    )
    return bundle_id


def add_pdf_path(
    conn: sqlite3.Connection, *, case_id: str, path: Path, folder_label: str
) -> str:
    return add_pdf(
        conn,
        case_id=case_id,
        data=path.read_bytes(),
        filename=path.name,
        folder_label=folder_label,
    )
