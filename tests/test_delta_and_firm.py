"""Delta runs (PRD §10.3) and the firm layer (§6.3).

Real firms receive records in batches. The CHUM file arrives three weeks after `Médical`,
and the paralegal has already approved 74 rows. She should be shown the six that changed,
not asked to re-read all of them.
"""

from __future__ import annotations

import pytest

from alie import flags
from alie.devkit import fixtures
from alie.packs import load as load_pack
from alie.stages import assemble, delta, ingest, manifest_build, parse, structured
from alie.stages.delta import Change
from alie.stores import cases, db, manifest


def _add_bundle(conn, case_id: str, folder: str) -> None:
    """Ingest one more batch into an existing case — the §10.3 scenario. The CHUM file
    arrives three weeks after `Médical`, into the case the paralegal already approved."""
    resolved = flags.resolve()
    filename = fixtures.EXPECTED["dupes"]["bundles"][folder]
    bundle_id = ingest.add_pdf_path(
        conn, case_id=case_id,
        path=fixtures.fixture_path("dupes", filename), folder_label=folder,
    )
    parse.run(conn, bundle_id, flags=resolved)
    manifest_build.run(conn, bundle_id, flags=resolved)
    for unit in manifest.units_for_bundle(conn, bundle_id):
        structured.run_unit(conn, unit.id)


def _batched(conn) -> tuple[list, list]:
    """The approved chronology, then the same case after a second batch arrives."""
    case_id = cases.create_case(conn, "batched", "cnesst")
    _add_bundle(conn, case_id, "Médical")
    before = assemble.run(conn, case_id).rows
    _add_bundle(conn, case_id, "CHUM")
    after = assemble.run(conn, case_id).rows
    return before, after


def test_a_second_batch_reports_what_changed_not_a_fresh_chronology(store):
    """`6 new rows, 2 existing rows gained a second locator` — not 74 rows to re-read."""
    with db.session(store.db_path) as conn:
        before, after = _batched(conn)

    result = delta.Delta("run_a", "run_b", delta.compare(before, after))

    assert result.of(Change.NEW), "the second bundle brought rows that did not exist"
    # The point of the feature: most of the approved chronology is untouched, so the
    # review screen shows what moved rather than every row again (§10.3).
    assert result.of(Change.UNCHANGED)
    assert result.review_count < len(after)
    assert "inchangées" in result.summary()


def test_a_row_that_gained_a_locator_keeps_its_identity(store):
    """Row ids derive from the unit set, so a row cited by a second bundle arrives as the
    same row — not as a deletion plus an insertion, which is what would make the delta
    unreadable on exactly the case §10.3 describes."""
    with db.session(store.db_path) as conn:
        before, after = _batched(conn)

    gained = [c for c in delta.compare(before, after) if c.change is Change.GAINED_LOCATOR]

    assert gained, "the re-fax should attach a second locator to an existing row"
    assert all(c.added_locators for c in gained)


def test_an_unchanged_case_produces_an_empty_delta(store):
    """Unit ids are derived from content, so a re-run over unchanged input reproduces them
    and every approved row stays attached (§10.4). Spurious diffs would make the whole
    feature untrustworthy."""
    with db.session(store.db_path) as conn:
        case_id = cases.create_case(conn, "steady", "cnesst")
        _add_bundle(conn, case_id, "Médical")
        before = assemble.run(conn, case_id).rows
        after = assemble.run(conn, case_id).rows

    result = delta.Delta("a", "b", delta.compare(before, after))

    assert result.review_count == 0
    assert result.summary() == "aucun changement depuis la version approuvée"


def test_a_disappearing_row_is_never_silent(store):
    """A row present before and absent now is the single most alarming thing a delta can
    contain (§3.4)."""
    with db.session(store.db_path) as conn:
        case_id = cases.create_case(conn, "lossy", "cnesst")
        _add_bundle(conn, case_id, "Médical")
        rows = assemble.run(conn, case_id).rows

    result = delta.Delta("a", "b", delta.compare(rows, rows[:-1]))

    assert len(result.of(Change.DROPPED)) == 1
    assert "disparue" in result.summary()


def test_a_redated_row_is_distinguished_from_a_reworded_one(store):
    """A changed date and changed text need different attention: one is a chronology
    error, the other a transcription difference."""
    import copy
    import dataclasses
    from datetime import date

    with db.session(store.db_path) as conn:
        case_id = cases.create_case(conn, "shifted", "cnesst")
        _add_bundle(conn, case_id, "Médical")
        rows = assemble.run(conn, case_id).rows

    redated = copy.deepcopy(rows)
    redated[0].row_date = dataclasses.replace(redated[0].row_date, value=date(1999, 1, 1))
    changed = {c.row_id: c.change for c in delta.compare(rows, redated)}
    assert changed[rows[0].id] is Change.DATE_CHANGED

    reworded = copy.deepcopy(rows)
    reworded[0].bullets[0] = dataclasses.replace(
        reworded[0].bullets[0], text="texte différent"
    )
    changed = {c.row_id: c.change for c in delta.compare(rows, reworded)}
    assert changed[rows[0].id] is Change.CONTENT_CHANGED


# ------------------------------------------------------------------- firm layer


def test_a_firm_restates_wording_and_inherits_the_rest():
    """Style is per-firm and arguably per-paralegal. Without this layer, onboarding firm #2
    means forking a pack, and the fork drifts from the regime rules it inherited (§6.3)."""
    base = load_pack("cnesst")
    firm = load_pack("cnesst", firm="demo")

    assert firm.field_line("consolidation") != base.field_line("consolidation")
    assert firm.field_line("diagnostic") == base.field_line("diagnostic")
    assert firm.firm == "demo"


def test_a_firm_layer_merges_mappings_key_by_key():
    """A firm overriding one heading restates that heading and keeps every sibling."""
    base = load_pack("cnesst")
    firm = load_pack("cnesst", firm="demo")

    assert firm.output["rows"]["undated_heading"] != base.output["rows"]["undated_heading"]
    assert firm.output["rows"]["removed_heading"] == base.output["rows"]["removed_heading"]


def test_a_firm_cannot_invent_regime_rules():
    """Classes, date roles and filters are regime facts, not house style. A firm editing
    them would put regime knowledge in two places and make the pack a suggestion (§6.3)."""
    from alie.packs.loader import _FIRM_LAYERABLE

    assert "classes" not in _FIRM_LAYERABLE
    assert "dates" not in _FIRM_LAYERABLE
    assert "filters" not in _FIRM_LAYERABLE


def test_an_unknown_firm_fails_loudly():
    """Silently falling back to the pack would ship one firm's chronology in another
    firm's house style."""
    with pytest.raises(KeyError):
        load_pack("cnesst", firm="no-such-firm")


def test_the_pack_is_unchanged_by_a_firm_layer():
    """The layer is an overlay, not an edit. A second firm must not see the first's
    wording."""
    firm = load_pack("cnesst", firm="demo")
    base = load_pack("cnesst")

    assert base.field_line("consolidation") == "Consolidation : {value}"
    assert firm.field_line("consolidation").startswith("Lésion consolidée")
