"""Product metrics (PRD §11.5).

MLflow measures extraction. It does not measure whether the firm keeps using the thing.

Flag precision is the one that matters most: a review queue that cries wolf gets ignored,
and an ignored queue silently disables the main safety mechanism. That failure is invisible
to every extraction metric in §11.3.
"""

from __future__ import annotations

from alie import flags
from alie.devkit import fixtures
from alie.eval import product
from alie.packs import versions as pack_versions
from alie.pipeline import enqueue_case
from alie.stores import cases, corrections, db, manifest, runs
from alie.worker import drain


def _run_case(store, name: str = "hard") -> str:
    with db.session(store.db_path) as conn:
        case_id = cases.create_case(conn, name, "cnesst")
        for folder, filename in fixtures.EXPECTED[name]["bundles"].items():
            from alie.stages import ingest

            ingest.add_pdf_path(
                conn, case_id=case_id,
                path=fixtures.fixture_path(name, filename), folder_label=folder,
            )
        run_id = runs.create_run(
            conn, case_id=case_id, flags=flags.resolve(),
            pack_versions=pack_versions(), plan={},
        )
        enqueue_case(conn, run_id, case_id)
    drain()
    return run_id


def test_an_unreviewed_case_reports_unknown_not_perfect(store):
    """Counting an untouched case as agreement would make flag precision rise every time
    the tool goes unused."""
    run_id = _run_case(store)
    with db.session(store.db_path) as conn:
        got = product.for_run(conn, run_id)

    assert not got.reviewed
    assert "not yet reviewed" in got.summary()
    assert "unknown, not perfect" in got.summary()


def test_a_correction_on_a_flagged_row_counts_the_flag_as_earned(store, env_actor):
    """`hard` is built to flag: undated, ambiguous, illegible. Correcting one is evidence
    the queue was worth reading."""
    run_id = _run_case(store)
    with db.session(store.db_path) as conn:
        run = runs.get_run(conn, run_id)
        flagged_unit = next(
            u for u in manifest.units_for_case(conn, run["case_id"])
            if u.row_date and str(u.row_date.status) in ("undated", "ambiguous", "illegible")
        )
        corrections.apply(
            conn, case_id=run["case_id"], subject_type="unit", subject_id=flagged_unit.id,
            field="row_date", new_value="2023-08-03", actor="test",
        )
        got = product.for_run(conn, run_id)

    assert got.reviewed
    assert got.flag_precision.flagged > 0
    assert got.flag_precision.corrected >= 1
    assert got.flag_precision.precision is not None


def test_a_correction_the_queue_never_flagged_is_counted_as_missed(store, env_actor):
    """The more dangerous direction. A flag that never fires costs a case; a flag that
    fires too often costs patience."""
    run_id = _run_case(store, "tiny")
    with db.session(store.db_path) as conn:
        run = runs.get_run(conn, run_id)
        # `tiny` is the happy path: every row is clean, so any correction is a miss.
        clean = manifest.units_for_case(conn, run["case_id"])[0]
        corrections.apply(
            conn, case_id=run["case_id"], subject_type="unit", subject_id=clean.id,
            field="doc_class", new_value="note_consultation", actor="test",
        )
        got = product.for_run(conn, run_id)

    assert got.flag_precision.missed >= 1
    assert got.flag_precision.recall is not None


def test_precision_over_nothing_flagged_is_unknown_not_full_marks(store):
    """A rate over zero is not 100%."""
    empty = product.FlagPrecision(flagged=0, corrected=0, missed=0)

    assert empty.precision is None
    assert empty.recall is None


def test_rows_accepted_without_an_edit_is_the_headline(store):
    """The number that answers "is this saving her time" (§11.5)."""
    run_id = _run_case(store, "tiny")
    with db.session(store.db_path) as conn:
        got = product.for_run(conn, run_id)

    assert got.rows > 0
    assert got.accepted_unedited == got.rows
    assert got.accept_rate == 1.0


def test_time_to_first_draft_comes_from_the_run_itself(store):
    run_id = _run_case(store, "tiny")
    with db.session(store.db_path) as conn:
        got = product.for_run(conn, run_id)

    assert got.seconds_to_draft is not None
    assert got.seconds_to_draft >= 0
