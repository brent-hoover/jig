"""Wireframe-HTML linter — enforces the constrained vocabulary.

Per ``docs/v2.0/visual-design/design.md`` §"What's prohibited (wireframe-HTML
linter rejects with a structured comment)":

- No inline ``style=`` attributes.
- No real color values (``#``, ``rgb(``, ``hsl(``) outside ``<style>``
  tags — only token references via CSS variables.
- No ``<script>`` tags (wireframes are static HTML; the design.md
  Alpine include is a single curated tag we treat as a discoverable
  exception, not a general script allowance).
- A leading ``<!-- wireframe-meta: ... -->`` comment must be present
  with a parseable ``screen_id`` so the linker can index by screen.

Mechanical, no LLM. Returns a list of structured ``LintError`` objects;
empty list means clean. Severity tiers mirror the reviewer comment
schema (``CRITICAL`` = block, ``IMPORTANT`` = should fix, ``NOTABLE`` =
advisory).

The linter intentionally uses regex rather than an HTML parser because
(1) wireframes are small (one screen each, hand-authored or short
agent-authored files), (2) the rules are about specific *substrings*
(``style=``, ``#abc``, ``<script``), not about DOM structure, and
(3) bringing in lxml / BeautifulSoup adds a dep for negligible
robustness gain on documents this constrained.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict

from jig.wireframes.format import extract_meta

__all__ = [
    "LintError",
    "LintErrorCode",
    "LintSeverity",
    "lint_wireframe",
]


class LintSeverity(str, Enum):
    """Three-tier severity matching the reviewer comment schema."""

    CRITICAL = "critical"
    IMPORTANT = "important"
    NOTABLE = "notable"


class LintErrorCode(str, Enum):
    """One enum entry per check the linter performs.

    Adding a new check requires (1) the enum entry, (2) the regex /
    branch in ``lint_wireframe``, (3) a test that produces it. The
    enum keeps the visual_compliance reviewer's comment-emission
    surface uniform — the same code can be cited in a reviewer comment
    so the operator sees consistent labels across linter and reviewer.
    """

    INLINE_STYLE = "inline-style"
    REAL_COLOR_VALUE = "real-color-value"
    DISALLOWED_SCRIPT_TAG = "disallowed-script-tag"
    META_MISSING = "meta-missing"
    META_INVALID = "meta-invalid"


class LintError(BaseModel):
    """One linter finding."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    code: LintErrorCode
    severity: LintSeverity
    message: str
    line: int | None = None


# Inline ``style=`` attribute. Picks ``style="..."`` and ``style='...'``;
# the more permissive ``style=foo`` (no quotes) is also caught because
# the ``=`` is the load-bearing token. Case-insensitive because some
# editors emit camelCase via JSX.
_INLINE_STYLE_RE = re.compile(r"\bstyle\s*=", re.IGNORECASE)

# Real color values outside a ``<style>`` block. Three flavors covered:
# 6/3-digit hex (``#fff``, ``#ffffff``), ``rgb(...)`` / ``rgba(...)``,
# and ``hsl(...)`` / ``hsla(...)``. The wireframe.css file itself is
# allowed to contain real colors (it defines the greyscale variables);
# this linter runs on per-screen ``.html`` files, not on the css.
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6,8})\b")
_RGB_COLOR_RE = re.compile(r"\brgba?\s*\(", re.IGNORECASE)
_HSL_COLOR_RE = re.compile(r"\bhsla?\s*\(", re.IGNORECASE)

# ``<script ...>`` opening tag. The Alpine.js CDN include in design.md
# is the canonical exception (``<script src="https://cdn.jsdelivr.net/
# npm/alpinejs@3" defer></script>``); we treat any ``src`` referencing
# alpinejs as the curated exception. Other script tags fail.
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_ALPINE_SCRIPT_RE = re.compile(r"alpinejs", re.IGNORECASE)

# A ``<style>...</style>`` block. We strip these from the haystack
# before running the color-value checks because the design's
# wireframe.css is allowed to contain real values, and an inline style
# block is the operator's escape hatch for one-off layouts when the
# utility class set genuinely doesn't cover a need. The block itself is
# rare; if its contents need linting, that's a Final scope addition.
_STYLE_BLOCK_RE = re.compile(
    r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL
)


def _line_of(html: str, offset: int) -> int:
    """1-based line number of the byte offset ``offset`` in ``html``."""
    return html[:offset].count("\n") + 1


def _strip_style_blocks(html: str) -> str:
    """Replace ``<style>...</style>`` blocks with whitespace.

    Whitespace (rather than empty) preserves line numbers for the
    remaining checks so a ``style=`` violation that follows a long
    style block reports its true line, not a shifted one. Newlines
    inside the style block survive because the ``re.DOTALL`` match
    consumes them too — we re-insert per-newline whitespace to avoid
    a one-character shift.
    """
    def _replace(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    return _STYLE_BLOCK_RE.sub(_replace, html)


def lint_wireframe(html: str) -> list[LintError]:
    """Lint a wireframe HTML string; return the list of violations.

    Empty list means clean. The check order matches the design's
    listing: meta block first (CRITICAL — without it the linker can't
    index the wireframe), then inline-style + color + script (the
    vocabulary rules). Severity reflects what the operator *must* fix
    versus what's advisory; the visual_compliance reviewer at MVP+ can
    map these to its own comment severities.
    """
    errors: list[LintError] = []

    # Meta-comment check. ``extract_meta`` raises ValueError when the
    # comment is present but malformed; otherwise returns the meta or
    # None. Both failure modes get distinct error codes so the agent
    # sees "you forgot the meta" vs "your meta JSON is broken".
    try:
        meta = extract_meta(html)
    except ValueError as e:
        errors.append(
            LintError(
                code=LintErrorCode.META_INVALID,
                severity=LintSeverity.CRITICAL,
                message=str(e),
            )
        )
        meta = None

    if meta is None and not any(
        e.code == LintErrorCode.META_INVALID.value for e in errors
    ):
        errors.append(
            LintError(
                code=LintErrorCode.META_MISSING,
                severity=LintSeverity.CRITICAL,
                message=(
                    "wireframe is missing the leading "
                    "<!-- wireframe-meta: {...} --> comment; the "
                    "linker can't index this wireframe without it"
                ),
            )
        )

    # Strip <style> blocks before the substring checks so legitimate
    # CSS (operator escape hatch) doesn't false-positive on color values.
    body = _strip_style_blocks(html)

    for m in _INLINE_STYLE_RE.finditer(body):
        errors.append(
            LintError(
                code=LintErrorCode.INLINE_STYLE,
                severity=LintSeverity.CRITICAL,
                message=(
                    "inline style= attribute found; use utility classes "
                    "from wireframe.css instead"
                ),
                line=_line_of(body, m.start()),
            )
        )

    for regex, label in (
        (_HEX_COLOR_RE, "hex color"),
        (_RGB_COLOR_RE, "rgb()/rgba() color"),
        (_HSL_COLOR_RE, "hsl()/hsla() color"),
    ):
        for m in regex.finditer(body):
            errors.append(
                LintError(
                    code=LintErrorCode.REAL_COLOR_VALUE,
                    severity=LintSeverity.IMPORTANT,
                    message=(
                        f"real {label} value {m.group(0)!r} found; use "
                        "var(--color-*) tokens from wireframe.css"
                    ),
                    line=_line_of(body, m.start()),
                )
            )

    for m in _SCRIPT_TAG_RE.finditer(body):
        # The single curated Alpine include is the only permitted
        # script tag per design.md. Anything else is a sketch wireframe
        # smuggling real interactivity in.
        tag = m.group(0)
        if _ALPINE_SCRIPT_RE.search(tag):
            continue
        errors.append(
            LintError(
                code=LintErrorCode.DISALLOWED_SCRIPT_TAG,
                severity=LintSeverity.CRITICAL,
                message=(
                    f"disallowed <script> tag {tag!r}; wireframes are "
                    "static HTML — only the Alpine.js CDN include is "
                    "permitted"
                ),
                line=_line_of(body, m.start()),
            )
        )

    return errors
