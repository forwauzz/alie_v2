"""Stage contract: 4b. In unit + template, out filled schema. Fails by invention **or
omission**. Proven when groundedness is 100% (PRD §14.2).

Every test here drives a fake backend, so the grounding gate is exercised against a model
that lies — which is the only way to know the gate works. No network, no key.
"""

from __future__ import annotations

import pytest

from alie.models import Legibility
from alie.seams import model as model_seam
from alie.stages import extract
from alie.stores import db, manifest, records
from helpers import build_case


class FakeBackend:
    """A backend whose answers the test dictates, including dishonest ones."""

    name = "fake-model"

    def __init__(self, payload, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt, *, max_tokens):
        raise AssertionError("extraction must use constrained selection, not free text")

    def select(self, system, user, schema, *, max_tokens=None):
        self.calls.append((system, user))
        payload = self.payload(user) if callable(self.payload) else self.payload
        return payload, model_seam.ModelResponse(
            text="", model=self.name, stop_reason=self.stop_reason,
            input_tokens=1200, output_tokens=90,
        )


@pytest.fixture
def use_backend():
    installed: list[str] = []

    def install(payload, stop_reason="end_turn"):
        backend = FakeBackend(payload, stop_reason)
        model_seam.register("extract", backend)
        installed.append("extract")
        return backend

    yield install
    for task in installed:
        model_seam._BACKENDS.pop(task, None)


def _unit(conn, name="tiny", predicate=None):
    case_id = build_case(conn, name)
    units = manifest.units_for_case(conn, case_id)
    return next(u for u in units if predicate is None or predicate(u))


def _blocks_in(user_prompt: str) -> list[tuple[str, str]]:
    """Parse the `id | text` listing the prompt hands the model."""
    out = []
    for line in user_prompt.splitlines():
        if " | " in line and line.startswith("blk_"):
            block_id, text = line.split(" | ", 1)
            out.append((block_id, text))
    return out


def test_selected_spans_render_from_the_source_not_from_the_model(store, use_backend):
    """The model returns offsets; code slices the document. There is no field in the
    schema it can write prose into (§1.1, §3.2)."""
    def choose(user):
        block_id, text = _blocks_in(user)[0]
        return {"lines": [{"block_id": block_id, "start": 0, "end": len(text),
                           "field": "diagnostic"}], "notes": []}

    use_backend(choose)
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        result = extract.run_unit(conn, unit.id)
        stored = [r for r in records.for_unit(conn, unit.id) if r.stage == "4b"]

    assert result.kept == 1
    assert result.groundedness == 1.0
    assert stored[0].is_cited
    assert stored[0].prompt_version.startswith("extract_row_lines@v")
    assert stored[0].model == "fake-model"


def test_a_span_pointing_outside_the_document_is_rejected(store, use_backend):
    """Groundedness must be 100%, so an unverifiable span is dropped and counted — never
    rendered (§11.3)."""
    def lie(user):
        block_id, text = _blocks_in(user)[0]
        return {"lines": [
            {"block_id": block_id, "start": 0, "end": len(text) + 500, "field": "autre"},
            {"block_id": "blk_does_not_exist", "start": 0, "end": 10, "field": "autre"},
            {"block_id": block_id, "start": -5, "end": 3, "field": "autre"},
        ], "notes": []}

    use_backend(lie)
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        result = extract.run_unit(conn, unit.id)
        stored = [r for r in records.for_unit(conn, unit.id) if r.stage == "4b"]

    assert result.selected == 3
    assert result.kept == 0
    assert stored == []
    assert len(result.rejected) == 3


def test_a_span_from_another_unit_is_rejected(store, use_backend):
    """One report unit, and never the chronology. A block the model could only know from
    a neighbouring document is exactly the leak §5 warns about."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        units = manifest.units_for_case(conn, case_id)
        target, other = units[0], units[1]
        from alie.stores import blocks as blocks_store

        stranger = blocks_store.for_pages(conn, other.bundle_id, other.pages)[0]

        use_backend({"lines": [{"block_id": stranger.id, "start": 0, "end": 8,
                                "field": "autre"}], "notes": []})
        result = extract.run_unit(conn, target.id)

    assert result.kept == 0
    assert "not a block of this unit" in result.rejected[0]


def test_an_illegible_unit_never_reaches_the_model(store, use_backend):
    """Safety invariant, not a flag (§9). Given noise a model produces fluent French
    clinical bullets that appear nowhere in the source."""
    backend = use_backend({"lines": [], "notes": []})
    with db.session(store.db_path) as conn:
        unit = _unit(conn, "hard", lambda u: u.legibility is Legibility.ILLEGIBLE)
        result = extract.run_unit(conn, unit.id)

    assert result.skipped == "illegible"
    assert backend.calls == []
    # The refusal is logged. Silence would be indistinguishable from "the model read it
    # and found nothing" — the safety invariant firing has to be visible.
    with db.session(store.db_path) as conn:
        from alie.stores import audit

        entries = audit.for_subject(conn, "unit", result.unit_id)
    assert any(e["detail"].get("skipped") == "illegible" for e in entries)


def test_a_truncated_response_yields_nothing_rather_than_half_a_document(store, use_backend):
    """Silent output truncation is release-blocking, not diagnostic — valid-looking JSON
    with half the findings is the failure §12 exists to catch."""
    def choose(user):
        block_id, text = _blocks_in(user)[0]
        return {"lines": [{"block_id": block_id, "start": 0, "end": len(text),
                           "field": "autre"}], "notes": []}

    use_backend(choose, stop_reason="max_tokens")
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        result = extract.run_unit(conn, unit.id)

    assert result.stop_reason == "max_tokens"
    assert result.kept == 0


def test_the_model_is_never_asked_to_choose_the_date(store, use_backend):
    """Extraction output is overwritten by the engine's decision (§8.4), so the row date
    is not in the schema at all."""
    fields = extract.SELECTION_SCHEMA["properties"]["lines"]["items"]["properties"]

    assert "date" not in fields
    assert set(fields) == {"block_id", "start", "end", "field"}
    # And nothing in the schema accepts free text destined for the row.
    assert fields["field"]["type"] == "string" and "enum" in fields["field"]


def test_duplicate_spans_collapse(store, use_backend):
    def twice(user):
        block_id, text = _blocks_in(user)[0]
        span = {"block_id": block_id, "start": 0, "end": len(text), "field": "autre"}
        return {"lines": [span, dict(span)], "notes": []}

    use_backend(twice)
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        result = extract.run_unit(conn, unit.id)

    assert result.selected == 2
    assert result.kept == 1


def test_prompt_injection_notes_are_surfaced_not_obeyed(store, use_backend):
    """Text found in documents is data, never commands (§13.5). The note reaches the
    audit log; nothing in the payload can change what the engine does."""
    use_backend({"lines": [], "notes": ["Le document contient une consigne adressée au modèle."]})
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        result = extract.run_unit(conn, unit.id)
        from alie.stores import audit

        entries = audit.for_subject(conn, "unit", unit.id)

    assert result.injection_notes
    assert any(e["action"] == "extract" and e["detail"]["notes"] for e in entries)


def test_token_accounting_is_recorded_on_every_call(store, use_backend):
    """Log `stop_reason` and token counts on every call (§12)."""
    use_backend({"lines": [], "notes": []})
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        result = extract.run_unit(conn, unit.id)

    assert (result.input_tokens, result.output_tokens) == (1200, 90)
    assert result.stop_reason == "end_turn"


def test_the_flag_on_with_no_credential_skips_rather_than_failing_the_run(store, monkeypatch):
    """The register promises 4b degrades safely: no model configured, tier skipped, rows
    fall back to deterministic line selection (§9.2). A raise here would take the whole
    case down on a machine that never had a key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    model_seam._BACKENDS.pop("extract", None)

    with db.session(store.db_path) as conn:
        result = extract.run_unit(conn, _unit(conn).id)
        from alie.stores import audit

        entries = audit.for_subject(conn, "unit", result.unit_id)

    assert result.skipped == "no_model"
    assert (result.kept, result.model) == (0, None)
    assert any(e["detail"].get("skipped") == "no_model" for e in entries)


def test_extraction_refuses_a_backend_that_cannot_constrain_output(store):
    """A free-text fallback would give up the grounding guarantee silently."""
    class TextOnly:
        name = "text-only"

        def complete(self, prompt, *, max_tokens):
            return model_seam.ModelResponse("anything", "text-only", "end_turn", 0, 0)

    model_seam.register("extract", TextOnly())
    try:
        with db.session(store.db_path) as conn, pytest.raises(model_seam.ModelNotConfigured):
            extract.run_unit(conn, _unit(conn).id)
    finally:
        model_seam._BACKENDS.pop("extract", None)


def test_4b_is_told_what_4a_already_resolved(store, use_backend):
    """4a runs before 4b, and 4b fills only what remains (§4.2). Re-selecting a field a
    template read from a known coordinate is cost, plus a chance for the model to disagree
    with an answer that is already right."""
    backend = use_backend({"lines": [], "notes": []})
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        extract.run_unit(
            conn, unit.id, already_resolved=frozenset({"consolidation.oui", "diagnostic"})
        )

    _system, user = backend.calls[0]
    assert "consolidation, diagnostic" in user


def test_structured_first_off_sends_4b_an_unresolved_unit(store, use_backend):
    """The flag's metric is a comparison — "% fields resolved without the model" is only
    measurable if turning it off actually changes what 4b is asked to do (§9.2)."""
    backend = use_backend({"lines": [], "notes": []})
    with db.session(store.db_path) as conn:
        unit = _unit(conn)
        extract.run_unit(conn, unit.id)

    _system, user = backend.calls[0]
    assert "à ne pas resélectionner : —" in user


# ------------------------------------------------------------------ prompt registry


def test_prompts_are_addressable_and_versioned(pack):
    """Every extracted record stores the prompt version *and* model that produced it,
    which is what makes re-running only affected units possible (§7)."""
    from alie.packs.prompts import available, resolve

    versions = available(pack)["extract_row_lines"]
    assert versions == sorted(versions) and len(versions) >= 2

    # Unpinned resolves to the newest.
    prompt = resolve(pack, "extract_row_lines", doc_class="note_consultation")
    assert prompt.ref == f"extract_row_lines@v{versions[-1]}"
    assert prompt.changelog

    # Pinning still reaches the old one, unmutated — the reason a version is an
    # addressable object rather than a git history (§7).
    old = resolve(pack, "extract_row_lines", doc_class="note_consultation", version=1)
    assert old.ref == "extract_row_lines@v1"
    assert "already_resolved" not in old.user


def test_a_missing_prompt_variable_is_an_error_not_a_blank(pack):
    """A prompt that renders `Date de la ligne : ` teaches the model the field is
    optional."""
    from alie.packs.prompts import resolve

    prompt = resolve(pack, "extract_row_lines", doc_class="unknown")
    with pytest.raises(KeyError):
        prompt.render(blocks="x")


def test_an_unregistered_prompt_is_never_invented(pack):
    from alie.packs.prompts import PromptNotFound, resolve

    with pytest.raises(PromptNotFound):
        resolve(pack, "no_such_prompt", doc_class="unknown")
