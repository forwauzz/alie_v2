"""Flags (PRD §9) and the five seams (§13.4)."""

from __future__ import annotations

import os

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

    # `parse.ocr` shipped off, was measured against the reference case, and its own metric
    # turned it on: the free path could read 9% of report units, with OCR 81%. That is the
    # register working as designed (§9.2), not a default drifting.
    assert defaults["parse.ocr"] is True
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
    changed = flags.output_affecting(flags.resolve(run_flags={"parse.vision": True}))
    assert "parse.vision" in changed

    # Turning an implementation flag *off* also invalidates work.
    assert "parse.ocr" in flags.output_affecting(flags.resolve(run_flags={"parse.ocr": False}))

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


# --------------------------------------------------------------------------- OCR tier


def test_ocr_tier_is_only_registered_when_the_flag_and_the_binary_agree():
    """With the binary missing, pages fall through to unparseable exactly as when the flag
    was off — the flag must not promise a tier the machine cannot run."""
    from alie.parse import register_tiers
    from alie.parse.ocr import OcrConfig, available

    assert register_tiers({"parse.ocr": False}) == ["text_layer"]
    assert not available(OcrConfig(exe="", tessdata_dir=None, lang="fra", scale=3.0))


def test_tsv_rows_become_line_blocks_with_boxes_and_confidence():
    """Tesseract is called for its TSV, not its text: a string would throw away the word
    boxes every citation anchors to (§4.3)."""
    from alie.parse.ocr import _lines_from_tsv

    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
        "\tleft\ttop\twidth\theight\tconf\ttext"
    )
    rows = [
        header,
        "5\t1\t1\t1\t1\t1\t10\t20\t40\t12\t92.0\tEntorse",
        "5\t1\t1\t1\t1\t2\t55\t20\t50\t12\t88.0\tlombaire",
        # A different line of the same paragraph.
        "5\t1\t1\t1\t2\t1\t10\t40\t30\t12\t70.0\tsuivi",
        # Below the confidence floor: speckle read as a character.
        "5\t1\t1\t1\t3\t1\t10\t60\t8\t8\t4.0\t~",
    ]
    lines = _lines_from_tsv("\n".join(rows))

    assert [text for text, _, _ in lines] == ["Entorse lombaire", "suivi"]
    assert lines[0][1] == (10, 20, 105, 32)
    assert abs(lines[0][2] - 0.90) < 1e-6


def test_low_confidence_words_are_dropped_not_shipped():
    from alie.parse.ocr import MIN_WORD_CONFIDENCE, _lines_from_tsv

    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
        "\tleft\ttop\twidth\theight\tconf\ttext"
    )
    junk = f"5\t1\t1\t1\t1\t1\t0\t0\t5\t5\t{MIN_WORD_CONFIDENCE - 1}\t¬"
    assert _lines_from_tsv(f"{header}\n{junk}") == []


def test_ocr_blocks_are_anchored_in_pdf_points_not_image_pixels(store):
    """A citation must mean the same thing whatever tier produced it, so the render scale
    is divided back out (§4.3)."""
    import pytest

    from alie.devkit import fixtures
    from alie.parse import ocr
    from alie.seams.parser import PageInput

    config = ocr.load_config()
    if not ocr.available(config):
        pytest.skip("tesseract not installed on this machine")

    path = str(fixtures.fixture_path("tiny", "Medical.pdf"))
    from alie.parse import pdfium as pdfium_io

    width, height = pdfium_io.page_sizes(path)[0]
    blocks = ocr.ocr_page(PageInput("bun", 1, path, width, height), config)

    assert blocks
    for block in blocks:
        assert 0 <= block.bbox.x0 <= width + 1
        assert 0 <= block.bbox.y0 <= height + 1
        assert block.source.value == "ocr"
        assert block.attrs["engine"].startswith("tesseract-")


# ----------------------------------------------------------------------------- packs


def test_every_pack_pattern_compiles():
    """Pack rules are data, and YAML double-quotes eat a lone backslash — `"\s"` arrives
    as `s`. A pattern that fails to compile, or silently means something else, would show
    up as a document that never classifies rather than as an error."""
    import re

    from alie.packs import available, load

    for pack_id in available():
        pack = load(pack_id)
        for spec in pack.class_list:
            for field in ("declares", "headings", "body"):
                for pattern in spec.get(field, []):
                    re.compile(pattern)
                    assert "\s" in pattern or " " not in pattern.strip(), (
                        f"{pack_id}/{spec['id']}/{field}: {pattern!r} has a literal space; "
                        "OCR output is not reliably spaced"
                    )
        for role, cues in pack.date_roles.items():
            for cue in cues.get("cues", []):
                re.compile(cue), role


def test_every_class_has_a_date_rule():
    """A class with no entry in the date rule table falls to the `unknown` priority, which
    silently gives it the wrong date rather than failing (§8.4)."""
    from alie.packs import available, load

    for pack_id in available():
        pack = load(pack_id)
        table = pack.date_rule_table
        for spec in pack.class_list:
            assert spec["id"] in table, f"{pack_id}: {spec['id']} has no date rule"


def test_dotenv_never_overrides_an_exported_variable(tmp_path, monkeypatch):
    """A file on disk must never silently beat a key exported for one run, or you get a
    run that used a different credential than the one you set, with no way to tell from
    the output (§13.4)."""
    from alie.config import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("ALIE_TEST_TOKEN=from-file\n", encoding="utf-8")

    monkeypatch.setenv("ALIE_TEST_TOKEN", "from-shell")
    assert load_dotenv(env_file) == []
    assert os.environ["ALIE_TEST_TOKEN"] == "from-shell"

    monkeypatch.delenv("ALIE_TEST_TOKEN")
    assert load_dotenv(env_file) == ["ALIE_TEST_TOKEN"]
    assert os.environ["ALIE_TEST_TOKEN"] == "from-file"
    monkeypatch.delenv("ALIE_TEST_TOKEN", raising=False)


def test_dotenv_returns_names_never_values(tmp_path, monkeypatch):
    """The return value reaches logs and stdout; a credential must not."""
    from alie.config import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text('ALIE_TEST_SECRET="sk-ant-not-a-real-key"\n', encoding="utf-8")
    monkeypatch.delenv("ALIE_TEST_SECRET", raising=False)

    applied = load_dotenv(env_file)

    assert applied == ["ALIE_TEST_SECRET"]
    assert "sk-ant" not in " ".join(applied)
    monkeypatch.delenv("ALIE_TEST_SECRET", raising=False)


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    from alie.config import load_dotenv

    assert load_dotenv(tmp_path / "nope.env") == []


def test_the_env_example_ships_no_value():
    """A committed example with a real key in it is the classic way secrets leak."""
    from pathlib import Path

    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("ANTHROPIC_API_KEY"):
            assert line.strip() == "ANTHROPIC_API_KEY="


def test_each_task_resolves_its_own_model(monkeypatch):
    """`ALIE_MODEL_<TASK>` is per task (§13.4). It used to fall through to
    ALIE_MODEL_EXTRACT for every task, so choosing a cheaper extraction model would
    silently change the transcription model too."""
    pytest.importorskip("anthropic")
    from alie.seams.anthropic_backend import DEFAULT_MODEL, AnthropicBackend

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("ALIE_MODEL_EXTRACT", "claude-haiku-4-5")
    monkeypatch.delenv("ALIE_MODEL_VISION", raising=False)

    assert AnthropicBackend(task="extract").name == "claude-haiku-4-5"
    # Vision is untouched by the extraction setting.
    assert AnthropicBackend(task="vision").name == DEFAULT_MODEL

    monkeypatch.setenv("ALIE_MODEL_VISION", "claude-opus-5")
    assert AnthropicBackend(task="vision").name == "claude-opus-5"
