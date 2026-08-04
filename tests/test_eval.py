"""The eval harness (PRD §11).

Four metrics are release-blocking, not diagnostic: groundedness 100%, uncited 0,
coverage 100%, truncation 0. A run that fails one is a failure whatever else it scored.
"""

from __future__ import annotations

from datetime import date

from alie import eval as eval_kit
from alie.eval import gold as gold_kit
from alie.eval import scoring
from alie.stores import db


def test_every_gold_passes_its_must_holds(store):
    """The floor. If this breaks, something citable stopped being citable."""
    for name in eval_kit.available():
        with db.session(store.db_path) as conn:
            report = eval_kit.run(conn, eval_kit.load(name))
        assert report.holds, f"{name}:\n{report.summary()}"


def test_coverage_counts_pages_per_bundle(store):
    """A page index is only unique within its bundle. Two bundles of three pages each are
    six pages, not three — keying on the index alone reported 50% coverage on a case where
    every page was read."""
    with db.session(store.db_path) as conn:
        report = eval_kit.run(conn, eval_kit.load("dupes"))

    assert report.scores["render.page_coverage"] == 1.0


def test_a_gold_records_its_version_and_a_content_hash(store):
    """~30 of the 74 CNESST rows were never reverse-engineered, so golds get corrected. A
    metric jump from editing the answer key must be distinguishable from one from
    improving the system (§11.1)."""
    gold = eval_kit.load("tiny")

    assert gold.version and gold.digest
    with db.session(store.db_path) as conn:
        report = eval_kit.run(conn, gold)

    assert report.params["gold_version"] == gold.version
    assert report.params["gold_digest"] == gold.digest


def test_the_answer_key_itself_is_never_a_param(store):
    """Raw PDFs and answer keys are never logged. The hash proves which gold ran without
    duplicating patient files into a second store (§11.1, §16)."""
    with db.session(store.db_path) as conn:
        report = eval_kit.run(conn, eval_kit.load("tiny"))

    blob = " ".join(str(v) for v in report.params.values()).lower()
    assert ".pdf" not in blob
    assert "resources" not in blob


def test_the_harness_measures_a_flags_own_metric(store):
    """`manifest.orphan_rejoin` asks "how common are non-contiguous units", and its metric
    is the boundary precision delta (§9.2). That number is now computable."""
    with db.session(store.db_path) as conn:
        off = eval_kit.run(conn, eval_kit.load("hard"), flags={"manifest.orphan_rejoin": False})
    with db.session(store.db_path) as conn:
        on = eval_kit.run(conn, eval_kit.load("hard"), flags={"manifest.orphan_rejoin": True})

    assert on.scores["manifest.boundary_exact"] > off.scores["manifest.boundary_exact"]


def test_a_failed_must_hold_fails_the_report_whatever_else_scored():
    from alie.eval.scoring import Score, StageReport

    stage = StageReport("render", scores=[
        Score("cited", 9, 10, must_hold=1.0),
        Score("something_else", 10, 10),
    ])
    report = eval_kit.EvalReport("x", "1", "abc", stages=[stage])

    assert not stage.holds
    assert not report.holds
    assert "MUST HOLD" in str(stage.scores[0])


# ------------------------------------------------------------------- scoring rules


def test_scoring_is_fuzzy_never_exact():
    """The gold contains `IRM de la jmabe D`, `Ilisible`, and `polyneuropathie` where the
    EMG says radiculopathie. An exact scorer measures how well the engine reproduces a
    human's typos (§11.4)."""
    assert scoring.matches("IRM de la jambe D", "IRM de la jmabe D")
    assert scoring.matches("Illisible", "Ilisible")
    # Accents and case are lost by OCR constantly and carry no meaning for scoring.
    assert scoring.matches("ENTORSE LOMBAIRE", "Entorse lombaire")
    # But two different findings stay different.
    assert not scoring.matches("hernie discale L4-L5", "entorse cervicale")


def test_a_date_within_three_days_is_the_same_encounter():
    """A report signed the day after the visit is the same encounter, and the gold's own
    dates come from a human reading a fax banner."""
    assert scoring.date_accuracy(date(2022, 4, 2), "2022-04-02") == "exact"
    assert scoring.date_accuracy(date(2022, 4, 4), "2022-04-02") == "near"
    assert scoring.date_accuracy(date(2022, 5, 20), "2022-04-02") == "wrong"


def test_undated_on_both_sides_is_not_a_miss():
    """The engine agreeing that a document has no date is a correct answer (§8.5)."""
    assert scoring.date_accuracy(None, None) == "both_undated"
    assert scoring.date_accuracy(None, "2022-04-02") == "missing"
    assert scoring.date_accuracy(date(2022, 4, 2), None) == "spurious"


def test_boundaries_score_as_set_overlap():
    """A report unit is a *set* of pages, and non-contiguous is normal (§2, §8.3)."""
    assert scoring.page_overlap((2, 5), (2, 5)) == 1.0
    assert scoring.page_overlap((5,), (2, 5)) == 0.5
    assert scoring.page_overlap((7,), (2, 5)) == 0.0


def test_a_gold_names_the_rows_it_never_resolved(tmp_path):
    """~30 of 74 rows were never reverse-engineered and some [PROP] divergences are
    deliberate. Both go to human adjudication, not automatic failure (§11.4)."""
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "expected.json").write_text(
        '{"gold_version": "3", "bundles": {}, "units": [],'
        ' "proposed": [4, 9], "unresolved": [12]}',
        encoding="utf-8",
    )
    gold = gold_kit.load("g", root=tmp_path)

    assert gold.version == "3"
    assert gold.proposed == (4, 9)
    assert gold.unresolved == (12,)


def test_mlflow_absence_is_not_an_error(store):
    """The harness's numbers are the product; logging them elsewhere is convenience."""
    from alie.eval import mlflow_sink

    with db.session(store.db_path) as conn:
        report = eval_kit.run(conn, eval_kit.load("tiny"))

    logged = mlflow_sink.log(report, run_group="test")
    assert logged is mlflow_sink.available()

    # Artifacts are written either way: the chronology, the failure list, and the resolved
    # prompt text verbatim — in six months `v12` may not be reconstructible (§11.1).
    out = store.var_dir / "eval" / "tiny"
    assert (out / "chronology.json").exists()
    assert (out / "failures.txt").exists()
    assert "extract_row_lines@v1" in (out / "prompts.txt").read_text(encoding="utf-8")
