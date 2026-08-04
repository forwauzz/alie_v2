"""Manifest store — report units, their dates, and the engine's date decision.

The manifest is the product (PRD §3.1). Corrections land here as `status: manual` and
survive re-runs (§10.2).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from ..models import DateFact, DateRole, Legibility, ReportUnit, RowDate, RowStatus, UnitKind
from ..provenance import Producer
from .db import new_id


def _to_unit(r: sqlite3.Row) -> ReportUnit:
    return ReportUnit(
        id=r["id"],
        bundle_id=r["bundle_id"],
        case_id=r["case_id"],
        pages=tuple(json.loads(r["pages"])),
        doc_class=r["doc_class"],
        class_confidence=r["class_confidence"],
        class_source=r["class_source"],
        regime=r["regime"],
        legibility=Legibility(r["legibility"]),
        author=r["author"],
        form_serial=r["form_serial"],
        form_revision=r["form_revision"],
        kind=UnitKind(r["kind"]),
        cited_by_unit_id=r["cited_by_unit_id"],
        excluded_by=r["excluded_by"],
        attrs=json.loads(r["attrs"]),
    )


def upsert_unit(conn: sqlite3.Connection, u: ReportUnit, producer: Producer) -> None:
    conn.execute(
        """INSERT INTO units (id, case_id, bundle_id, pages, doc_class, class_confidence,
                              class_source, regime, legibility, author, form_serial,
                              form_revision, kind, cited_by_unit_id, excluded_by, attrs,
                              producer)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (id) DO UPDATE SET
             pages=excluded.pages, doc_class=excluded.doc_class,
             class_confidence=excluded.class_confidence,
             class_source=excluded.class_source, regime=excluded.regime,
             legibility=excluded.legibility, author=excluded.author,
             form_serial=excluded.form_serial, form_revision=excluded.form_revision,
             kind=excluded.kind, cited_by_unit_id=excluded.cited_by_unit_id,
             excluded_by=excluded.excluded_by, attrs=excluded.attrs,
             producer=excluded.producer""",
        (
            u.id,
            u.case_id,
            u.bundle_id,
            json.dumps(list(u.pages)),
            u.doc_class,
            u.class_confidence,
            u.class_source,
            u.regime,
            str(u.legibility),
            u.author,
            u.form_serial,
            u.form_revision,
            str(u.kind),
            u.cited_by_unit_id,
            u.excluded_by,
            json.dumps(u.attrs, ensure_ascii=False, sort_keys=True),
            producer.to_json(),
        ),
    )


def replace_dates(conn: sqlite3.Connection, unit_id: str, facts: list[DateFact]) -> None:
    conn.execute("DELETE FROM unit_dates WHERE unit_id = ?", (unit_id,))
    conn.executemany(
        """INSERT INTO unit_dates (id, unit_id, role, readings, raw, block_id, span_start,
                                   span_end, pdf_index, confidence, century_inferred)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                new_id("dt"),
                unit_id,
                str(f.role),
                json.dumps([d.isoformat() for d in f.readings]),
                f.raw,
                f.block_id,
                f.start,
                f.end,
                f.pdf_index,
                f.confidence,
                1 if f.century_inferred else 0,
            )
            for f in facts
        ],
    )


def dates_for_unit(conn: sqlite3.Connection, unit_id: str) -> list[DateFact]:
    rows = conn.execute(
        "SELECT * FROM unit_dates WHERE unit_id = ? ORDER BY pdf_index, span_start", (unit_id,)
    ).fetchall()
    return [
        DateFact(
            role=DateRole(r["role"]),
            readings=tuple(date.fromisoformat(s) for s in json.loads(r["readings"])),
            raw=r["raw"],
            block_id=r["block_id"],
            start=r["span_start"],
            end=r["span_end"],
            pdf_index=r["pdf_index"],
            confidence=r["confidence"],
            century_inferred=bool(r["century_inferred"]),
        )
        for r in rows
    ]


def set_row_date(conn: sqlite3.Connection, unit_id: str, rd: RowDate) -> None:
    conn.execute(
        """INSERT INTO unit_row_dates (unit_id, value, status, role, rule, explanation,
                                       alternatives)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (unit_id) DO UPDATE SET
             value=excluded.value, status=excluded.status, role=excluded.role,
             rule=excluded.rule, explanation=excluded.explanation,
             alternatives=excluded.alternatives""",
        (
            unit_id,
            rd.value.isoformat() if rd.value else None,
            str(rd.status),
            str(rd.role) if rd.role else None,
            rd.rule,
            rd.explanation,
            json.dumps([d.isoformat() for d in rd.alternatives]),
        ),
    )


def get_row_date(conn: sqlite3.Connection, unit_id: str) -> RowDate | None:
    r = conn.execute("SELECT * FROM unit_row_dates WHERE unit_id = ?", (unit_id,)).fetchone()
    if not r:
        return None
    return RowDate(
        value=date.fromisoformat(r["value"]) if r["value"] else None,
        status=RowStatus(r["status"]),
        role=DateRole(r["role"]) if r["role"] else None,
        rule=r["rule"],
        explanation=r["explanation"],
        alternatives=tuple(date.fromisoformat(s) for s in json.loads(r["alternatives"])),
    )


def _hydrate(conn: sqlite3.Connection, unit: ReportUnit) -> ReportUnit:
    """A unit without its row date is not usable by any caller, so loading is not
    optional — an unhydrated unit would read as `None` and crash far from here."""
    unit.row_date = get_row_date(conn, unit.id)
    unit.dates = tuple(dates_for_unit(conn, unit.id))
    return unit


def units_for_case(conn: sqlite3.Connection, case_id: str) -> list[ReportUnit]:
    rows = conn.execute(
        "SELECT * FROM units WHERE case_id = ? ORDER BY bundle_id, id", (case_id,)
    ).fetchall()
    return [_hydrate(conn, _to_unit(r)) for r in rows]


def units_for_bundle(conn: sqlite3.Connection, bundle_id: str) -> list[ReportUnit]:
    rows = conn.execute(
        "SELECT * FROM units WHERE bundle_id = ? ORDER BY id", (bundle_id,)
    ).fetchall()
    return [_hydrate(conn, _to_unit(r)) for r in rows]


def get_unit(conn: sqlite3.Connection, unit_id: str) -> ReportUnit | None:
    r = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    return _hydrate(conn, _to_unit(r)) if r else None


def delete_units_for_bundle(conn: sqlite3.Connection, bundle_id: str) -> None:
    ids = [r["id"] for r in conn.execute("SELECT id FROM units WHERE bundle_id = ?", (bundle_id,))]
    for uid in ids:
        conn.execute("DELETE FROM unit_dates WHERE unit_id = ?", (uid,))
        conn.execute("DELETE FROM unit_row_dates WHERE unit_id = ?", (uid,))
        conn.execute("DELETE FROM records WHERE unit_id = ?", (uid,))
    conn.execute("DELETE FROM units WHERE bundle_id = ?", (bundle_id,))
