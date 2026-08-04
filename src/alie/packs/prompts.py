"""Prompt registry (PRD §7).

Every prompt is an addressable, versioned object: `id`, regime, doc class, version,
template, variables, changelog. Resolution runs base rules -> pack -> class template ->
firm -> case override.

Editing creates a new version and never mutates an existing one; otherwise a tweak
silently changes output the firm already approved. Every extracted record stores the
prompt version *and* the model that produced it, which is what makes it possible to
re-run only the affected units after a prompt change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .loader import Pack

#: `extract_row_lines.v3.yaml`
FILENAME = re.compile(r"^(?P<id>[a-z0-9_]+)\.v(?P<version>\d+)\.yaml$")


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    regime: str
    doc_class: str
    model_task: str
    system: str
    user: str
    changelog: tuple[dict[str, Any], ...]

    @property
    def ref(self) -> str:
        """What gets stored on every record produced under this prompt (§7)."""
        return f"{self.id}@v{self.version}"

    def render(self, **variables: str) -> tuple[str, str]:
        """Return `(system, user)` with variables substituted.

        Missing variables are a programming error, not something to paper over with a
        blank: a prompt that renders `Date de la ligne : ` teaches the model that the
        field is optional.
        """
        try:
            return self.system, self.user.format(**variables)
        except KeyError as exc:
            raise KeyError(f"prompt {self.ref} needs variable {exc}") from exc


class PromptNotFound(LookupError):
    """No prompt registered for this task, and none can be invented."""


@lru_cache(maxsize=32)
def _registry(root: str) -> dict[str, list[Prompt]]:
    directory = Path(root) / "prompts"
    if not directory.is_dir():
        return {}
    out: dict[str, list[Prompt]] = {}
    for path in sorted(directory.glob("*.yaml")):
        match = FILENAME.match(path.name)
        if not match:
            continue
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        prompt = Prompt(
            id=spec.get("id", match.group("id")),
            version=int(spec.get("version", match.group("version"))),
            regime=spec.get("regime", ""),
            doc_class=spec.get("doc_class", "*"),
            model_task=spec.get("model_task", "extract"),
            system=spec.get("system", "").strip(),
            user=spec.get("user", "").strip(),
            changelog=tuple(spec.get("changelog", [])),
        )
        out.setdefault(prompt.id, []).append(prompt)
    for prompts in out.values():
        prompts.sort(key=lambda p: p.version)
    return out


def resolve(pack: Pack, prompt_id: str, *, doc_class: str, version: int | None = None) -> Prompt:
    """Pick the prompt for this class, newest published version unless one is pinned.

    A class-specific prompt outranks the `*` fallback, which is the class-template step of
    §7's resolution order. Firm and case overrides slot in above this and are not built.
    """
    candidates = _registry(str(pack.root)).get(prompt_id, [])
    if not candidates:
        raise PromptNotFound(f"no prompt {prompt_id!r} registered in pack {pack.id}")

    if version is not None:
        pinned = [p for p in candidates if p.version == version]
        if not pinned:
            raise PromptNotFound(f"{prompt_id} has no version {version}")
        candidates = pinned

    specific = [p for p in candidates if p.doc_class == doc_class]
    return (specific or [p for p in candidates if p.doc_class == "*"] or candidates)[-1]


def available(pack: Pack) -> dict[str, list[int]]:
    return {pid: [p.version for p in ps] for pid, ps in _registry(str(pack.root)).items()}
