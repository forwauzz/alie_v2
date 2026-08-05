"""Anthropic backend for the model seam (PRD §13.4).

The seam's contract is `complete(prompt, task) -> text`; this is one implementation of it.
Config and secrets come from the environment, never from code:

  ANTHROPIC_API_KEY      credential, read by the SDK itself — never passed through ALIE
  ALIE_MODEL_<TASK>      model id per task, e.g. ALIE_MODEL_EXTRACT
  ALIE_MODEL_MAX_TOKENS  output ceiling per call

`stop_reason` and token counts are recorded on every call. Truncation is release-blocking,
not diagnostic — the failure is silent and fluent (§12).
"""

from __future__ import annotations

import json
import os
from typing import Any

from .model import ModelResponse

#: Anthropic's current default. Overridable per task so a model swap is a config change
#: plus a re-run of the regime golds (§13.4).
DEFAULT_MODEL = "claude-opus-5"

#: Comfortably under the SDK's non-streaming timeout guard. A unit is one report, not a
#: bundle, so extraction output is small by construction (§12).
DEFAULT_MAX_TOKENS = 8000


class AnthropicBackend:
    """Implements the `ModelBackend` protocol using the official SDK."""

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int | None = None,
        *,
        task: str = "extract",
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "the anthropic SDK is not installed; `uv pip install -e \".[model]\"`"
            ) from exc

        # `ALIE_MODEL_<TASK>`, resolved per task (§13.4). It previously fell through to
        # `ALIE_MODEL_EXTRACT` for every task, so setting a cheaper extraction model would
        # silently change the *transcription* model too — two different jobs behind one
        # accidental knob, and the kind of coupling nobody discovers until the output is
        # already worse.
        self.name = model or os.environ.get(
            f"ALIE_MODEL_{task.upper()}", DEFAULT_MODEL
        )
        self.max_tokens = max_tokens or int(
            os.environ.get("ALIE_MODEL_MAX_TOKENS", DEFAULT_MAX_TOKENS)
        )
        self._client = anthropic.Anthropic()

    def complete(self, prompt: str, *, max_tokens: int) -> ModelResponse:
        """Plain-text completion. Extraction uses `select` instead — see below."""
        message = self._client.messages.create(
            model=self.name,
            max_tokens=max_tokens or self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return _to_response(message, self.name)

    def select(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], ModelResponse]:
        """Constrained call returning JSON that validates against `schema`.

        The schema carries no field the model can write prose into — only block ids and
        character offsets. Structured outputs guarantee the shape; the engine still
        verifies every span against the source before anything is rendered (§11.3).
        """
        message = self._client.messages.create(
            model=self.name,
            max_tokens=max_tokens or self.max_tokens,
            # The system prompt is identical across every unit of a run, so caching it
            # turns a per-unit cost into a per-run one on a 3000-page file (§7).
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        response = _to_response(message, self.name)

        # A refusal returns HTTP 200 with empty or partial content. Reading content[0]
        # unconditionally would crash here, and treating a partial as complete would
        # silently drop half a document's findings.
        if message.stop_reason == "refusal":
            return {}, response
        if response.truncated:
            return {}, response

        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            return json.loads(text), response
        except json.JSONDecodeError:
            # Structured outputs make this near-impossible, but a parse failure paired
            # with a truncation is exactly §12's silent-output case — refuse, don't guess.
            return {}, response


    def transcribe(
        self,
        system: str,
        image_base64: str,
        schema: dict[str, Any],
        *,
        media_type: str = "image/png",
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], ModelResponse]:
        """Read a page image and return its lines (§4.3 vision tier).

        Unlike extraction, there is no source text to verify the answer against — here the
        model *is* the source. The engine compensates by stamping every block
        `BlockSource.VISION` at a confidence ceiling, so a transcription never carries the
        weight of a page the cheap tiers read cleanly.
        """
        message = self._client.messages.create(
            model=self.name,
            max_tokens=max_tokens or self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        }
                    ],
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        response = _to_response(message, self.name)
        if message.stop_reason == "refusal" or response.truncated:
            return {}, response
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            return json.loads(text), response
        except json.JSONDecodeError:
            return {}, response


def _to_response(message: Any, model: str) -> ModelResponse:
    usage = getattr(message, "usage", None)
    return ModelResponse(
        text=next((b.text for b in message.content if b.type == "text"), ""),
        model=getattr(message, "model", model),
        stop_reason=message.stop_reason or "end_turn",
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


def register_if_configured() -> bool:
    """Register the backend for the extract task when a credential is available.

    Absence is not an error: Phase 1 runs the deterministic floor with no model at all,
    and the seam already fails loudly if something asks for one (§9.2).
    """
    from . import model as model_seam

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        model_seam.register("extract", AnthropicBackend(task="extract"))
    except RuntimeError:
        return False
    return True
