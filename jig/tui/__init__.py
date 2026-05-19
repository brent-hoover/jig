"""TUI package — Textual app, widgets, screens, and shared helpers."""

from __future__ import annotations

_ZWSP = "​"
_LIGATURE_PAIRS = ("--", "->", "=>", ">=", "<=", "!=", "==", "::")


def ligature_safe(text: str) -> str:
    """Break common font-ligature pairs in ``text`` with a zero-width space.

    Many terminal fonts (FiraCode, JetBrains Mono, Cascadia) render
    sequences like ``--`` as a single long dash via contextual alternates.
    For free-text user content (ticket titles, capability descriptions)
    that visual transformation is wrong — operators read it as
    strikethrough or "this item is done".

    Inserting a zero-width space between the two characters prevents the
    font from matching the ligature pattern without changing the visible
    width of the string (the ZWSP is non-printing and zero-width).
    Apply at render time only; never to data persisted to disk or sent
    over the bus.
    """
    if not text:
        return text
    out = text
    for pair in _LIGATURE_PAIRS:
        out = out.replace(pair, pair[0] + _ZWSP + pair[1])
    return out
