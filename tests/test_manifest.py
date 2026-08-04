"""Stage contract: Manifest. In blocks, out report units. Fails by wrong boundaries or
wrong date. Proven when units and dates match the page map (PRD §14.1, §14.2).

This is the §14.1 proof: if the manifest reproduces those spans and dates, the hardest
layer is proven and no model has run.
"""

from __future__ import annotations

from datetime import date

from alie import flags
from alie.devkit import fixtures
from alie.models import Legibility, RowStatus
from alie.stages import ingest, manifest_build, parse
from alie.stores import cases, db, manifest
from helpers import build_case


def _manifest(settings, name: str, *, rejoin: bool):
    resolved = flags.resolve(run_flags={"manifest.orphan_rejoin": rejoin})
    with db.session(settings.db_path) as conn:
        case_id = cases.create_case(conn, name, "cnesst")
        bundle_id = ingest.add_pdf_path(
            conn, case_id=case_id, path=fixtures.fixture_path(name, "Medical.pdf"),
            folder_label="Médical",
        )
        parse.run(conn, bundle_id)
        result = manifest_build.run(conn, bundle_id, flags=resolved)
        units = sorted(manifest.units_for_bundle(conn, bundle_id), key=lambda u: u.pages)
        return result, units


def test_tiny_reproduces_its_expected_page_map(store):
    _, units = _manifest(store, "tiny", rejoin=True)
    expected = fixtures.EXPECTED["tiny"]["units"]

    assert [list(u.pages) for u in units] == [e["pages"] for e in expected]
    assert [u.doc_class for u in units] == [e["class"] for e in expected]
    assert [u.row_date.value.isoformat() for u in units] == [e["row_date"] for e in expected]


def test_hard_reproduces_its_expected_page_map(store):
    _, units = _manifest(store, "hard", rejoin=True)
    expected = fixtures.EXPECTED["hard"]["units"]

    assert [list(u.pages) for u in units] == [e["pages"] for e in expected]
    assert [u.doc_class for u in units] == [e["class"] for e in expected]


def test_a_unit_is_a_page_set_not_a_range(store):
    """The consult note wraps around the IRM: pages 2 and 5, not 2 through 5 (§8.3)."""
    result, units = _manifest(store, "hard", rejoin=True)
    note = next(u for u in units if u.doc_class == "note_consultation")

    assert note.pages == (2, 5)
    assert not note.is_contiguous
    assert result.non_contiguous == 1
    assert result.rejoined == 1


def test_orphan_rejoin_off_leaves_the_continuation_stranded(store):
    """The flag's metric is units changed by the pass, and the boundary precision delta
    (§9.2). With it off the orphan becomes its own undated row."""
    off, units_off = _manifest(store, "hard", rejoin=False)
    on, units_on = _manifest(store, "hard", rejoin=True)

    assert off.units == on.units + 1
    assert off.rejoined == 0
    assert off.undated == on.undated + 1
    assert (2, 5) not in [u.pages for u in units_off]
    assert (2, 5) in [u.pages for u in units_on]


def test_rem_takes_the_exam_date_over_the_evenement_date(store):
    _, units = _manifest(store, "hard", rejoin=True)
    rem = next(u for u in units if u.doc_class == "rapport_evaluation_medicale")

    assert rem.row_date.value == date(1992, 12, 10)
    assert rem.form_serial == "2064"
    assert rem.form_revision == "2012-06"
    # The événement date is kept, just never eligible to be the row date.
    assert date(1990, 5, 8) in [f.readings[0] for f in rem.dates]


def test_nothing_is_dropped_every_page_reaches_a_unit(store):
    """Undated, illegible, excluded and zero-content units all reach the manifest with a
    status (§3.4)."""
    _, units = _manifest(store, "hard", rejoin=True)
    covered = sorted(p for u in units for p in u.pages)

    assert covered == list(range(1, 9))


def test_illegible_unit_is_gated_from_the_model(store):
    """Safety invariant, not a flag (§9)."""
    _, units = _manifest(store, "hard", rejoin=True)
    illegible = [u for u in units if u.legibility is Legibility.ILLEGIBLE]

    assert len(illegible) == 1
    assert illegible[0].pages == (8,)
    assert illegible[0].gated_from_model
    assert illegible[0].row_date.status is RowStatus.ILLEGIBLE


def test_ambiguous_and_undated_units_carry_a_status(store):
    result, units = _manifest(store, "hard", rejoin=True)

    assert result.ambiguous == 1
    assert result.undated == 1
    statuses = {u.doc_class: u.row_date.status for u in units}
    assert statuses["certificat_medical"] is RowStatus.AMBIGUOUS
    assert statuses["resultat_laboratoire"] is RowStatus.UNDATED


def test_unit_ids_are_stable_across_reruns(store):
    """Approved rows are sticky and re-runs must not produce spurious diffs (§10.3)."""
    _, first = _manifest(store, "tiny", rejoin=True)
    with db.session(store.db_path) as conn:
        bundle_id = first[0].bundle_id
        manifest_build.run(conn, bundle_id, flags=flags.resolve(
            run_flags={"manifest.orphan_rejoin": True}))
        second = sorted(manifest.units_for_bundle(conn, bundle_id), key=lambda u: u.pages)

    assert [u.id for u in first] == [u.id for u in second]


def test_correction_overrides_the_pipeline_and_survives_a_rerun(store):
    """Corrections write to the manifest, not the output (§10.2)."""
    from alie.stores import corrections

    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        target = next(
            u for u in manifest.units_for_case(conn, case_id)
            if u.row_date.status is RowStatus.AMBIGUOUS
        )
        corrections.apply(
            conn, case_id=case_id, subject_type="unit", subject_id=target.id,
            field="row_date", new_value="2004-03-02", actor="amelie",
        )
        manifest_build.run(conn, target.bundle_id, flags=flags.resolve(
            run_flags={"manifest.orphan_rejoin": True}))
        after = manifest.get_unit(conn, target.id)

    assert after.row_date.value == date(2004, 3, 2)
    assert after.row_date.status is RowStatus.MANUAL
    assert "amelie" in after.row_date.explanation


def test_classification_below_threshold_is_flagged_not_guessed(store, pack):
    """Below the pack's minimum, the classifier fallback runs. Phase 1 configures no
    model, so the unit stays unknown and is flagged rather than guessed at (§5)."""
    _, units = _manifest(store, "hard", rejoin=True)
    unknown = [u for u in units if u.doc_class == pack.unknown_class]

    assert len(unknown) == 1  # the image-only page
    assert unknown[0].class_confidence < pack.min_class_confidence


def test_noise_with_a_text_layer_is_illegible_not_legible(pack):
    """A scan arrives already OCR'd by whoever made it, and that pass can fail while still
    emitting characters. Counting characters cannot tell noise from prose, and the gate is
    the only thing between noise and a model that will fluently invent French clinical
    bullets from it (§8.5)."""
    from alie.manifest.legibility import assess
    from alie.models import BBox, Block, BlockSource, BlockType

    def block(text, i=0):
        return Block(
            id=f"b{i}", bundle_id="bun", pdf_index=1, type=BlockType.PARAGRAPH, text=text,
            bbox=BBox(0, 0, 100, 10), source=BlockSource.TEXT_LAYER, confidence=1.0, order=i,
        )

    # Real text taken from the reference bundle's failed OCR pass.
    noise = [block("\rLllll\{vÊ tet-ttttr{trE ?ù o4.loÀ) l/*,/ ffik *kffi'", 0)]
    assert assess(noise, 1).level is Legibility.ILLEGIBLE
    assert assess(noise, 1).gated_from_model

    prose = [block("Le travailleur présente une entorse lombaire persistante", 0)]
    assert assess(prose, 1).level is Legibility.LEGIBLE


def test_an_unreadable_page_does_not_extend_the_document_before_it(pack):
    """Physical adjacency alone merged 32 unreadable pages of the reference bundle into
    one confident, wrong unit. You cannot know an unreadable page continues the document
    before it (§3.4)."""
    from alie.manifest.boundaries import group_pages
    from alie.models import BBox, Block, BlockSource, BlockType

    def page(idx, text, kind=BlockType.PARAGRAPH):
        return [
            Block(id=f"b{idx}", bundle_id="bun", pdf_index=idx, type=kind, text=text,
                  bbox=BBox(0, 200, 300, 212), source=BlockSource.TEXT_LAYER,
                  confidence=1.0, order=0)
        ]

    pages = {
        1: page(1, "Note de consultation du travailleur ce matin"),
        2: page(2, "\rLllll\{vÊ tet-ttttr{trE ?ù o4.loÀ)"),
        3: page(3, "Suite de la note et des observations cliniques"),
    }
    groups, signals = group_pages(pages, {i: 792.0 for i in pages}, pack)

    assert not signals[2].readable
    assert [2] in groups
    assert not any(len(g) > 1 and 2 in g for g in groups)


def test_fax_transmission_change_is_a_document_boundary(pack):
    """On scanned bundles the fax banner is often the only reliable boundary signal.
    Headings arrive OCR damaged and the clinic masthead repeats on every sheet, but a
    change of transmission is unambiguous (§10.1)."""
    from alie.manifest.boundaries import group_pages, transmission_gaps
    from alie.models import BBox, Block, BlockSource, BlockType

    def page(idx, banner, body):
        return [
            Block(id=f"s{idx}", bundle_id="bun", pdf_index=idx, type=BlockType.STAMP,
                  text=banner, bbox=BBox(0, 0, 400, 10), source=BlockSource.OCR,
                  confidence=0.9, order=0),
            Block(id=f"b{idx}", bundle_id="bun", pdf_index=idx, type=BlockType.PARAGRAPH,
                  text=body, bbox=BBox(0, 40, 400, 52), source=BlockSource.OCR,
                  confidence=0.9, order=1),
        ]

    body = "Clinique Médicale et suivi du travailleur en cours"
    pages = {
        1: page(1, "4/1/2025, 6:35 AM PDT TO: +1450 FROM: 1450 PAGE 4/15", body),
        2: page(2, "4/1/2025, 6:35 AM PDT TO: +1450 FROM: 1450 PAGE 5/15", body),
        # Same fax, but sheets 6 and 7 are not in the file.
        3: page(3, "4/1/2025, 6:35 AM PDT TO: +1450 FROM: 1450 PAGE 8/15", body),
        # A different transmission entirely.
        4: page(4, "6/12/2025, 6:41 AM PDT TO: +1450 FROM: 1450 PAGE 6/27", body),
    }
    groups, signals = group_pages(pages, {i: 792.0 for i in pages}, pack)

    assert [1, 2] in groups
    assert [3] in groups
    assert [4] in groups

    gaps = transmission_gaps(signals)
    assert len(gaps) == 1
    assert gaps[0]["missing_sheets"] == 2
    assert gaps[0]["after_pdf_page"] == 2


def test_missing_sheets_are_only_claimed_within_one_transmission():
    """A gap can only be asserted inside a single fax; two different faxes say nothing
    about each other's completeness."""
    from alie.parse.transmission import missing_pages, parse_banner

    a = parse_banner("4/1/2025, 6:35 AM TO: x PAGE 7/15")
    b = parse_banner("4/1/2025, 6:35 AM TO: x PAGE 10/15")
    other = parse_banner("6/12/2025, 6:41 AM TO: x PAGE 2/27")

    assert missing_pages(a, b) == 2
    assert missing_pages(a, other) == 0
    assert missing_pages(None, b) == 0


def test_a_banner_without_a_page_count_is_not_a_transmission():
    from alie.parse.transmission import parse_banner

    assert parse_banner("Tél.: (579) 995-0789 Fax : (450) 500-0776") is None
    assert parse_banner("PAGE 20/15") is None  # position beyond the total


def test_author_is_the_signer_not_the_signature_boilerplate(pack):
    """`Signé électroniquement le 2023-12-14 à 10:03 par: Dr X` — taking everything after
    `Signé` put a date and a timestamp in the row title the firm reads."""
    from alie.manifest.boundaries import _author
    from alie.models import BBox, Block, BlockSource, BlockType

    def signature(text):
        return [Block(id="s", bundle_id="b", pdf_index=1, type=BlockType.SIGNATURE,
                      text=text, bbox=BBox(0, 0, 10, 10), source=BlockSource.OCR,
                      confidence=0.9, order=0)]

    assert _author(signature(
        "Signé électroniquement le 2023-12-14 à 10:03 par: Dr Marie-France LABERGE"
    )) == "Dr Marie-France LABERGE"
    assert _author(signature("Signé: Dre Claire Fontaine")) == "Dre Claire Fontaine"
    assert _author(signature("Signé le 12 décembre 1992")) == "le 12 décembre 1992"


def test_a_document_declaring_itself_starts_a_unit_whatever_its_block_type(pack):
    """`Note - Soins infirmiers` is typed as a heading on one sheet and a paragraph on the
    next, depending on how OCR read it. Requiring the heading type merged a complete
    one-page note into the document before it — and a merged unit gets one date for two
    encounters, so content from one is filed under the other's date with a real citation
    attached (§2, §8.5)."""
    from alie.manifest.boundaries import group_pages
    from alie.models import BBox, Block, BlockSource, BlockType

    def page(idx, declaration_type, body):
        return [
            Block(id=f"s{idx}", bundle_id="bun", pdf_index=idx, type=BlockType.STAMP,
                  text=f"4/1/2025, 6:35 AM TO: +1450 PAGE {idx}/15",
                  bbox=BBox(0, 0, 400, 10), source=BlockSource.OCR, confidence=0.9, order=0),
            Block(id=f"d{idx}", bundle_id="bun", pdf_index=idx, type=declaration_type,
                  text="Note - Soins infirmiers", bbox=BBox(0, 60, 400, 72),
                  source=BlockSource.OCR, confidence=0.9, order=1),
            Block(id=f"b{idx}", bundle_id="bun", pdf_index=idx, type=BlockType.PARAGRAPH,
                  text=body, bbox=BBox(0, 90, 400, 102), source=BlockSource.OCR,
                  confidence=0.9, order=2),
        ]

    # Same transmission throughout, so the fax banner offers no boundary here.
    pages = {
        1: page(1, BlockType.HEADING, "Première note infirmière"),
        2: page(2, BlockType.PARAGRAPH, "Deuxième note, lue comme un paragraphe"),
        3: page(3, BlockType.PARAGRAPH, "Troisième note"),
    }
    groups, signals = group_pages(pages, {i: 792.0 for i in pages}, pack)

    assert groups == [[1], [2], [3]]
    assert all(
        "declares its own class" in " ".join(signals[i].reasons) for i in (1, 2, 3)
    )
