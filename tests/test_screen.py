"""Regime screening (PRD §6.1, §15.1).

Regime is a property of the unit, not the case. The IVAC gold file contains CNESST
documents — the victim was a logger assaulted on his own logging property, and LATMP takes
precedence. Case-level regime would read those units with the wrong vocabulary and the
wrong impairment math.
"""

from __future__ import annotations

from alie import flags
from alie.devkit import fixtures
from alie.manifest import screen
from alie.models import BBox, Block, BlockSource, BlockType
from alie.packs import load as load_pack
from alie.stages import ingest, manifest_build, parse
from alie.stores import cases, db, manifest


def _blocks(*lines: str) -> list[Block]:
    return [
        Block(
            id=f"blk_{i}", bundle_id="b", pdf_index=1, order=i, text=text,
            type=BlockType.HEADING if i == 0 else BlockType.PARAGRAPH,
            bbox=BBox(0, 0, 400, 12), source=BlockSource.TEXT_LAYER, confidence=1.0,
        )
        for i, text in enumerate(lines)
    ]


def _build(conn, *, screen_on: bool):
    resolved = flags.resolve(run_flags={"screen.per_unit_regime": screen_on})
    case_id = cases.create_case(conn, f"mixed-{screen_on}", "cnesst")
    bundle_id = ingest.add_pdf_path(
        conn, case_id=case_id,
        path=fixtures.fixture_path("mixed", "Medical.pdf"), folder_label="Médical",
    )
    parse.run(conn, bundle_id, flags=resolved)
    result = manifest_build.run(conn, bundle_id, flags=resolved)
    units = {u.pages[0]: u for u in manifest.units_for_case(conn, case_id)}
    return result, units


def test_a_saaq_report_inside_a_cnesst_case_is_retagged(store):
    """Same shape as the IVAC gold file, which contains CNESST documents (§6.1)."""
    with db.session(store.db_path) as conn:
        result, units = _build(conn, screen_on=True)

    assert units[2].regime == "saaq"
    assert units[2].attrs["regime_source"] == "screened"
    # The flag's metric verbatim: units whose regime differs from the case default (§9.2).
    assert result.off_regime == 1


def test_the_override_carries_the_evidence_that_earned_it(store):
    """An override without its evidence is unreviewable — the why-panel has to show what
    the unit said (§7.1)."""
    with db.session(store.db_path) as conn:
        _result, units = _build(conn, screen_on=True)

    assert "assurance" in units[2].attrs["regime_matched"]
    assert float(units[2].attrs["regime_confidence"]) >= screen.MIN_CONFIDENCE


def test_cnesst_documents_keep_the_case_default(store):
    """The control. A screener that moves regimes on weak evidence is worse than one that
    never moves them: the wrong vocabulary and the wrong impairment math arrive looking
    correct."""
    with db.session(store.db_path) as conn:
        _result, units = _build(conn, screen_on=True)

    assert units[1].regime == "cnesst"
    assert units[3].regime == "cnesst"
    assert units[3].attrs["regime_source"] == "case_default"


def test_screening_off_leaves_every_unit_on_the_case_regime(store):
    """The flag is off by default: it asks whether mixed-regime is real or a one-off, and
    an implementation flag that changes output silently is the thing §9 forbids."""
    with db.session(store.db_path) as conn:
        result, units = _build(conn, screen_on=False)

    assert result.off_regime == 0
    assert {u.regime for u in units.values()} == {"cnesst"}
    assert "regime_source" not in units[2].attrs


def test_silence_is_never_read_as_a_regime():
    """A unit that says nothing keeps the default. Guessing from silence would read a
    CNESST report with SAAQ vocabulary on the strength of no evidence at all."""
    got = screen.screen(
        "u", _blocks("RÉSULTAT DE LABORATOIRE", "Hémogramme complet."),
        default_pack="cnesst",
    )

    assert got.regime == "cnesst"
    assert got.is_default


def test_a_tie_goes_to_the_case_default():
    """The case is the paralegal's own statement of what this file is. It takes real
    evidence to overrule her, not a draw."""
    both = _blocks(
        "RAPPORT",
        "Réclamation CNESST pour lésion professionnelle; accident d'automobile SAAQ.",
    )
    got = screen.screen("u", both, default_pack="cnesst")

    assert got.regime == "cnesst"


def test_a_form_serial_outranks_wording():
    """A form serial is printed by the issuing body, not typed by a clinician (§4.4)."""
    pack = load_pack("cnesst")
    score, matched = screen.score(_blocks("RAPPORT MÉDICAL"), pack, form_serial="1918")

    assert score >= screen.MIN_CONFIDENCE
    assert "serial:1918" in matched


def test_the_engine_holds_no_regime_knowledge():
    """No `if regime == ...` anywhere. Detection comes from each pack's own identity block,
    so adding a regime is authoring a pack (§6)."""
    from pathlib import Path

    source = Path("src/alie/manifest/screen.py").read_text(encoding="utf-8")
    for regime in ("cnesst", "saaq", "ivac"):
        assert f'== "{regime}"' not in source
        assert f"'{regime}'" not in source


def test_a_pack_declares_how_its_documents_identify_themselves():
    for pack_id in ("cnesst", "saaq"):
        identity = load_pack(pack_id).pack.get("identity", {})
        assert identity.get("strong"), f"{pack_id} states no strong identity signal"


def test_saaq_names_the_gaps_it_cannot_read_yet():
    """`[GAP]` is a first-class epistemic tag (§6.2). A gap somebody wrote down is a
    decision; a gap nobody wrote down is a defect."""
    from alie.packs import required

    pack = load_pack("saaq")
    validation = required.validate(pack)

    assert validation, f"undeclared unreadable fields: {validation.unreadable}"
    assert "stabilisation" in validation.declared_gaps
    # And every declared gap says why, so it can be closed rather than forgotten.
    assert all(required.known_gaps(pack))


def test_saaq_uses_its_own_vocabulary_not_cnesst_translated():
    """`consolidation` under CNESST is `stabilisation` here; APIPP there is IPNP here (§6).
    A chronology using the wrong one reads as though the paralegal does not know the
    regime."""
    saaq, cnesst = load_pack("saaq"), load_pack("cnesst")

    assert saaq.pack["vocabulary"]["milestone"] == "stabilisation"
    assert cnesst.pack["vocabulary"]["milestone"] == "consolidation"
    assert saaq.field_line("stabilisation")
    # And SAAQ has no binding-treating-opinion tier (§8.6).
    assert saaq.evidence_weight("treating") == "contestable"
    assert cnesst.evidence_weight("treating") == "binding_unless_displaced"
