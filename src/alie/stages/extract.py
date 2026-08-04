"""Stage 4b — model extract: prose and judgement (PRD §4.2).

In: unit + template. Out: filled schema. Fails by: invention, **or omission**. Proven
when: groundedness 100%; field recall vs gold (§14.2).

The model never writes a row. It returns **spans** — a block id and character offsets —
and code slices the source text at those offsets. There is no field in the output schema
that carries model-authored prose, so invention is not merely detected here, it is
structurally unavailable (§1.1, §3.2).

Two things this stage does not do:

- **Choose the date.** Extraction output is overwritten by the engine's decision (§8.4).
  The row date never appears in this schema.
- **See the chronology.** An extraction subagent sees one report unit and never the other
  rows. A model that has read 60 rows smooths the 61st toward the learned pattern and
  produces grounded-*looking* text by copying from a neighbouring document in its own
  context. Isolated context is a correctness measure, not a cost measure (§5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..models import Block, Legibility, ReportUnit
from ..packs import Pack
from ..packs import load as load_pack
from ..packs.prompts import Prompt
from ..packs.prompts import resolve as resolve_prompt
from ..provenance import Producer
from ..seams import model as model_seam
from ..stores import audit, cases, manifest
from ..stores import blocks as blocks_store
from ..stores.records import Record, replace_for_unit

#: Output schema. Every field is an identifier or an integer — there is nowhere for the
#: model to write a sentence, which is what makes the grounding guarantee structural.
SELECTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "field": {
                        "type": "string",
                        "enum": [
                            "diagnostic",
                            "examen",
                            "conduite",
                            "decision",
                            "evenement",
                            "suivi",
                            "autre",
                        ],
                    },
                },
                "required": ["block_id", "start", "end", "field"],
                "additionalProperties": False,
            },
        },
        # Where the model reports text that tried to instruct it. Documents can contain
        # text aimed at the model; it is data, never commands (§13.5).
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lines", "notes"],
    "additionalProperties": False,
}

MIN_SPAN_CHARS = 3


@dataclass(frozen=True)
class ExtractResult:
    unit_id: str
    prompt: str | None
    model: str | None
    selected: int
    kept: int
    #: Spans that did not verify against the source. Groundedness must be 100%, so these
    #: are dropped and counted, never rendered (§11.3).
    rejected: tuple[str, ...] = ()
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    skipped: str | None = None
    injection_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def groundedness(self) -> float:
        return self.kept / self.selected if self.selected else 1.0


def run_unit(
    conn: sqlite3.Connection,
    unit_id: str,
    *,
    run_id: str | None = None,
    already_resolved: frozenset[str] = frozenset(),
) -> ExtractResult:
    unit = manifest.get_unit(conn, unit_id)
    if unit is None:
        raise KeyError(f"unknown unit: {unit_id}")

    def skip(reason: str, prompt_ref: str | None = None) -> ExtractResult:
        """Every way out of this stage is logged. A unit with no 4b records and no audit
        entry is indistinguishable from one the model read and found nothing in — and the
        difference between "refused" and "empty" is the whole point of the register."""
        audit.record(
            conn, subject_type="unit", subject_id=unit.id, action="extract", run_id=run_id,
            rule=f"skip.{reason}", prompt_version=prompt_ref, model=None,
            detail={"skipped": reason, "selected": 0, "kept": 0},
        )
        return ExtractResult(unit_id, prompt_ref, None, 0, 0, skipped=reason)

    # Illegible units never reach the model. Enforced again at the seam so no caller can
    # route around it — given noise a model produces fluent French clinical bullets that
    # appear nowhere in the source (§8.5, §9).
    if unit.legibility is Legibility.ILLEGIBLE:
        return skip("illegible")

    case = cases.get_case(conn, unit.case_id)
    pack = load_pack(unit.regime or case["primary_pack"])
    blocks = blocks_store.for_pages(conn, unit.bundle_id, unit.pages)
    if not blocks:
        return skip("no_blocks")

    prompt = resolve_prompt(pack, "extract_row_lines", doc_class=unit.doc_class)
    system, user = _render(prompt, unit, blocks, pack, already_resolved)

    backend = _backend()
    if backend is None:
        # The flag is on but nothing is configured. Skipping is the promise the register
        # makes: rows fall back to deterministic line selection rather than the run
        # failing (§9.2).
        return skip("no_model", prompt.ref)
    if not hasattr(backend, "select"):
        # The configured backend cannot do constrained selection. Refusing is right:
        # falling back to free text would give up the grounding guarantee silently.
        raise model_seam.ModelNotConfigured(
            "the extract task needs a backend supporting constrained span selection"
        )

    payload, response = backend.select(system, user, SELECTION_SCHEMA)

    # Truncation is release-blocking, not diagnostic (§12). A cut-off selection is
    # well-formed and looks complete — half a report's findings, silently. The stage
    # enforces this rather than trusting the backend to, because "the model returned
    # fewer lines" and "the model was cut off" are indistinguishable downstream.
    lines = [] if response.truncated else payload.get("lines", [])
    kept, rejected = _verify(lines, blocks)
    if response.truncated:
        rejected = [f"response truncated ({response.stop_reason}); selection discarded"]

    records = [
        Record(
            unit_id=unit.id,
            field=f"line.{index}",
            value=text,
            stage="4b",
            confidence=block.confidence,
            block_id=block.id,
            span_start=start,
            span_end=end,
            rule=f"prompt.{prompt.ref}",
            epistemic_tag="INF-H",
            prompt_version=prompt.ref,
            model=response.model,
        )
        for index, (block, start, end, text, _field) in enumerate(kept, start=1)
    ]
    replace_for_unit(
        conn, unit.id, "4b", records, Producer(model=response.model, prompt=prompt.ref)
    )

    result = ExtractResult(
        unit_id=unit.id,
        prompt=prompt.ref,
        model=response.model,
        selected=len(payload.get("lines", [])),
        kept=len(kept),
        rejected=tuple(rejected),
        stop_reason=response.stop_reason,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        injection_notes=tuple(payload.get("notes", [])),
    )
    audit.record(
        conn, subject_type="unit", subject_id=unit.id, action="extract", run_id=run_id,
        rule=f"prompt.{prompt.ref}", prompt_version=prompt.ref, model=response.model,
        detail={
            "selected": result.selected,
            "kept": result.kept,
            "rejected": list(result.rejected),
            "groundedness": result.groundedness,
            "stop_reason": result.stop_reason,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "notes": list(result.injection_notes),
        },
    )
    return result


def _backend() -> model_seam.ModelBackend | None:
    """The extract backend, registering the default one on first use.

    Same shape as the parse tiers: registration happens in the stage, not at startup, so
    a machine with no credential runs the deterministic floor with nothing to configure
    and nothing to fail (§13.4).
    """
    backend = model_seam.backend_for("extract")
    if not isinstance(backend, model_seam.UnconfiguredBackend):
        return backend

    from ..seams.anthropic_backend import register_if_configured

    return model_seam.backend_for("extract") if register_if_configured() else None


def _render(
    prompt: Prompt,
    unit: ReportUnit,
    blocks: list[Block],
    pack: Pack,
    already_resolved: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    listing = "\n".join(f"{b.id} | {b.text}" for b in blocks if b.is_body_text)
    # Fields a template already read from a known coordinate. Naming them keeps 4b from
    # re-selecting what 4a resolved deterministically — that is cost, and a chance for the
    # model to disagree with an answer that is already right (§4.2).
    resolved = (
        ", ".join(sorted(f.split(".")[0] for f in already_resolved)) if already_resolved else "—"
    )
    return prompt.render(
        doc_class_label=pack.class_label(unit.doc_class),
        row_date=unit.row_date.render() if unit.row_date else "—",
        already_resolved=resolved,
        blocks=listing,
    )


def _verify(
    lines: list[dict], blocks: list[Block]
) -> tuple[list[tuple[Block, int, int, str, str]], list[str]]:
    """Check every span against the source before anything is rendered.

    A span survives only if its block belongs to *this* unit and its offsets land inside
    that block's text. The returned string is sliced from the source, never taken from the
    model — so even a well-formed lie renders as whatever the document actually says.
    """
    by_id = {b.id: b for b in blocks}
    kept: list[tuple[Block, int, int, str, str]] = []
    rejected: list[str] = []
    seen: set[tuple[str, int, int]] = set()

    for line in lines:
        block_id = str(line.get("block_id", ""))
        block = by_id.get(block_id)
        if block is None:
            rejected.append(f"{block_id}: not a block of this unit")
            continue

        try:
            start, end = int(line["start"]), int(line["end"])
        except (KeyError, TypeError, ValueError):
            rejected.append(f"{block_id}: offsets missing or not integers")
            continue

        if not (0 <= start < end <= len(block.text)):
            rejected.append(f"{block_id}: span [{start},{end}] outside 0..{len(block.text)}")
            continue

        text = block.text[start:end].strip()
        if len(text) < MIN_SPAN_CHARS:
            rejected.append(f"{block_id}: span too short to be a line")
            continue

        key = (block_id, start, end)
        if key in seen:
            continue
        seen.add(key)
        kept.append((block, start, end, text, str(line.get("field", "autre"))))

    return kept, rejected
