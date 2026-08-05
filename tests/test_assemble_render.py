"""Stage contracts: 4a, Assemble and Render (PRD §14.2).

4a fails by wrong template revision; assemble by wrong merge/split; render by uncited
text, and must hold uncited = 0 and coverage = 100%.
"""

from __future__ import annotations

import json

import pytest

from alie.models import RowStatus
from alie.packs import UnknownRevision, lookup
from alie.packs import load as load_pack
from alie.stages import assemble, render, structured
from alie.stores import db, manifest, records
from helpers import build_case

# ------------------------------------------------------------------------------- 4a


def test_structured_read_produces_cited_checkbox_states(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        unit = next(
            u for u in manifest.units_for_case(conn, case_id) if u.form_serial == "1918"
        )
        recs = {r.field: r for r in records.for_unit(conn, unit.id)}

    assert recs["consolidation"].value == "non"
    assert recs["consolidation"].is_cited
    assert recs["consolidation"].stage == "4a"


def test_absent_field_is_distinct_from_a_blank_one(store):
    """Three-state consolidation: `aucune` is not `trop tôt` is not absent (§8.6)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rem = next(
            u for u in manifest.units_for_case(conn, case_id) if u.form_serial == "2064"
        )
        recs = {r.field: r for r in records.for_unit(conn, rem.id)}

    # `rated`, not `oui`: APIPP is not a yes/no question. A rated impairment carries a
    # percentage; `aucune` is a finding of no impairment; `trop_tot` is not yet assessable.
    assert recs["apipp"].value == "rated"
    assert recs["consolidation"].value == "absent"
    # An absence has no text to cite; that is derived, not an uncited transcription.
    assert recs["consolidation"].derived
    assert not recs["consolidation"].violates_citation_invariant


def test_bareme_rows_are_stored_individually_with_an_expected_count(store):
    """Storing only a total loses the ability to check either. The count is what makes
    silent output truncation detectable (§8.6, §12)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rem = next(
            u for u in manifest.units_for_case(conn, case_id) if u.form_serial == "2064"
        )
        recs = {r.field: r for r in records.for_unit(conn, rem.id)}

    assert json.loads(recs["bareme.1"].value) == {"code": "102 383", "pct": "2"}
    assert json.loads(recs["bareme.2"].value) == {"code": "204 219", "pct": "2°2"}
    assert recs["bareme.expected_count"].value == "2"
    assert recs["bareme.expected_count"].derived
    # The mis-OCR'd percentage keeps its low confidence all the way through.
    assert recs["bareme.2"].confidence < 1.0


def test_unknown_revision_falls_back_rather_than_reading_wrong_coordinates(store, pack):
    """Silently reading wrong coordinates is worse than no template (§4.3)."""
    with pytest.raises(UnknownRevision):
        lookup(pack, "2064", "2099-01")

    assert lookup(pack, "9999", "2012-06") is None


def test_unregistered_form_records_no_structured_fields(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        imagerie = next(
            u for u in manifest.units_for_case(conn, case_id)
            if u.doc_class == "rapport_imagerie"
        )
        result = structured.run_unit(conn, imagerie.id)

    assert result.template is None
    assert result.fields_read == 0


# ------------------------------------------------------------------------- assemble


def test_cross_bundle_union_keeps_one_row_and_both_locators(store):
    """Same (date, author, class) in two bundles -> one row, content unioned, both
    locators retained (§8.5)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        result = assemble.run(conn, case_id)

    unioned = [r for r in result.rows if len(r.locators) > 1]
    assert result.cross_bundle_unions == 2
    assert len(unioned) == 2
    for row in unioned:
        folders = {c.bundle_id for c in row.locators}
        assert len(folders) == 2


def test_unioned_content_is_deduplicated_not_concatenated(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        result = assemble.run(conn, case_id)

    for row in result.rows:
        texts = [" ".join(b.text.split()).casefold() for b in row.bullets]
        assert len(texts) == len(set(texts)), f"duplicate bullets in {row.id}"


def test_identical_masthead_with_a_different_visit_date_stays_two_rows(store):
    """Amélie's example: identical masthead, different visit dates -> keep both (§10.1)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        result = assemble.run(conn, case_id)

    dates = sorted(r.row_date.value.isoformat() for r in result.rows)
    assert dates == ["2024-02-11", "2024-03-05", "2024-04-18", "2024-05-20"]


def test_diagnostic_study_keeps_its_own_row(store):
    """Merge by encounter, split by study (§8.5)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "dupes")
        result = assemble.run(conn, case_id)

    imaging = [r for r in result.rows if r.doc_class == "rapport_imagerie"]
    assert len(imaging) == 1
    assert imaging[0].row_date.value.isoformat() == "2024-03-05"


def test_undated_rows_lead_the_document(store):
    """So they are the first thing reviewed rather than the last thing discovered (§8.5)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        result = assemble.run(conn, case_id)

    leading = result.rows[: result.undated]
    assert result.undated == 2
    assert all(r.is_undated for r in leading)
    assert not any(r.is_undated for r in result.rows[result.undated :])


def test_illegible_row_exists_carries_a_reason_and_has_no_bullets(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        result = assemble.run(conn, case_id)

    illegible = [r for r in result.rows if r.row_date.status is RowStatus.ILLEGIBLE]
    assert len(illegible) == 1
    assert illegible[0].illegible_reason
    assert illegible[0].bullets == []


def test_row_ordering_is_stable_across_runs(store):
    """Or every re-run produces spurious diffs and eval diffs become unreadable (§10.4)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        first = [r.id for r in assemble.run(conn, case_id).rows]
        second = [r.id for r in assemble.run(conn, case_id).rows]

    assert first == second


def test_confidence_rides_with_the_string(store):
    """Row confidence = min(bullet confidences) x date-status factor (§8.7)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        result = assemble.run(conn, case_id)

    rem = next(r for r in result.rows if r.doc_class == "rapport_evaluation_medicale")
    assert rem.warns
    assert rem.confidence < min(b.confidence for b in rem.bullets) + 1e-9


# --------------------------------------------------------------------------- render


def test_render_holds_uncited_zero_and_coverage_one(store):
    for name in ("tiny", "hard", "dupes"):
        with db.session(store.db_path) as conn:
            case_id = build_case(conn, name)
            rows = assemble.run(conn, case_id).rows
            validation = render.validate(conn, case_id, rows)

        assert validation.uncited_bullets == [], name
        assert validation.coverage == 1.0, name
        assert validation.passes, name


def test_every_bullet_carries_a_resolvable_span(store):
    from alie.stores import blocks as blocks_store

    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        rows = assemble.run(conn, case_id).rows
        for row in rows:
            for bullet in row.bullets:
                span = bullet.citation.span
                assert span is not None
                block = blocks_store.by_id(conn, span.block_id)
                assert block is not None
                assert span.slice(block.text)


def test_row_without_a_printed_label_is_flagged(store):
    """Display falls back to pdf_index and the row is flagged (§8.1)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rows = assemble.run(conn, case_id).rows
        validation = render.validate(conn, case_id, rows)

    assert len(validation.rows_without_printed_label) == 2


def test_markdown_leads_with_the_undated_heading(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rows = assemble.run(conn, case_id).rows
        markdown = render.to_markdown(conn, case_id, rows)

    assert markdown.startswith("**SANS DATE — 2 documents à dater**")
    assert "(?)" in markdown  # the ambiguous date renders its marker
    assert "Illisible" in markdown


def test_json_export_carries_both_page_numbers(store):
    """Both page numbers, on every page, always (§8.1)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        rows = assemble.run(conn, case_id).rows
        payload = render.to_json(conn, case_id, rows)

    locator = next(
        lo for row in payload for lo in row["locators"] if lo["printed_label"] == "44"
    )
    assert locator["pdf_index"] == 5
    assert locator["display_page"] == "44"


def test_pack_controls_display_only(store):
    """Packs may not change citation storage; only display (§8.1)."""
    pack = load_pack("cnesst")
    text = render.locator_text(pack, "Médical", "44", needs_flag=False)

    assert "Médical" in text and "p. 44" in text
    assert pack.output["locator"]["page_prefix"] == "p. "


def test_row_titles_and_bullets_never_show_storage_form(store):
    """The deliverable goes to opposing counsel. A raw class id, a field name or a JSON
    blob appearing in it is a defect, not cosmetics (§3.2 — code renders the row)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rows = assemble.run(conn, case_id).rows

    rendered = [r.title for r in rows] + [b.text for r in rows for b in r.bullets]
    for text in rendered:
        assert "{" not in text and "}" not in text, text
        assert "|pct=" not in text, text
        assert not text.startswith("bareme."), text
    # The unclassified unit still gets a readable name.
    assert "unknown" not in " ".join(r.title for r in rows)


def test_bareme_line_comes_from_the_pack_not_the_engine(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rows = assemble.run(conn, case_id).rows

    rem = next(r for r in rows if r.doc_class == "rapport_evaluation_medicale")
    lines = [b.text for b in rem.bullets]
    assert "Barème 102 383 — 2 %" in lines
    assert "Atteinte permanente : oui" in lines


# ------------------------------------------------------------------ line selection


def _blocks(*rows):
    from alie.models import BBox, Block, BlockSource, BlockType

    out = []
    for i, (page, kind, text) in enumerate(rows):
        out.append(
            Block(id=f"b{i}", bundle_id="bun", pdf_index=page, type=BlockType(kind), text=text,
                  bbox=BBox(0, i * 12, 400, i * 12 + 10), source=BlockSource.OCR,
                  confidence=0.9, order=i)
        )
    return out


def test_letterhead_and_identity_never_become_row_content():
    """She transcribes *selected* lines (§1.1). Every line, cited, is the document again."""
    from alie.packs import load
    from alie.stages.select_lines import select

    blocks = _blocks(
        (1, "paragraph", "Clinique Médicale Mères & Monde"),
        (1, "paragraph", "Nom du Patient HUARD, Eric Date de naissance 1970-07-16"),
        (1, "paragraph", "NAM HUAE70071617 Sexe à la naissance Homme"),
        (1, "heading", "RAISON DE LA VISITE"),
        (1, "paragraph", "Suivi CNESST Lombosciatalgie G sur HD L5-S1."),
    )
    kept = [b.text for b in select(blocks, load("cnesst")).kept]

    assert kept == ["Suivi CNESST Lombosciatalgie G sur HD L5-S1."]


def test_a_recurring_footer_does_not_close_the_section_above_it():
    """`CONFIDENTIEL` is printed at the foot of every page. Read as a section boundary it
    deleted the tail of every note."""
    from alie.packs import load
    from alie.stages.select_lines import select

    blocks = _blocks(
        (1, "heading", "EXAMEN"),
        (1, "paragraph", "SLR G 25 et D 40 avec fesse G"),
        (1, "heading", "CONFIDENTIEL"),
        (2, "heading", "CONFIDENTIEL"),
        (2, "paragraph", "ROM épaule G: rot externe limitée 40 degrés"),
    )
    kept = [b.text for b in select(blocks, load("cnesst")).kept]

    assert "SLR G 25 et D 40 avec fesse G" in kept
    assert "ROM épaule G: rot externe limitée 40 degrés" in kept


def test_a_named_section_heading_is_never_treated_as_furniture():
    """A unit holding several consecutive notes repeats `RAISON DE LA VISITE` on every
    page. Discarding it as a running header meant no section ever opened and the whole
    five-page unit came out with nothing in it."""
    from alie.packs import load
    from alie.stages.select_lines import select

    blocks = _blocks(
        *[(p, "heading", "RAISON DE LA VISITE") for p in (1, 2, 3, 4)],
        (1, "paragraph", "Première consultation"),
        (4, "paragraph", "Quatrième consultation"),
    )
    kept = [b.text for b in select(blocks, load("cnesst")).kept]

    assert "Première consultation" in kept
    assert "Quatrième consultation" in kept


def test_selection_never_empties_a_readable_document():
    """A row rendering as a title with nothing under it is indistinguishable from a
    document that genuinely said nothing (§3.4)."""
    from alie.packs import load
    from alie.stages.select_lines import select

    blocks = _blocks(
        (1, "paragraph", "Contenu clinique sans aucune section reconnue par le pack"),
        (1, "paragraph", "Deuxième ligne également hors section"),
    )
    result = select(blocks, load("cnesst"))

    assert len(result.kept) == 2


def test_real_case_rows_are_never_empty_when_legible(store):
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        rows = assemble.run(conn, case_id).rows

    for row in rows:
        if row.illegible_reason is None:
            assert row.bullets, f"{row.title} rendered with no content"


def test_the_doctype_code_is_a_display_preference_not_a_stored_value(store):
    """`render.doctype_code` is a *behaviour* flag: safe mid-case, instantly reversible,
    no recompute (§9). So it is applied at render time and never baked into a stored row —
    the same rows must be renderable both ways."""
    from alie.stages import assemble, render

    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        rows_out = assemble.run(conn, case_id).rows
        plain = render.to_markdown(conn, case_id, rows_out)
        coded = render.to_markdown(
            conn, case_id, rows_out, flags={"render.doctype_code": True}
        )

    assert "[IMG]" not in plain
    assert "[IMG] Rapport d'imagerie" in coded
    # Same rows, two renderings. Nothing was recomputed.
    assert plain.count("|") == coded.count("|")


def test_every_class_the_pack_declares_has_a_short_code(pack):
    """A code shown for some classes and blank for others reads as a data gap rather than
    a display choice."""
    missing = [c["id"] for c in pack.class_list if not c.get("short")]

    assert not missing, f"classes with no short code: {missing}"
