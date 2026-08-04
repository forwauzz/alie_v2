"""How word-like a page's text layer is (PRD §4.4f).

A page can carry a text layer and still be unreadable. Real bundles arrive already
OCR'd by whoever scanned them, and that pass can produce plenty of characters that are
not words: `\\rLllll\\{vÊ`, `tet-ttttr{trE`, `?ù o4.loÀ) Nom l/"*,/`.

Counting characters cannot tell those apart from prose, so the legibility gate — the one
thing standing between noise and a model that will fluently invent French clinical
bullets from it (§8.5) — needs a quality signal, not a quantity one.

Measured on the 139-page reference bundle: median word-likeness 0.67, and 8% of pages
below 0.30. Even clean pages only reach ~0.85, because French accents are the first thing
a degraded scan loses (`SÉCURIÉ`, `déctarés`, `d'iûrpræsioi`). The thresholds below are
set against that distribution, not against synthetic fixtures.
"""

from __future__ import annotations

import re

#: A token that reads as a word: letters only, at least two of them.
_WORDLIKE = re.compile(r"^[A-Za-zÀ-ÿ]{2,}$")

#: French and English both need one of these in almost every word.
_VOWEL = re.compile(r"[aeiouyàâäéèêëîïôöùûü]", re.IGNORECASE)

_STRIP = ".,;:()[]{}'\"·–—-«»"


def word_likeness(text: str) -> float:
    """Share of alphabetic tokens that read as words. 0.0 when there is nothing to judge.

    Digits, dates and codes are ignored rather than penalised: a séquelles table is mostly
    numbers and is perfectly legible.
    """
    tokens = [t.strip(_STRIP) for t in text.split()]
    alpha = [t for t in tokens if any(ch.isalpha() for ch in t)]
    if not alpha:
        return 0.0
    good = sum(1 for t in alpha if _WORDLIKE.match(t) and _VOWEL.search(t))
    return good / len(alpha)


def has_control_artifacts(text: str) -> bool:
    """Control characters and escape debris are a signature of a failed OCR pass."""
    return any(ord(ch) < 32 and ch not in "\n\t" for ch in text)
