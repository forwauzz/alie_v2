"""Duplicates are a view over the manifest (PRD §10.1).

Amélie: seven things you have to look at to make sure it is a duplicate. Her example —
identical masthead, different visit dates → keep both. Page hashing cannot express that.

Only `identical` is auto-removable, and removal is a status, not a deletion.
"""

from __future__ import annotations

from datetime import date

from alie.manifest import dedupe
from alie.manifest.dedupe import Fingerprint, Verdict
from alie.stores import db, manifest
from helpers import build_case


def _print(**kw) -> Fingerprint:
    base = dict(
        unit_id="u", doc_class="note_consultation", event_date=date(2024, 2, 11),
        author="dr alain roy", form_serial=None,
        clinical_content="lombalgie mecanique amelioration partielle",
        annotations=(), transmission=(),
    )
    return Fingerprint(**(base | kw))


def test_all_seven_axes_agreeing_is_the_only_removable_case():
    got = dedupe.compare(_print(unit_id="a"), _print(unit_id="b"))

    assert got.verdict is Verdict.IDENTICAL
    assert got.verdict.removable
    assert got.differing == ()


def test_a_refax_scores_full_content_and_differs_only_on_transmission(store):
    """The content fingerprint strips transmission furniture before comparing, then
    reports it on its own axis — so a re-fax scores 1.00 on content *and* shows exactly
    what changed (§10.1)."""
    got = dedupe.compare(
        _print(unit_id="a"),
        _print(unit_id="b", transmission=("page 4 15 chum",)),
    )

    assert got.content_similarity == 1.0
    assert got.differing == ("transmission",)
    assert got.verdict is Verdict.SAME_DOC_DIFFERENT_ARTIFACT
    # Not removable. Firm policy, and a rescan may be the only legible copy.
    assert not got.verdict.removable


def test_an_annotated_copy_is_never_auto_removed():
    """The annotation may be the most important thing on the page."""
    got = dedupe.compare(
        _print(unit_id="a"),
        _print(unit_id="b", annotations=("voir avec me tremblay",)),
    )

    assert not got.verdict.removable
    assert "annotations" in got.differing


def test_identical_masthead_different_visit_dates_keeps_both():
    """Amélie's own example (§10.1)."""
    got = dedupe.compare(
        _print(unit_id="a", event_date=date(2024, 2, 11)),
        _print(unit_id="b", event_date=date(2024, 4, 18)),
    )

    assert not got.verdict.removable
    assert "event_date" in got.differing


def test_two_undated_units_have_not_agreed_on_a_date():
    """They have both failed to state one. Unknown counts against sameness, because only
    `identical` is removable and the conservative direction is the safe one (§9)."""
    got = dedupe.compare(
        _print(unit_id="a", event_date=None),
        _print(unit_id="b", event_date=None),
    )

    assert got.verdict is not Verdict.IDENTICAL
    assert "event_date" in got.differing


def test_same_class_and_date_but_different_content_is_related():
    """The same encounter documented twice, not a duplicate."""
    got = dedupe.compare(
        _print(unit_id="a"),
        _print(unit_id="b", clinical_content="reprise des activites progressives"),
    )

    assert got.verdict is Verdict.RELATED
    assert not got.verdict.removable


def test_the_fixture_pairs_score_as_the_gold_says(store):
    """The `dupes` fixture states three verdicts: a re-fax, a byte-identical pair, and a
    later-visit pair that must stay two rows."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        units = {u.id: u for u in manifest.units_for_case(conn, case_id)}
        found = dedupe.candidates(conn, case_id)

    verdicts = {
        tuple(sorted((units[c.a].pages[0], units[c.b].pages[0]))): c.verdict for c in found
    }
    assert verdicts[(1, 1)] is Verdict.SAME_DOC_DIFFERENT_ARTIFACT
    assert verdicts[(2, 2)] is Verdict.IDENTICAL
    # The later-visit pair scores `different` and never reaches the candidate list.
    assert (3, 3) not in verdicts


def test_only_one_of_an_identical_pair_is_ever_removed(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        found = dedupe.candidates(conn, case_id)

    removals = dedupe.removable(found)
    assert len(removals) == 1
    # The kept unit is named, so the export's manifest can say what was held back and
    # against what.
    kept = next(iter(removals.values()))
    assert kept not in removals


def test_removal_is_a_status_not_a_deletion(store):
    """Removing pages from a legal record is never a destructive operation on the source
    (§10.1). The unit, its pages and every citation into it stay where they were."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        found = dedupe.candidates(conn, case_id)
        victim = next(iter(dedupe.removable(found)))
        before = manifest.get_unit(conn, victim)
        manifest.set_excluded(conn, victim, "dedupe.identical_to:x")
        after = manifest.get_unit(conn, victim)

    assert after is not None
    assert after.pages == before.pages
    assert after.excluded_by == "dedupe.identical_to:x"


def test_a_removed_row_reaches_the_export_as_a_manifest_entry(store):
    """A deduplicated export must be reversible and carry a manifest of what was removed
    and why (§10.1)."""
    from alie.stages import assemble, render

    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "admin")
        rows = assemble.run(conn, case_id).rows
        markdown = render.to_markdown(conn, case_id, rows)

    held = [r for r in rows if r.excluded_by]
    assert held, "excluded units must still become rows (§3.4)"
    assert "RETIRÉ PAR RÈGLE" in markdown
    assert "cnesst.filter.billing" in markdown
    # And they stay out of the chronology table itself.
    body = markdown.split("RETIRÉ PAR RÈGLE")[0]
    assert "cnesst.filter.billing" not in body
