"""VD wireframes package — HTML wireframe format + linter + index generator.

Per ``docs/visual-design/design.md`` §"Wireframe format — HTML wireframes":
each screen is one HTML file under ``.jig/spec/wireframes/<screen-id>.html``
authored against the constrained vocabulary (utility CSS classes from
``wireframe.css``, no inline styles, no real colors, no scripts beyond
the Alpine include). The linter enforces the vocabulary; the index
generator produces a browser-viewable ``index.html`` over the per-screen
HTMLs.

Sub-modules:

- ``format`` — ``WireframeMeta`` schema + meta-comment extraction.
- ``linter`` — ``lint_wireframe(html)`` returning structured violations.
- ``wireframe_css`` — canonical ``wireframe.css`` content generator.
- ``index_generator`` — browser-viewable ``index.html`` over the
  per-screen HTMLs.
"""
from __future__ import annotations

from jig.wireframes.format import (
    WireframeMeta,
    extract_meta,
    render_meta_comment,
)
from jig.wireframes.linter import LintError, LintSeverity, lint_wireframe
from jig.wireframes.wireframe_css import WIREFRAME_CSS, generate_wireframe_css

__all__ = [
    "LintError",
    "LintSeverity",
    "WIREFRAME_CSS",
    "WireframeMeta",
    "extract_meta",
    "generate_wireframe_css",
    "lint_wireframe",
    "render_meta_comment",
]
