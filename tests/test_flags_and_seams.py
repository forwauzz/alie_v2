"""Flags (PRD §9) and the five seams (§13.4)."""

from __future__ import annotations

import pytest

from alie import flags
from alie.seams import model, parser


def test_every_flag_defines_the_metric_that_judges_it():
    """A flag without a defined metric is a preference, not an experiment (§9.2)."""
    for flag in flags.REGISTER:
        assert flag.metric, flag.id
        assert flag.question, flag.id


def test_unproven_features_default_off_and_proven_ones_on():
    defaults = flags.defaults()

    assert defaults["parse.ocr"] is False
    assert defaults["parse.vision"] is False
    assert defaults["dedupe.enabled"] is False
    assert defaults["manifest.orphan_rejoin"] is False
    assert defaults["parse.templates"] is True
    assert defaults["extract.structured_first"] is True


def test_resolution_runs_global_then_case_then_run():
    resolved = flags.resolve(
        global_flags={"parse.ocr": True},
        case_flags={"parse.ocr": False, "dedupe.enabled": True},
        run_flags={"dedupe.enabled": False},
    )

    assert resolved["parse.ocr"] is False
    assert resolved["dedupe.enabled"] is False


def test_unknown_flag_is_rejected():
    with pytest.raises(KeyError):
        flags.resolve(run_flags={"parse.telepathy": True})


def test_model_and_prompt_flags_are_open_namespaces():
    resolved = flags.resolve(run_flags={"model.extract": "x", "prompt.rem_v3": "12"})
    assert resolved["model.extract"] == "x"


def test_implementation_flags_carry_a_rerun_badge():
    """Behaviour flags are safe mid-case; implementation flags invalidate work (§9)."""
    changed = flags.output_affecting(flags.resolve(run_flags={"parse.ocr": True}))
    assert "parse.ocr" in changed

    behaviour_only = flags.output_affecting(flags.resolve(run_flags={"render.doctype_code": True}))
    assert behaviour_only == []


def test_safety_invariants_are_not_flags():
    """They appear read-only and produce no data point when disabled (§9, §9.3)."""
    ids = {f.id for f in flags.REGISTER}
    assert not ids & {"model.illegible_gate", "render.allow_uncited", "dedupe.auto_remove_any"}
    assert len(flags.SAFETY_INVARIANTS) == 3


def test_model_seam_refuses_illegible_input():
    """Illegible units never reach the model, enforced at the seam so no caller can route
    around it (§8.5, §9)."""
    with pytest.raises(model.IllegibleInputRefused):
        model.complete("prompt", "extract", legible=False)


def test_model_seam_fails_loudly_when_unconfigured():
    """Phase 1 makes no model calls. An unconfigured task must raise, never return
    plausible text."""
    with pytest.raises(model.ModelNotConfigured):
        model.complete("prompt", "extract")


def test_truncation_is_detected_from_stop_reason():
    """Log `stop_reason` on every call; truncation is release-blocking (§12)."""
    ok = model.ModelResponse("x", "m", "end_turn", 10, 10)
    cut = model.ModelResponse("x", "m", "max_tokens", 10, 4096)

    assert not ok.truncated
    assert cut.truncated


def test_parser_seam_reports_which_tiers_are_registered():
    from alie.parse import register_default_tiers

    register_default_tiers()
    assert parser.registered_tiers() == ["text_layer"]


def test_parser_seam_raises_when_no_tier_handles_a_page():
    parser.clear()
    with pytest.raises(parser.TierUnavailable):
        parser.parse(parser.PageInput("b", 1, "x.pdf", 612, 792))
    from alie.parse import register_default_tiers

    register_default_tiers()


def test_producer_keys_derived_artifacts_by_input_and_config():
    """Switching OCR recomputes only affected pages (§9 rule 2)."""
    from alie.provenance import Producer, derived_key

    a = derived_key("hash1", Producer(ocr="none"))
    b = derived_key("hash1", Producer(ocr="tesseract"))
    c = derived_key("hash2", Producer(ocr="none"))

    assert a != b and a != c and b != c
    assert derived_key("hash1", Producer(ocr="none")) == a
