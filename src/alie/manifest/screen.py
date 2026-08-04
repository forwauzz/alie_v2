"""Regime screening (PRD §6.1, §15.1).

Regime is a property of the **unit**, not the case. The IVAC gold file contains CNESST
documents — the victim was a logger assaulted on his own logging property, and LATMP takes
precedence. Case-level regime would read those units with the wrong vocabulary and the
wrong impairment math.

The engine holds no regime knowledge. Each pack declares an `identity` block saying how its
own documents announce themselves, and the screener scores every unit against every
installed pack. Adding a regime is authoring a pack, not editing this file.

**Open decision §15.1 — where screening runs.** It needs the offence date, which comes from
the manifest; classification vocabulary depends on the regime, which comes from screening.
The PRD proposes a two-pass manifest. This implements the alternative it names beside it:
a **case-level default with a per-unit override**, because it is strictly cheaper and the
override is the part that carries the evidence. A unit whose own text names another regime
is re-tagged and flagged; a unit that says nothing keeps the case default rather than being
guessed at. If measurement later shows re-classification actually moves boundaries, the
second pass is the next step — and the flag's metric is what would show it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Block
from ..packs import Pack
from ..packs import available as available_packs
from ..packs import load as load_pack

#: A form serial is printed by the issuing body, not typed by a clinician. Nothing outvotes
#: it — the same reasoning the classifier already uses (§4.4).
SERIAL_SCORE = 1.0
STRONG_SCORE = 0.45
WEAK_SCORE = 0.12

#: Below this a unit has said nothing about its regime and keeps the case default. Above
#: it, and above the default's own score, it is re-tagged.
MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Screening:
    unit_id: str
    regime: str
    confidence: float
    #: What the unit actually said. The override is only useful with its evidence (§7.1).
    matched: tuple[str, ...]
    #: True when this differs from the case's primary pack — the flag's whole metric
    #: (§9.2: "units whose regime differs from the case default").
    differs: bool = False

    @property
    def is_default(self) -> bool:
        return not self.differs


def _identity(pack: Pack) -> dict:
    return pack.pack.get("identity", {})


def score(
    blocks: list[Block], pack: Pack, *, form_serial: str | None = None
) -> tuple[float, list[str]]:
    """How strongly this unit claims to belong to `pack`'s regime."""
    identity = _identity(pack)
    if not identity:
        return 0.0, []

    matched: list[str] = []
    total = 0.0

    if form_serial and form_serial in identity.get("serials", []):
        total += SERIAL_SCORE
        matched.append(f"serial:{form_serial}")

    text = "\n".join(b.text for b in blocks if b.is_body_text)
    for pattern in identity.get("strong", []):
        if re.search(pattern, text, re.IGNORECASE):
            total += STRONG_SCORE
            matched.append(pattern)
    for pattern in identity.get("weak", []):
        if re.search(pattern, text, re.IGNORECASE):
            total += WEAK_SCORE
            matched.append(pattern)

    return min(total, 1.0), matched


def screen(
    unit_id: str,
    blocks: list[Block],
    *,
    default_pack: str,
    form_serial: str | None = None,
) -> Screening:
    """Tag one unit with the regime its own text claims, or the case default.

    A unit that says nothing keeps the default. Guessing a regime from silence would read
    a CNESST report with SAAQ vocabulary on the strength of no evidence at all.
    """
    scores: dict[str, tuple[float, list[str]]] = {}
    for pack_id in available_packs():
        try:
            scores[pack_id] = score(blocks, load_pack(pack_id), form_serial=form_serial)
        except (FileNotFoundError, KeyError):
            continue

    default_score = scores.get(default_pack, (0.0, []))[0]
    best_id, (best_score, matched) = max(
        scores.items(), key=lambda kv: kv[1][0], default=(default_pack, (0.0, []))
    )

    # Re-tag only when another pack both clears the bar and beats the default outright. A
    # tie goes to the default: the case is the paralegal's own statement of what this file
    # is, and it takes real evidence to overrule her.
    if best_id != default_pack and best_score >= MIN_CONFIDENCE and best_score > default_score:
        return Screening(unit_id, best_id, best_score, tuple(matched), differs=True)

    return Screening(
        unit_id, default_pack, default_score, tuple(scores.get(default_pack, (0.0, []))[1])
    )
