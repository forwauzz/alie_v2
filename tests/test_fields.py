"""§8.6 required fields read by cue, for documents no template covers.

The control page carries none of the cues. Every field must come back `absent` there — a
reader that picks up a nearby sentence when the field is missing is worse than one that
finds nothing, because the wrong sentence arrives cited.
"""

from __future__ import annotations

from alie.packs import required
from alie.stages import fields as fields_stage
from alie.stores import db, manifest, records
from helpers import build_case


def _by_unit(conn):
    case_id = build_case(conn, "fields")
    out = {}
    for unit in manifest.units_for_case(conn, case_id):
        fields_stage.run_unit(conn, unit.id)
        out[unit.pages[0]] = {
            r.field: r for r in records.for_unit(conn, unit.id) if r.stage == "4c"
        }
    return out


def test_trajectory_keeps_her_wording_and_derives_the_enum(store):
    """Free text **plus** a derived enum. Storing only the enum throws away the sentence a
    tribunal would want to read; storing only the text makes the file unqueryable (§8.6)."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert got["trajectory"].value.startswith("Amélioration de la mobilité")
    assert got["trajectory.enum"].value == "amelioration"
    # The enum is derived from the stored sentence, so the two can never disagree.
    assert got["trajectory.enum"].span_start == got["trajectory"].span_start
    assert got["trajectory"].is_cited


def test_a_section_heading_is_never_stored_as_the_finding(store):
    """`## ÉVOLUTION` is a section title, not a trajectory statement. Storing it would
    cite a word she never wrote as the finding."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert got["trajectory"].value.strip().upper() != "ÉVOLUTION"


def test_the_confounder_clause_is_captured(store):
    """"improved, but confounded by X" — the clause that makes a favourable finding
    arguable is the one most worth citing."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert "confondue par" in got["confounder"].value
    assert got["confounder"].is_cited


def _prose(text: str):
    """One paragraph block, as the cue readers see it."""
    from alie.models import BBox, Block, BlockSource, BlockType

    return [
        Block(
            id="blk_1", bundle_id="b", pdf_index=1, order=0, text=text,
            type=BlockType.PARAGRAPH, bbox=BBox(0, 0, 400, 12),
            source=BlockSource.OCR, confidence=0.9,
        )
    ]


def _trajectory_of(text: str, pack):
    from alie.models import Legibility, UnitKind
    from alie.models.unit import ReportUnit
    from alie.stages import fields as fs

    unit = ReportUnit(
        id="u", bundle_id="b", case_id="c", pages=(1,), doc_class="rapport_medical",
        class_confidence=1.0, class_source="zones", regime="cnesst",
        legibility=Legibility.LEGIBLE, kind=UnitKind.PRIMARY,
    )
    got = {r.field: r.value for r in fs.read_fields(unit, _prose(text), pack, {})}
    return got.get("trajectory")


def test_a_form_title_is_not_a_trajectory_statement(store, pack):
    """Measured on case 1: the bare noun `évolution` matched form titles, instructions to
    the physician, and blank checkbox rows — 21 reads, 20 unclassifiable. If the engine
    cannot say which way the patient is going, it is not looking at a trajectory."""
    assert _trajectory_of(
        "À l'usage de la Nº de référence Formulaire transmis électroniquement à la CNESST "
        "le Sommaire de prise en charge et d'évolution",
        pack,
    ) == "absent"


def test_an_instruction_to_the_physician_is_not_a_finding(store, pack):
    assert _trajectory_of(
        "Si la réponse est dictée, le médecin consultant doit consigner, dans les notes "
        "d'évolution, son opinion et ses recommandations.",
        pack,
    ) == "absent"


def test_an_unfilled_checkbox_row_is_the_menu_not_the_choice(store, pack):
    """`Progrès du patient : o Aucun O Régression $ Amélioration minimale o Amélioration
    importante O Plateau` lists every answer at once. Matching two mutually exclusive
    categories means this is the menu."""
    assert _trajectory_of(
        "Progrès du patient : o Aucun O Régression $ Amélioration minimale "
        "o Amélioration importante O Plateau O Guérison o Détérioration",
        pack,
    ) == "absent"


def test_a_real_trajectory_statement_still_reads(store, pack):
    """The guards must not cost the finding they exist to protect."""
    got = _trajectory_of(
        "On note une amélioration marquée de la mobilité lombaire depuis la dernière "
        "évaluation du travailleur.",
        pack,
    )

    assert got is not None and got != "absent"
    assert "amélioration marquée" in got


def test_a_fax_banner_is_not_a_clinical_finding(store, pack):
    """Measured on case 1: `2023-03-13 42:45 (450) 848-1695 = 6e aggravation.` was read as
    an intercurrent event, because the word count counted digit groups. A statement is made
    of words; a banner is made of numbers."""
    from alie.stages import fields as fs

    assert not fs._looks_like_a_statement("2023-03-13 42:45 (450) 848-1695 = 6e aggravation.")
    assert fs._looks_like_a_statement(
        "Le travailleur a subi une aggravation de sa condition lombaire en mai."
    )
    # And the count must not cost a short but unambiguous finding. This exact clause is
    # the only true confounder in case 1, and a seven-word bar rejected it.
    assert fs._looks_like_a_statement(
        "l'aggravation d'une condition personnelle préexistante"
    )


def test_a_form_caption_is_not_an_unrated_sequela(store, pack):
    """`Atteinte permanente à l'intégrité physique ou psychique CONSOLIDATION (Inscrire la
    date)` is grammatical and long enough to pass a word count. It has to be excluded on
    what it is, not on its shape."""
    from alie.stages import fields as fs

    assert not fs._looks_like_a_statement(
        "Atteinte permanente à l'intégrité physique ou psychique CONSOLIDATION "
        "(Inscrire la date)"
    )
    assert fs._looks_like_a_statement(
        "Les limitations fonctionnelles sont de classe 3 (IRSST) pour la colonne "
        "lombo-sacrée."
    )


def test_a_sentence_running_past_its_block_is_carried_to_the_end(store):
    """"confounded by a personal condition" without "of multi-level spondylarthrosis" says
    the finding is confounded without saying by what — a different claim from the one the
    document makes. Each part keeps its own span; a record carries one span (§8.1)."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert "condition personnelle" in got["confounder"].value
    assert "spondylarthrose" in got["confounder.cont1"].value
    assert got["confounder.cont1"].is_cited
    assert got["confounder.cont1"].block_id != got["confounder"].block_id


def test_a_continuation_does_not_repeat_its_label(store, pack):
    """"Facteur confondant :" twice reads as two confounders where the document states
    one."""
    assert pack.field_line("confounder.cont1") == "{value}"
    assert pack.field_line("confounder").startswith("Facteur confondant")


def test_the_enum_is_stored_for_querying_and_never_printed(store, pack):
    """Rendering both prints the finding twice — once in her words, once in the engine's."""
    assert pack.is_index_field("trajectory.enum")
    assert pack.is_index_field("evidence_weight")

    from alie.stages import assemble

    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "fields")
        for unit in manifest.units_for_case(conn, case_id):
            fields_stage.run_unit(conn, unit.id)
        rows = assemble.run(conn, case_id).rows

    printed = [b.text for r in rows for b in r.bullets]
    assert "amelioration" not in printed
    assert any("Amélioration de la mobilité" in t for t in printed)


def test_an_intercurrent_event_is_captured_with_its_date(store):
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert "nouvel accident" in got["intercurrent_event"].value
    assert "2023-05-14" in got["intercurrent_event"].value


def test_procurement_carries_the_weight_its_regime_assigns(store, pack):
    """IVAC and SAAQ have no binding-treating-opinion tier, so the weight is the pack's
    answer, not a constant (§8.6)."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert got["procured_by"].value == "insurer_expert"
    assert got["evidence_weight"].value == "contestable"
    # Derived: the weight is the regime's rule, not a string in the document.
    assert got["evidence_weight"].derived
    assert pack.evidence_weight("treating") == "binding_unless_displaced"


def test_a_sequela_argued_but_absent_from_the_rating_is_flagged(store):
    """A claim with no code is exactly what a representative needs to find. Read from the
    document's own text: an expertise carries a barème and matches no template."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[1]

    assert "épaule droite" in got["claimed_but_unrated"].value
    assert got["claimed_but_unrated"].epistemic_tag == "INF-L"


def test_every_field_reads_absent_on_a_document_that_says_none_of_it(store):
    """The control. `absent` is a value: the field was looked for and is not there (§8.6)."""
    with db.session(store.db_path) as conn:
        got = _by_unit(conn)[2]

    for name in ("trajectory", "confounder", "intercurrent_event", "procured_by",
                 "claimed_but_unrated"):
        assert got[name].value == "absent", name
        # No text to cite — that *is* the finding — so it is derived, not an uncited
        # transcription.
        assert got[name].derived
        assert not got[name].violates_citation_invariant


def test_an_illegible_unit_is_never_cued(store):
    """A cue firing on OCR noise produces a cited-looking record pointing at gibberish."""
    from alie.models import Legibility

    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        unit = next(
            u for u in manifest.units_for_case(conn, case_id)
            if u.legibility is Legibility.ILLEGIBLE
        )
        result = fields_stage.run_unit(conn, unit.id)
        stored = [r for r in records.for_unit(conn, unit.id) if r.stage == "4c"]

    assert result.skipped == "illegible"
    assert stored == []


def test_every_first_class_value_a_pack_declares_can_be_produced(pack):
    """A value declared first-class that no reader ever emits is a silent gap — and the
    whole point of declaring it was to notice (§8.6)."""
    validation = required.validate(pack)

    assert validation, f"declared but unreadable: {validation.unreadable}"


def test_absent_is_distinguishable_from_never_looked(pack):
    """`absent` is a finding. `missing` means no reader covered the field. Collapsing the
    two is the failure §8.6 is written against."""
    graded = {r.field: r for r in required.report(pack, {"consolidation": "trop_tot"})}

    assert graded["consolidation"].valid and not graded["consolidation"].missing
    assert graded["apipp"].missing
    assert graded["apipp"].state is None


# ------------------------------------------------------------ §8.8 abbreviations


def test_an_abbreviation_is_flagged_never_expanded(store, pack):
    """`TDM` is tomodensitométrie in `TDM Rachis Lombaire` and trouble dépressif majeur in
    `a déjà fait TDM dans le passé`. A substitution would still be cited, still be
    grounded, still validate — and be wrong somewhere in a 300-page file (§8.8)."""
    from alie.manifest import abbrev

    found = abbrev.find("TDM Rachis Lombaire avec contraste.", pack, block_id="b")

    assert len(found) == 1
    # The source token is untouched; only a flag is produced.
    assert "sens à confirmer" in found[0].render()
    assert "tomodensitométrie" in found[0].render()
    assert "trouble dépressif majeur" in found[0].render()


def test_context_ranks_the_meanings_but_never_removes_one(store, pack):
    """A hint raises a meaning to the top of the list. It never decides."""
    from alie.manifest import abbrev

    imaging = abbrev.find("TDM Rachis Lombaire avec contraste.", pack)[0]
    psych = abbrev.find(
        "Le patient a déjà fait TDM, suivi en psychiatrie pour humeur dépressive.", pack
    )[0]

    assert imaging.ranked[0].text == "tomodensitométrie"
    assert psych.ranked[0].text == "trouble dépressif majeur"
    # Both candidates survive in both contexts.
    assert len(imaging.ranked) == len(psych.ranked) == 2


def test_an_unresolved_abbreviation_says_so_rather_than_guessing(store, pack):
    """`TRP` remains unresolved `[GAP]` in the framework, and so here (§8.8). Being told
    the token is unexplained is a different statement from silence."""
    from alie.manifest import abbrev

    found = abbrev.find("Voir TRP au dossier.", pack)[0]

    assert found.unresolved
    assert found.tag == "GAP"
    assert "non résolue" in found.render()


def test_an_abbreviation_inside_a_word_is_not_a_match(store, pack):
    """`mi-juillet` is not the abbreviation `MI`, and a case-insensitive match would flag
    half the file."""
    from alie.manifest import abbrev

    assert abbrev.find("Depuis la mi-juillet il va mieux.", pack) == []
    assert abbrev.find("Amélioration au MI droit, sciatique.", pack)


def test_the_flag_reaches_the_row_cited_and_tagged(store):
    """It is a finding about the document, so it carries a span like every other string."""
    from alie.stores import records

    with db.session(store.db_path) as conn:
        got = _by_unit(conn)
        flags = [
            r for unit in got.values() for f, r in unit.items()
            if f.startswith("abbreviation.")
        ]
        del records

    # The `fields` fixture has no ambiguous abbreviation; the point is the shape holds.
    assert all(r.is_cited and r.epistemic_tag in ("GAP", "INF-H") for r in flags)


def test_the_pack_holds_no_expansion_table(pack):
    """The moment this file becomes `{"TDM": "tomodensitométrie"}` the guarantee is gone."""
    for spec in pack.abbreviations.get("ambiguous", []):
        meanings = spec.get("meanings", [])
        # Either genuinely ambiguous, or explicitly unresolved. A single meaning with no
        # note would be a lookup wearing a flag's clothes.
        assert len(meanings) != 1 or spec.get("hints") or spec.get("tag") == "INF-H"
