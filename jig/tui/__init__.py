"""TUI package — Textual app, widgets, screens, and shared helpers."""

from __future__ import annotations

# Explicit escape — embedded ZWSP literals look like an empty string in
# diffs and editors and can be silently lost by a trim-trailing-whitespace
# pass.
_ZWSP = "\u200B"
_LIGATURE_PAIRS = ("--", "->", "=>", ">=", "<=", "!=", "==", "::")


def ligature_safe(text: str) -> str:
    """Break common font-ligature pairs in ``text`` with U+200B."""
    if not text:
        return text
    # str.replace is non-overlapping, so a run like "===" only breaks the
    # first "==" in one pass. Repeat until the string stabilizes so all
    # adjacent pairs render literally. Apply AFTER any width-based
    # truncation by callers — ZWSPs count toward len() but not visual width.
    out = text
    while True:
        nxt = out
        for pair in _LIGATURE_PAIRS:
            nxt = nxt.replace(pair, pair[0] + _ZWSP + pair[1])
        if nxt == out:
            return out
        out = nxt
