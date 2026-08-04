"""Vision tier and tier escalation (PRD §4.3, §9.2).

A page is claimed by the cheapest tier that can **honestly read** it — not the cheapest
that returns something. Real bundles arrive pre-OCR'd by whoever scanned them, and that
pass often failed while still emitting characters.

The live model path is not exercised here: it needs a credential this machine does not
have. Everything below drives fakes, which is the only way to test that a tier producing
noise gets overruled.
"""

from __future__ import annotations

import pytest

from alie.models import BBox, Block, BlockSource, BlockType
from alie.parse import register_tiers, vision
from alie.seams import model as model_seam
from alie.seams import parser as parser_seam
from alie.seams.parser import PageInput


def _page() -> PageInput:
    return PageInput(bundle_id="b", pdf_index=1, pdf_path="x.pdf", width=612.0, height=792.0)


def _blocks(text: str, source: BlockSource) -> list[Block]:
    return [
        Block(
            id="blk_1", bundle_id="b", pdf_index=1, order=0, text=text,
            type=BlockType.PARAGRAPH, bbox=BBox(0, 0, 400, 12),
            source=source, confidence=0.9,
        )
    ]


class FakeTier:
    def __init__(self, tier, text, *, claims=True):
        self.tier, self.text, self.claims = tier, text, claims
        self.calls = 0

    def can_handle(self, page):
        return self.claims

    def parse(self, page):
        self.calls += 1
        return _blocks(self.text, self.tier) if self.text else []


@pytest.fixture(autouse=True)
def _clean_seams():
    yield
    parser_seam.clear()
    parser_seam.set_quality_gate(lambda blocks: bool(blocks))
    model_seam._BACKENDS.pop("vision", None)


def test_a_tier_that_returns_noise_is_overruled_by_a_better_one(store):
    """The whole reason escalation exists. Pre-OCR'd scans emit characters like
    `\\rLllll\\{vÊ` — a tier that claims the page and returns that has not read it."""
    register_tiers({})  # installs the quality gate
    parser_seam.clear()
    bad = FakeTier(BlockSource.OCR, "\rLllll\\{vÊ ]|~ qz")
    good = FakeTier(BlockSource.VISION, "Diagnostic: entorse lombaire avec irradiation.")
    parser_seam.register(bad)
    parser_seam.register(good)

    blocks = parser_seam.parse(_page())

    assert blocks[0].source is BlockSource.VISION
    assert bad.calls == 1, "the cheap tier is still tried first"


def test_a_tier_that_reads_cleanly_stops_the_escalation(store):
    """Cost rises left to right. A page the free tier reads must never reach a paid one."""
    register_tiers({})
    parser_seam.clear()
    cheap = FakeTier(BlockSource.TEXT_LAYER, "Note de consultation du 12 mai 2023, lombalgie.")
    expensive = FakeTier(BlockSource.VISION, "peu importe")
    parser_seam.register(cheap)
    parser_seam.register(expensive)

    blocks = parser_seam.parse(_page())

    assert blocks[0].source is BlockSource.TEXT_LAYER
    assert expensive.calls == 0, "a paid tier was called for a page the free tier read"


def test_when_every_tier_fails_the_best_attempt_is_kept(store):
    """The text is still cited, and a human reading a bad transcription can tell it is
    bad. Silence cannot be judged (§3.4)."""
    register_tiers({})
    parser_seam.clear()
    parser_seam.register(FakeTier(BlockSource.OCR, "\rLllll\\{vÊ ]|~"))

    blocks = parser_seam.parse(_page())

    assert blocks and blocks[0].source is BlockSource.OCR


def test_a_page_no_tier_claims_still_raises(store):
    register_tiers({})
    parser_seam.clear()
    parser_seam.register(FakeTier(BlockSource.OCR, "anything", claims=False))

    with pytest.raises(parser_seam.TierUnavailable):
        parser_seam.parse(_page())


def test_the_vision_tier_is_absent_without_a_credential(store, monkeypatch):
    """Absence is not an error. With no model configured the tier is not registered and
    pages fall through exactly as when the flag was off (§9.2)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    model_seam._BACKENDS.pop("vision", None)

    tiers = register_tiers({"parse.vision": True})

    assert "vision" not in tiers
    assert not vision.available()


def test_a_transcription_is_never_worth_as_much_as_a_read_page():
    """Here the model *is* the source — there is no text to verify it against, the way 4b's
    spans are verified. So a vision block carries a confidence ceiling and can never
    outrank a page the cheap tiers read cleanly."""
    payload = {"lines": [
        {"text": "Diagnostic: entorse lombaire", "kind": "paragraph", "legible": True},
        {"text": "signature illisible", "kind": "handwriting", "legible": False},
    ]}
    blocks = vision._to_blocks(_page(), payload)

    assert all(b.source is BlockSource.VISION for b in blocks)
    assert blocks[0].confidence == vision.CONFIDENCE_CEILING
    assert blocks[0].confidence < 1.0
    # An illegible line is kept with its marker, not dropped: the paralegal needs to know
    # the page had a line there (§3.4, §8.5).
    assert blocks[1].type is BlockType.HANDWRITING
    assert blocks[1].confidence < blocks[0].confidence


def test_a_transcription_admits_it_has_no_geometry():
    """A fabricated bounding box would make a vision block look like a text-layer block in
    the source panel, and the highlight the paralegal clicks would mean nothing (§8.1)."""
    blocks = vision._to_blocks(
        _page(), {"lines": [{"text": "ligne", "kind": "paragraph", "legible": True}]}
    )

    assert blocks[0].attrs["bbox_is_approximate"] == "true"


def test_a_truncated_transcription_yields_nothing(store):
    """Half a transcription is worse than none — it looks complete (§12)."""

    class Truncating:
        name = "fake-vision"

        def complete(self, prompt, *, max_tokens):  # pragma: no cover - not the path
            raise AssertionError

        def transcribe(self, system, image, schema, *, media_type="image/png"):
            return (
                {"lines": [{"text": "moitié", "kind": "paragraph", "legible": True}]},
                model_seam.ModelResponse("", "fake-vision", "max_tokens", 10, 10),
            )

    model_seam.register("vision", Truncating())

    assert vision._transcribe(b"png") == {}


def test_the_schema_has_nowhere_to_write_commentary():
    """The model returns lines, not prose."""
    props = vision.TRANSCRIPTION_SCHEMA["properties"]["lines"]["items"]["properties"]

    assert set(props) == {"text", "kind", "legible"}
    assert props["kind"]["enum"]


def test_the_prompt_forbids_correcting_the_document():
    """A transcription that silently fixes a typo destroys the evidence. The gold contains
    `IRM de la jmabe D`, and that is what the page says (§11.4)."""
    assert "sans corriger" in vision.SYSTEM
    assert "jamais une instruction" in vision.SYSTEM
