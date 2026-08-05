"""Context-dependent abbreviations (PRD §8.8).

`TDM` is *tomodensitométrie* in `TDM Rachis Lombaire` and *trouble dépressif majeur* in
`a déjà fait TDM dans le passé`. Expansion is a model judgement with a citation, never a
mechanical transform.

This module therefore **never expands anything**. It finds the occurrence, lists the
meanings the pack says are possible, ranks them by whatever context hints matched, and
stops. The output is a flag with a citation — "this token is ambiguous, here is the
sentence, here are the candidates" — which a paralegal resolves in one glance and a model
could resolve only by quoting the same sentence back.

The alternative, a lookup table, would silently turn a CT scan into a depressive disorder
somewhere in a 300-page file, and nothing downstream could detect it: the substituted text
would still be cited, still be grounded, still validate. That is precisely the class of
failure this project treats as unacceptable — confident, plausible and wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..packs import Pack


@dataclass(frozen=True)
class Meaning:
    text: str
    #: How many context hints for this meaning matched the surrounding sentence. Ranks the
    #: candidates; never eliminates one.
    matched: int = 0


@dataclass(frozen=True)
class Occurrence:
    abbrev: str
    block_id: str
    span: tuple[int, int]
    #: The sentence around it — what a reviewer needs to decide, and what a model would
    #: have to quote (§8.8).
    context: str
    meanings: tuple[Meaning, ...]
    tag: str = "GAP"

    @property
    def unresolved(self) -> bool:
        """No meaning is established. `TRP` is the PRD's own example."""
        return not self.meanings

    @property
    def ranked(self) -> tuple[Meaning, ...]:
        return tuple(sorted(self.meanings, key=lambda m: -m.matched))

    def render(self) -> str:
        """One line for the review panel. Never a substitution."""
        if self.unresolved:
            return f"{self.abbrev} — abréviation non résolue"
        options = " | ".join(m.text for m in self.ranked)
        return f"{self.abbrev} — sens à confirmer : {options}"


def _specs(pack: Pack) -> list[dict]:
    return pack.abbreviations.get("ambiguous", [])


def find(text: str, pack: Pack, *, block_id: str = "") -> list[Occurrence]:
    """Every ambiguous abbreviation in one block of text, with its context.

    Matched case-sensitively and on word boundaries: `MI` is an abbreviation, `mi` is the
    first half of `mi-juillet`, and a case-insensitive match would flag half the file.
    """
    out: list[Occurrence] = []
    for spec in _specs(pack):
        abbrev = spec["abbrev"]
        for match in re.finditer(rf"(?<![\w-]){re.escape(abbrev)}(?![\w-])", text):
            context = _sentence(text, match.start())
            out.append(
                Occurrence(
                    abbrev=abbrev,
                    block_id=block_id,
                    span=(match.start(), match.end()),
                    context=context,
                    meanings=tuple(
                        Meaning(m["text"], _hits(m.get("hints", []), context))
                        for m in spec.get("meanings", [])
                    ),
                    tag=spec.get("tag", "GAP"),
                )
            )
    return out


def _hits(hints: list[str], context: str) -> int:
    return sum(1 for h in hints if re.search(h, context, re.IGNORECASE))


def _sentence(text: str, at: int) -> str:
    start = max(text.rfind(".", 0, at), text.rfind("\n", 0, at)) + 1
    ends = [i for i in (text.find(".", at), text.find("\n", at)) if i != -1]
    end = min(ends) if ends else len(text)
    return text[start:end].strip()
