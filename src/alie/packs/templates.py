"""Template registry (PRD §4.3).

Checkbox state is hard in general and trivial for us: detect the form id, look up a
registered field map, read the known field. The registry key is **form id + revision** —
CNESST 2064 carries a revision stamp (`2064 (2012-06)`) and layouts shift between
revisions. An unrecognised revision falls back to 4b; silently reading the wrong field is
worse than having no template.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .loader import Pack


@dataclass(frozen=True)
class Template:
    form: str
    revision: str
    doc_class: str
    fields: tuple[dict[str, Any], ...]
    tag: str

    @property
    def key(self) -> str:
        return f"{self.form}@{self.revision}"


class UnknownRevision(LookupError):
    """The form is registered but this revision is not. Falls back to 4b, never to
    another revision's field map."""


@lru_cache(maxsize=32)
def _registry(root: str) -> dict[str, Template]:
    directory = Path(root) / "templates"
    if not directory.is_dir():
        return {}
    out: dict[str, Template] = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        template = Template(
            form=str(spec["form"]),
            revision=str(spec["revision"]),
            doc_class=spec.get("doc_class", ""),
            fields=tuple(spec.get("fields", [])),
            tag=spec.get("tag", "INF-H"),
        )
        out[template.key] = template
    return out


def registry(pack: Pack) -> dict[str, Template]:
    return _registry(str(pack.root))


def known_forms(pack: Pack) -> set[str]:
    return {t.form for t in registry(pack).values()}


def lookup(pack: Pack, form: str | None, revision: str | None) -> Template | None:
    """Return the template, or None when there is nothing registered for this form.

    Raises `UnknownRevision` when the form *is* registered but under other revisions —
    that is a different situation from an unknown form, and the caller must fall back to
    4b rather than reach for a neighbouring revision's coordinates.
    """
    if not form:
        return None
    table = registry(pack)
    if revision and (t := table.get(f"{form}@{revision}")):
        return t
    if form in {t.form for t in table.values()}:
        available = sorted(t.revision for t in table.values() if t.form == form)
        raise UnknownRevision(
            f"form {form} is registered for revisions {available} but not "
            f"{revision or '(none printed)'}; falling back to the model path"
        )
    return None
