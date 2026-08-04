"""Model seam (PRD §13.4): `complete(prompt, task) -> text`, config maps task -> model.

`stop_reason` and token counts are logged on every call. Truncation is release-blocking,
not diagnostic — the failure is silent and fluent (§12).

Phase 1 is the deterministic floor and makes no model calls. The seam exists now so that
adding one is a config change, and so the safety gate has somewhere to live.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


class ModelNotConfigured(RuntimeError):
    """Raised when a task asks for a model and none is configured for it."""


class IllegibleInputRefused(RuntimeError):
    """Safety invariant, not a flag: illegible units never reach the model (§9).

    Given noise a model produces fluent French clinical bullets that appear nowhere in
    the source.
    """


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int

    @property
    def truncated(self) -> bool:
        """Detected via `stop_reason` and, upstream, a parse failure (§12)."""
        return self.stop_reason not in ("end_turn", "stop", "stop_sequence")


class ModelBackend(Protocol):
    name: str

    def complete(self, prompt: str, *, max_tokens: int) -> ModelResponse: ...


class UnconfiguredBackend:
    """The Phase 1 default. Fails loudly rather than returning plausible text."""

    name = "unconfigured"

    def complete(self, prompt: str, *, max_tokens: int) -> ModelResponse:
        raise ModelNotConfigured(
            "No model backend configured. Set ALIE_MODEL_<TASK> and register a backend."
        )


#: task -> backend. Populated from config; §9.2's `model.<task>` flag selects per task.
_BACKENDS: dict[str, ModelBackend] = {}
_DEFAULT: ModelBackend = UnconfiguredBackend()


def register(task: str, backend: ModelBackend) -> None:
    _BACKENDS[task] = backend


def backend_for(task: str) -> ModelBackend:
    return _BACKENDS.get(task, _DEFAULT)


def configured_tasks() -> dict[str, str]:
    return {task: b.name for task, b in _BACKENDS.items()}


def complete(
    prompt: str, task: str, *, legible: bool = True, max_tokens: int = 4096
) -> ModelResponse:
    """The only way into a model. The legibility gate is enforced here so no caller can
    route around it."""
    if not legible:
        raise IllegibleInputRefused(f"illegible input refused for task {task!r}")
    return backend_for(task).complete(prompt, max_tokens=max_tokens)


def task_model_config() -> dict[str, str]:
    """Reads `ALIE_MODEL_<TASK>` from the environment. Config never in code (§13.4)."""
    prefix = "ALIE_MODEL_"
    return {
        k[len(prefix) :].lower(): v for k, v in os.environ.items() if k.startswith(prefix)
    }
