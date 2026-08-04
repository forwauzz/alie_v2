"""Filters (PRD §6, §3.4).

Nothing is dropped. A filtered unit still reaches the manifest with `excluded_by` naming
the rule that fired, and the paralegal can see what was removed and why.

The control matters more than the exclusions: a filter that removes a consultation note is
worse than no filter at all.
"""

from __future__ import annotations

from alie.manifest import filters
from alie.stores import db, manifest
from helpers import build_case


def _units(conn):
    case_id = build_case(conn, "admin")
    return {u.pages[0]: u for u in manifest.units_for_case(conn, case_id)}


def test_billing_and_consent_are_excluded_by_rule(store):
    with db.session(store.db_path) as conn:
        units = _units(conn)

    assert units[1].excluded_by == "cnesst.filter.billing"
    assert units[2].excluded_by == "cnesst.filter.consent"
    # The rule that fired is named, and so is the text that tripped it — the why-panel
    # shows the paralegal what the engine saw (§7.1).
    assert units[1].attrs["excluded_reason"] == "billing"
    assert units[1].attrs["excluded_evidence"]


def test_a_clinical_note_is_never_excluded(store):
    """The control. Same bundle, same patient, genuinely clinical."""
    with db.session(store.db_path) as conn:
        units = _units(conn)

    note = units[3]
    assert note.excluded_by is None
    assert note.doc_class == "note_consultation"
    assert note.row_date.value is not None


def test_an_excluded_unit_still_reaches_the_manifest(store):
    """Exclusion is a status, not a deletion (§3.4). Removing pages from a legal record is
    never destructive on the source."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "admin")
        units = manifest.units_for_case(conn, case_id)

    assert len(units) == 4
    assert sum(1 for u in units if u.excluded_by) == 3
    # Every excluded unit still has its pages, class and citations intact.
    assert all(u.pages for u in units)


def test_zero_content_fires_on_admin_but_not_on_clinical(store, pack):
    """Zero-content *clinical* documents are kept as title-only rows for evidentiary
    completeness; this filter applies to admin classes only (§8.5)."""
    with db.session(store.db_path) as conn:
        units = _units(conn)

    assert units[4].excluded_by == "cnesst.filter.zero_content_admin"

    empty_clinical = filters.UnitFacts(
        doc_class="note_consultation", is_admin_class=False,
        has_clinical_content=False, text="# NOTE DE CONSULTATION",
    )
    assert not filters.PREDICATES["unit_has_no_clinical_content_and_class_is_admin"](
        empty_clinical
    )


def test_a_filter_never_removes_a_unit_carrying_clinical_content(store, pack):
    """Measured on the real bundle: a 4-page imaging report was excluded because a consent
    form was bundled into it and the words appeared in its text. Filters exist for admin
    noise; deleting a radiologist's findings is not a tradeoff worth making (§3.4, §8.5)."""
    from alie.models import BBox, Block, BlockSource, BlockType

    def block(order, text, kind=BlockType.PARAGRAPH):
        return Block(
            id=f"blk_{order}", bundle_id="b", pdf_index=1, order=order, text=text,
            type=kind, bbox=BBox(0, 0, 400, 12), source=BlockSource.OCR, confidence=0.9,
        )

    imaging = [
        block(0, "RAPPORT D'IMAGERIE", BlockType.HEADING),
        block(1, "Examen: IRM colonne lombaire réalisée le 2023-04-11 avec séquences."),
        block(2, "CONCLUSION: hernie discale L4-L5 avec contact radiculaire droit."),
        # The bundled consent page, deeper in the same unit.
        block(3, "FORMULAIRE DE CONSENTEMENT"),
        block(4, "Autorisation de divulgation de renseignements médicaux au dossier."),
    ]
    verdict, _ = filters.evaluate(
        imaging, pack, doc_class="rapport_imagerie", admin_classes=pack.admin_classes
    )

    assert not verdict.excluded


def test_a_filter_matches_what_a_document_is_not_what_it_mentions(store, pack):
    """A document names itself at the top. The same words on page 3 are a reference."""
    from alie.models import BBox, Block, BlockSource, BlockType

    def block(order, text, kind=BlockType.PARAGRAPH):
        return Block(
            id=f"blk_{order}", bundle_id="b", pdf_index=1, order=order, text=text,
            type=kind, bbox=BBox(0, 0, 400, 12), source=BlockSource.OCR, confidence=0.9,
        )

    # Admin class, no clinical content, but the match is buried past the declaration zone.
    mentions_late = [block(i, "Ligne de remplissage sans intérêt.") for i in range(8)]
    mentions_late.append(block(8, "voir formulaire de consentement au dossier"))
    verdict, _ = filters.evaluate(
        mentions_late, pack, doc_class="administratif", admin_classes=pack.admin_classes
    )
    assert not verdict.excluded

    # The same words as a heading: the document *is* the consent form.
    declares = [block(0, "FORMULAIRE DE CONSENTEMENT", BlockType.HEADING)]
    verdict, _ = filters.evaluate(
        declares, pack, doc_class="administratif", admin_classes=pack.admin_classes
    )
    assert verdict.rule_id == "cnesst.filter.consent"


def test_a_filter_whose_feature_is_unbuilt_is_reported_not_silently_false(store, pack):
    """A filter that quietly never fires looks exactly like a filter that found nothing.
    The plan would claim "0 excluded by rule" with equal confidence either way."""
    from alie.models import BBox, Block, BlockSource, BlockType

    block = Block(
        id="blk_x", bundle_id="b", pdf_index=1, order=0,
        text="Note de consultation sans particularité.",
        type=BlockType.PARAGRAPH, bbox=BBox(0, 0, 100, 12),
        source=BlockSource.TEXT_LAYER, confidence=1.0,
    )
    _, unavailable = filters.evaluate(
        [block], pack, doc_class="note_consultation", admin_classes=pack.admin_classes
    )

    assert set(unavailable) == {
        "cnesst.filter.duplicate_of_included",
        "cnesst.filter.out_of_claim_scope",
    }
    # And every unavailable condition names why, so the gap is reviewable rather than
    # discovered when a rule never fires.
    assert set(filters.PENDING) >= {
        "unit_has_identical_duplicate_already_included",
        "unit_date_precedes_earliest_claim_event_and_no_rra_reference",
    }
    assert all(filters.PENDING.values())


def test_the_manifest_reports_what_it_could_not_evaluate(store):
    """`excluded=0` must be distinguishable from `no rule ran`."""
    from alie import flags
    from alie.devkit import fixtures
    from alie.stages import ingest, manifest_build, parse
    from alie.stores import cases

    with db.session(store.db_path) as conn:
        case_id = cases.create_case(conn, "admin", "cnesst")
        bundle_id = ingest.add_pdf_path(
            conn, case_id=case_id, path=fixtures.fixture_path("admin", "Medical.pdf"),
            folder_label="Médical",
        )
        parse.run(conn, bundle_id)
        result = manifest_build.run(conn, bundle_id, flags=flags.resolve())

    assert result.excluded == 3
    assert "cnesst.filter.duplicate_of_included" in result.filters_unavailable


def test_pack_owns_which_classes_are_administrative(pack):
    """The engine holds no taxonomy of its own (§3)."""
    assert pack.admin_classes == {"administratif"}
