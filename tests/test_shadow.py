"""Shadow mode (PRD §9.1, §9.3).

Run a candidate over the same input as the incumbent, diff the results, report the
disagreements. That is how you choose an engine instead of guessing.

Nothing is promoted automatically. The comparison is the product; the decision is human.
"""

from __future__ import annotations

import pytest

from alie import eval as eval_kit
from alie.eval import shadow as shadow_kit
from alie.stores import db


def test_a_flag_that_helps_shows_the_metric_it_moved(store):
    """`manifest.orphan_rejoin` asks how common non-contiguous units are, and its metric is
    the boundary precision delta (§9.2). Shadow mode is where that number comes from."""
    with db.session(store.db_path) as conn:
        got = eval_kit.compare_flag(
            conn, eval_kit.load("hard"), flag="manifest.orphan_rejoin", candidate=True
        )

    moved = {d.name: d for d in got.improved}
    assert "manifest.boundary_exact" in moved
    assert moved["manifest.boundary_exact"].delta > 0
    assert got.safe


def test_regime_screening_moves_its_own_metric(store):
    """A flag that demonstrably re-tags a document must not read as "no metric moved" —
    which it did until the gold scored regime per unit (§6.1)."""
    with db.session(store.db_path) as conn:
        got = eval_kit.compare_flag(
            conn, eval_kit.load("mixed"), flag="screen.per_unit_regime", candidate=True
        )

    moved = {d.name: d for d in got.improved}
    assert moved["manifest.regime_agreement"].delta > 0


def test_output_movement_is_reported_separately_from_score(store):
    """Dedupe on does not improve extraction accuracy — it removes duplicate rows, which
    may *lower* row recall against a gold that contains them. That is correct behaviour,
    not a regression (§9.3). So movement is reported, never scored."""
    with db.session(store.db_path) as conn:
        got = eval_kit.compare_flag(
            conn, eval_kit.load("hard"), flag="manifest.orphan_rejoin", candidate=True
        )

    assert got.rows_total > 0
    assert got.rows_changed > 0
    assert "movement is not a score" in got.summary()


def test_more_than_one_variable_is_refused(store):
    """Ten flags is 1,024 configurations and the golds cannot be run against all of them.
    A grab-bag config that scores better tells you nothing about which change earned it
    (§9.3)."""
    with db.session(store.db_path) as conn, pytest.raises(shadow_kit.NotOneVariable) as exc:
        shadow_kit.compare(
            conn,
            eval_kit.load("tiny"),
            baseline={},
            candidate={"manifest.orphan_rejoin": True, "parse.ocr": False},
        )

    # The error names every flag that moved, so the fix is obvious.
    assert "manifest.orphan_rejoin" in str(exc.value)
    assert "parse.ocr" in str(exc.value)


def test_comparing_a_config_against_itself_is_refused(store):
    """A shadow run with nothing varying produces a reassuring zero and answers nothing."""
    with db.session(store.db_path) as conn, pytest.raises(shadow_kit.NotOneVariable):
        shadow_kit.compare(conn, eval_kit.load("tiny"), baseline={}, candidate={})


def test_the_guard_sits_where_it_can_actually_fire(store):
    """`compare_flag` builds the candidate from the baseline, so exactly one flag always
    differs. A guard there could never fire — it would be reassurance, not a check."""
    with db.session(store.db_path) as conn:
        got = eval_kit.compare_flag(
            conn,
            eval_kit.load("tiny"),
            flag="manifest.orphan_rejoin",
            candidate=True,
            baseline={"parse.ocr": False},
        )

    # A deviating baseline is legitimate: §9.3 asks for a *fixed* baseline, not the
    # default one. What it forbids is two things moving at once.
    assert got.variable == "manifest.orphan_rejoin"


def test_a_candidate_breaking_a_must_hold_is_not_adoptable():
    """Must-holds are absolute. A candidate that breaks one is not a candidate, whatever
    its metrics did (§11.3)."""
    got = shadow_kit.Shadow(
        gold_id="x", variable="f", baseline_value=False, candidate_value=True,
        deltas=[shadow_kit.MetricDelta("extract.field_recall", 0.5, 0.9)],
        candidate_holds=False,
    )

    assert got.improved and not got.safe
    assert "BREAKS A MUST-HOLD" in got.summary()


def test_shadow_mode_never_decides(store):
    """Promotion is a human decision. `safe` says the candidate may be considered, never
    that it should be adopted (§9.1)."""
    source = shadow_kit.__doc__ or ""
    assert "Nothing here is promoted automatically" in source

    with db.session(store.db_path) as conn:
        got = eval_kit.compare_flag(
            conn, eval_kit.load("tiny"), flag="manifest.orphan_rejoin", candidate=True
        )

    assert not hasattr(got, "promote")
    assert not hasattr(got, "adopt")


def test_a_metric_that_did_not_move_is_reported_as_such(store):
    with db.session(store.db_path) as conn:
        got = eval_kit.compare_flag(
            conn, eval_kit.load("tiny"), flag="render.doctype_code", candidate=True
        )

    assert [d for d in got.deltas if not d.moved]
    assert got.safe
