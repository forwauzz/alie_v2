"""Serialised access to pdfium.

pdfium's core is not thread-safe. The API serves requests while a background worker thread
drains the job queue, and a second worker may drain it too, so every document open goes
through the lock here. Without it the process takes an access violation rather than
raising — a crash, not an exception, which no stage-level error handling can catch.

Blobs are content-addressed and immutable, so per-path caches below are safe: the same
path always holds the same bytes.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pypdfium2 as pdfium

_LOCK = threading.RLock()

_page_sizes: dict[str, list[tuple[float, float]]] = {}
_char_counts: dict[str, list[int]] = {}
_page_text: dict[str, dict[int, str]] = {}


@contextmanager
def document(path: str) -> Iterator[pdfium.PdfDocument]:
    with _LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            yield pdf
        finally:
            pdf.close()


@contextmanager
def text_page(pdf: pdfium.PdfDocument, pdf_index: int) -> Iterator[tuple]:
    """Yield `(page, textpage)` and close both explicitly.

    Left to the garbage collector, these close on whichever thread happens to trigger a
    collection, which mutates pypdfium2's global child registry from outside the lock and
    raises `RuntimeError: Set changed size during iteration`. Closing here keeps every
    mutation on the thread that holds the lock.
    """
    page = pdf[pdf_index - 1]
    textpage = page.get_textpage()
    try:
        yield page, textpage
    finally:
        textpage.close()
        page.close()


def page_count(path: str) -> int:
    return len(page_sizes(path))


def page_sizes(path: str) -> list[tuple[float, float]]:
    with _LOCK:
        if path not in _page_sizes:
            with document(path) as pdf:
                sizes = []
                for i in range(len(pdf)):
                    page = pdf[i]
                    sizes.append(tuple(page.get_size()))
                    page.close()
                _page_sizes[path] = sizes
        return _page_sizes[path]


def char_counts(path: str) -> list[int]:
    """Characters the text layer yields per page, 0-indexed.

    Computed once per document rather than per page: routing every page of a 3000-page
    bundle would otherwise reopen the file twice per page.
    """
    with _LOCK:
        if path not in _char_counts:
            with document(path) as pdf:
                counts = []
                for i in range(len(pdf)):
                    with text_page(pdf, i + 1) as (_, textpage):
                        counts.append(textpage.count_chars())
                _char_counts[path] = counts
        return _char_counts[path]


def page_text(path: str, pdf_index: int) -> str:
    """The raw text layer for one page.

    Routing needs this, not just a character count: a page can be dense with characters
    that are not words, and that page belongs to OCR rather than the free tier.
    """
    with _LOCK:
        cached = _page_text.setdefault(path, {})
        if pdf_index not in cached:
            with document(path) as pdf, text_page(pdf, pdf_index) as (_, textpage):
                cached[pdf_index] = textpage.get_text_range()
        return cached[pdf_index]


def render_page(path: str, pdf_index: int, scale: float):
    """Render one page to a PIL image for the OCR tier.

    `scale` is relative to PDF user space at 72 dpi, so scale 3 is 216 dpi.
    """
    with _LOCK:
        with document(path) as pdf:
            page = pdf[pdf_index - 1]
            try:
                return page.render(scale=scale).to_pil()
            finally:
                page.close()


def char_count(path: str, pdf_index: int) -> int:
    counts = char_counts(path)
    return counts[pdf_index - 1] if 1 <= pdf_index <= len(counts) else 0


def clear_cache() -> None:
    with _LOCK:
        _page_sizes.clear()
        _char_counts.clear()
        _page_text.clear()
