"""TUI package — Textual app, widgets, screens, and shared helpers."""

from __future__ import annotations

# Use the explicit escape, NOT an embedded U+200B literal. The literal
# looks like an empty string in editors and diffs and can be silently
# lost by trim-trailing-whitespace passes; the escape survives any
# editor / linter intact. Exported so the App's copy_to_clipboard
# override can strip it before writing to the system clipboard.
ZWSP = "​"
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
            nxt = nxt.replace(pair, pair[0] + ZWSP + pair[1])
        if nxt == out:
            return out
        out = nxt
