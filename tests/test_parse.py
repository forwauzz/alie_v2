"""Stage contract: Parse. In PDF, out blocks. Fails by lost page anchors.
Proven when every block has page + bbox (PRD §14.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from alie.devkit import fixtures
from alie.models import BlockType
from alie.stages import ingest, parse
from alie.stores import blocks as blocks_store
from alie.stores import cases, db


def _parse(settings, name: str, filename: str = "Medical.pdf"):
    with db.session(settings.db_path) as conn:
        case_id = cases.create_case(conn, name, "cnesst")
        bundle_id = ingest.add_pdf_path(
            conn, case_id=case_id, path=fixtures.fixture_path(name, filename),
            folder_label="Médical",
        )
        result = parse.run(conn, bundle_id)
        return bundle_id, result


def test_every_block_carries_a_page_anchor_and_bbox(store):
    bundle_id, result = _parse(store, "tiny")
    with db.read_only(store.db_path) as conn:
        blocks = blocks_store.for_bundle(conn, bundle_id)

    assert blocks, "parse produced no blocks"
    assert result.blocks == len(blocks)
    for block in blocks:
        assert block.pdf_index >= 1
        assert block.bbox.width > 0 and block.bbox.height > 0
        assert block.source is not None


def test_printed_label_is_stored_separately_from_pdf_index(store):
    """`printed_label` is what renders, `pdf_index` is what we navigate by (§8.1)."""
    bundle_id, _ = _parse(store, "tiny")
    with db.read_only(store.db_path) as conn:
        labels = cases.printed_labels(conn, bundle_id)

    # pdf page 5 prints "44" — rendering the pdf index would produce the wrong citation.
    assert labels[5] == "44"
    assert labels[1] == "1"


def test_pages_with_no_text_layer_are_queued_not_silently_empty(store):
    """`parse.ocr`'s metric is % pages queued as unparseable (§9.2)."""
    _, result = _parse(store, "hard")

    assert result.unparseable_pages == (8,)
    assert 0 < result.unparseable_ratio < 1


def test_malformed_number_is_flagged_rather_than_shipped(store):
    """A wrong barème percentage is a legal error, not a typo (§4.3)."""
    bundle_id, _ = _parse(store, "hard")
    with db.read_only(store.db_path) as conn:
        blocks = blocks_store.for_bundle(conn, bundle_id)

    flagged = [b for b in blocks if b.attrs.get("degenerate_number")]
    assert len(flagged) == 1
    assert "2°2" in flagged[0].text
    assert flagged[0].confidence < 1.0


def test_fax_banner_is_isolated_as_a_stamp(store):
    """Stamps are isolated for the dedupe transmission axis (§4.3, §10.1)."""
    bundle_id, _ = _parse(store, "dupes", "CHUM.pdf")
    with db.read_only(store.db_path) as conn:
        blocks = blocks_store.for_bundle(conn, bundle_id)

    stamps = [b for b in blocks if b.type is BlockType.STAMP]
    assert len(stamps) == 1
    assert "CLINIQUE ST-LAURENT" in stamps[0].text


def test_reparse_is_idempotent(store):
    """Stages are pure and idempotent; blocks are replaced wholesale (§3.8, §4.5)."""
    bundle_id, first = _parse(store, "tiny")
    with db.session(store.db_path) as conn:
        second = parse.run(conn, bundle_id)
        ids = [b.id for b in blocks_store.for_bundle(conn, bundle_id)]

    assert first.blocks == second.blocks
    assert len(ids) == len(set(ids)), "block ids must be stable and unique across re-parse"


def test_unknown_bundle_raises(store):
    with db.session(store.db_path) as conn, pytest.raises(KeyError):
        parse.run(conn, "bun_does_not_exist")


def test_fixture_pdfs_exist_in_repo():
    """`tiny`, `hard` and `dupes` are in the repo; `gold-cnesst` is not (§13.3)."""
    root = Path(__file__).resolve().parents[1] / "fixtures"
    for name, spec in fixtures.EXPECTED.items():
        for filename in spec["bundles"].values():
            assert (root / name / filename).exists()
    assert not (root / "gold-cnesst").exists()


# --------------------------------------------------------- printed-label confirmation


def test_year_in_a_footer_is_not_a_page_label():
    """Medico-legal footers carry birth years. The reference bundle prints `1937` on four
    pages; citing `p. 1937` would be visibly wrong to the firm (§8.1)."""
    from alie.parse import pagelabel

    confirmed = pagelabel.confirm_bare_labels(
        {1: ("bare_number", "1937"), 2: ("bare_number", "44")}
    )
    assert 1 not in confirmed
    assert confirmed[2] == "44"


def test_isolated_bare_number_is_kept():
    """`Clinique mère et monde` prints `44` on a sheet whose neighbours print nothing, and
    the answer key cites `p. 44`. Dropping it to be safe loses the case the field exists
    for (§8.1)."""
    from alie.parse import pagelabel

    assert pagelabel.confirm_bare_labels({7: ("bare_number", "44")}) == {7: "44"}


def test_large_recurring_number_is_a_reference_not_a_page():
    from alie.parse import pagelabel

    labels = {i: ("bare_number", "780") for i in range(1, 5)}
    assert pagelabel.confirm_bare_labels(labels) == {}


def test_small_numbers_may_repeat_across_documents():
    """Every two-page fax has a page `2`."""
    from alie.parse import pagelabel

    labels = {1: ("bare_number", "2"), 5: ("bare_number", "2"), 9: ("bare_number", "2")}
    assert len(pagelabel.confirm_bare_labels(labels)) == 3


def test_explicit_labels_are_never_second_guessed():
    from alie.parse import pagelabel

    labels = {1: ("page_x_of_y", "1937"), 2: ("page_n", "780")}
    assert pagelabel.confirm_bare_labels(labels) == {1: "1937", 2: "780"}
