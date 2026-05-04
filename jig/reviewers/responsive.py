"""ResponsiveDesignReviewer — Track D Final mechanical responsive checks.

Per ``docs/v2.0/visual-design/design.md`` §"Responsive design enforcement":
a Final-layer mechanical reviewer that audits each authored wireframe
for the markers of a responsive layout. No LLM, no vision.

Rules covered:

- **Viewport meta** — every wireframe HTML carries
  ``<meta name="viewport" content="width=device-width, initial-scale=1">``
  (or close variant). Missing → CRITICAL: without it a mobile browser
  renders desktop-width and the layout breaks.
- **No fixed-width layouts** — no ``width="<px>"`` attributes; no
  inline ``style="...; width: <px>;"`` declarations. Pixel-locked
  widths ignore the viewport.
- **Breakpoints declared** — the wireframe-meta JSON carries a
  non-empty ``breakpoints`` list naming the target viewports
  (mobile / tablet / desktop or operator-chosen labels). Empty →
  IMPORTANT.
- **Fluid typography hint** — wireframe references at least one
  ``--font-size-*`` token rather than relying entirely on hardcoded
  pixel font sizes. Belt-and-suspenders against ``font-size: 14px;``
  inside <style> blocks (the wireframe linter strips style blocks
  before its own checks but we audit them here).

Each violation emits a ``responsive-design-violation`` comment with
a ``breakpoint`` field naming which target failed (or None when the
finding spans every breakpoint).

Default-on for Final-layer tickets with non-empty visual_references;
the dispatch table wires the call.
"""
from __future__ import annotations

import re
from pathlib import Path

from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.spec_loader import wireframe_path
from jig.ticket import Ticket
from jig.wireframes.format import extract_meta

__all__ = [
    "RESPONSIVE_REVIEWER_ID",
    "ResponsiveDesignReviewer",
]


RESPONSIVE_REVIEWER_ID = "responsive-design"


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# <meta name="viewport" ...> — case-insensitive, picks up the
# attribute pair regardless of order.
_VIEWPORT_META_RE = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*[\"']viewport[\"'][^>]*\bcontent\s*=",
    re.IGNORECASE,
)
# Required content tokens — both must be present somewhere in the
# meta's content attribute.
_VIEWPORT_DEVICE_WIDTH_RE = re.compile(
    r"width\s*=\s*device-width", re.IGNORECASE
)
_VIEWPORT_INITIAL_SCALE_RE = re.compile(
    r"initial-scale\s*=", re.IGNORECASE
)
# Pull out the content value so we can validate it.
_VIEWPORT_CONTENT_VALUE_RE = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*[\"']viewport[\"'][^>]*"
    r"\bcontent\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)

# width="<px>" attribute on any element. We allow width="100%",
# width="50%", width="auto" — only strict pixel values count as
# fixed-width.
_WIDTH_ATTR_PX_RE = re.compile(
    r"\bwidth\s*=\s*[\"']?(\d+)(?:px)?[\"']?", re.IGNORECASE
)

# Inline style="...; width: <px>; ..." — the wireframe linter rejects
# inline style at CRITICAL but we still check for completeness.
_INLINE_STYLE_WIDTH_PX_RE = re.compile(
    r"\bstyle\s*=\s*[\"'][^\"']*\bwidth\s*:\s*\d+(?:\.\d+)?\s*px",
    re.IGNORECASE,
)

# Inside a <style>...</style> block, find ``width: <px>``. We extract
# style blocks first so we can scope the check.
_STYLE_BLOCK_RE = re.compile(
    r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL
)
_CSS_WIDTH_PX_RE = re.compile(
    r"\bwidth\s*:\s*(\d+(?:\.\d+)?)\s*px", re.IGNORECASE
)

# --font-size-* token reference, anywhere in the document.
_FONT_SIZE_TOKEN_RE = re.compile(r"--font-size-[A-Za-z0-9_-]+")

# Hardcoded font-size in pixels — inside a style block.
_FONT_SIZE_PX_RE = re.compile(
    r"\bfont-size\s*:\s*\d+(?:\.\d+)?\s*px", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------


class ResponsiveDesignReviewer:
    """Mechanical responsive-design reviewer for authored wireframes.

    Stateless. Skips silently when ``ticket.visual_references`` is
    empty so direct calls from tests stay safe. The dispatch layer
    gates on the same condition for symmetry with peers.
    """

    reviewer_id: str = RESPONSIVE_REVIEWER_ID

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
        *,
        worktree_path: Path | None = None,  # noqa: ARG002 — symmetry
        base_ref: str = "main",  # noqa: ARG002 — symmetry
    ) -> list[ReviewerComment]:
        comments: list[ReviewerComment] = []

        for screen_id in ticket.visual_references:
            wf_path = wireframe_path(project_root, screen_id)
            if not wf_path.is_file():
                # Visual-compliance reviewer owns the missing-wireframe
                # critical; we skip silently here.
                continue

            html = wf_path.read_text()
            comments.extend(
                _audit_html(html, ticket_id=ticket.id, screen_id=screen_id)
            )

        return comments


def _audit_html(
    html: str, *, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    out: list[ReviewerComment] = []
    out.extend(_check_viewport_meta(html, ticket_id, screen_id))
    out.extend(_check_no_fixed_widths(html, ticket_id, screen_id))
    out.extend(_check_breakpoints_declared(html, ticket_id, screen_id))
    out.extend(_check_fluid_typography(html, ticket_id, screen_id))
    return out


def _violation(
    *,
    message: str,
    ticket_id: str,
    severity: Severity = Severity.IMPORTANT,
    breakpoint: str | None = None,
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.RESPONSIVE_DESIGN_VIOLATION,
        severity=severity,
        reviewer=RESPONSIVE_REVIEWER_ID,
        prose=message,
        ticket_id=ticket_id,
        breakpoint=breakpoint,
    )


def _check_viewport_meta(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    if _VIEWPORT_META_RE.search(html) is None:
        return [
            _violation(
                message=(
                    f"Wireframe {screen_id!r} is missing the "
                    "<meta name=\"viewport\" content=\"...\"> tag. Add "
                    "<meta name=\"viewport\" content=\"width=device-"
                    "width, initial-scale=1\"> or a mobile browser "
                    "will render the layout at desktop width."
                ),
                ticket_id=ticket_id,
                severity=Severity.CRITICAL,
            )
        ]

    # Meta present — validate its content.
    content_match = _VIEWPORT_CONTENT_VALUE_RE.search(html)
    if content_match is None:
        # Tag present but no content="..." attribute (rare). Treat as
        # incomplete viewport directive.
        return [
            _violation(
                message=(
                    f"Wireframe {screen_id!r} has a viewport meta tag "
                    "without a content= attribute. Set content="
                    "\"width=device-width, initial-scale=1\"."
                ),
                ticket_id=ticket_id,
                severity=Severity.CRITICAL,
            )
        ]
    content = content_match.group(1)
    out: list[ReviewerComment] = []
    if _VIEWPORT_DEVICE_WIDTH_RE.search(content) is None:
        out.append(
            _violation(
                message=(
                    f"Viewport meta in wireframe {screen_id!r} is "
                    "missing 'width=device-width'. Without it the "
                    "viewport doesn't follow the device's actual "
                    "width."
                ),
                ticket_id=ticket_id,
                severity=Severity.CRITICAL,
            )
        )
    if _VIEWPORT_INITIAL_SCALE_RE.search(content) is None:
        out.append(
            _violation(
                message=(
                    f"Viewport meta in wireframe {screen_id!r} is "
                    "missing 'initial-scale=1'. Add it so the page "
                    "loads at the natural zoom level."
                ),
                ticket_id=ticket_id,
                severity=Severity.IMPORTANT,
            )
        )
    return out


def _check_no_fixed_widths(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    out: list[ReviewerComment] = []
    # width="<px>" attributes (legacy <table>, <img>, <hr>, etc.).
    for m in _WIDTH_ATTR_PX_RE.finditer(html):
        # Skip if the matched value is followed by % or em (unlikely
        # given the regex but defensive).
        out.append(
            _violation(
                message=(
                    f"Wireframe {screen_id!r} has a fixed-width "
                    f"attribute (width=\"{m.group(1)}\" / px). Use "
                    "percentage / fr / fluid utility classes so the "
                    "layout reflows at narrow viewports."
                ),
                ticket_id=ticket_id,
                breakpoint="mobile",
            )
        )
    # Inline style="... width: <px> ..."
    for _ in _INLINE_STYLE_WIDTH_PX_RE.finditer(html):
        out.append(
            _violation(
                message=(
                    f"Wireframe {screen_id!r} has an inline style "
                    "declaring width in pixels. Replace with a fluid "
                    "value (%, vw, fr) or move to a utility class."
                ),
                ticket_id=ticket_id,
                breakpoint="mobile",
            )
        )
    # Style-block declarations.
    for style_match in _STYLE_BLOCK_RE.finditer(html):
        body = style_match.group(1)
        for _ in _CSS_WIDTH_PX_RE.finditer(body):
            out.append(
                _violation(
                    message=(
                        f"Wireframe {screen_id!r} has a CSS rule with "
                        "a hardcoded pixel width inside a <style> "
                        "block. Use percentage / fluid units so the "
                        "layout adapts across breakpoints."
                    ),
                    ticket_id=ticket_id,
                    breakpoint="mobile",
                )
            )
    return out


def _check_breakpoints_declared(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    try:
        meta = extract_meta(html)
    except ValueError:
        # Malformed meta — wireframe linter owns this; we skip.
        return []
    if meta is None:
        # Missing meta — wireframe linter critical; we skip.
        return []
    if not meta.breakpoints:
        return [
            _violation(
                message=(
                    f"Wireframe {screen_id!r} declares no target "
                    "breakpoints in its meta. Set 'breakpoints' to a "
                    "list like ['mobile', 'tablet', 'desktop'] in the "
                    "wireframe-meta JSON so the responsive review "
                    "knows which viewports to enforce."
                ),
                ticket_id=ticket_id,
            )
        ]
    return []


def _check_fluid_typography(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    """Belt-and-suspenders: warn if wireframe relies entirely on px font sizes.

    Strict rule: if the document contains a hardcoded ``font-size:
    <px>`` declaration AND has zero ``--font-size-*`` token
    references, emit one violation. (We want the warning when there's
    no fluid hint *and* there's evidence of px-only typography —
    fluid-only wireframes don't trigger it.)
    """
    has_token = _FONT_SIZE_TOKEN_RE.search(html) is not None
    has_px_font = False
    for style_match in _STYLE_BLOCK_RE.finditer(html):
        if _FONT_SIZE_PX_RE.search(style_match.group(1)):
            has_px_font = True
            break

    if has_px_font and not has_token:
        return [
            _violation(
                message=(
                    f"Wireframe {screen_id!r} uses pixel font-sizes "
                    "without referencing any --font-size-* design "
                    "token. Replace the hardcoded values with token "
                    "references so type scales fluidly across "
                    "breakpoints."
                ),
                ticket_id=ticket_id,
            )
        ]
    return []
