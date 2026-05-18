"""AccessibilityReviewer — Track D Final WCAG AA mechanical checks.

Per ``docs/v2.0/visual-design/design.md`` §"Accessibility (WCAG AA)" and
``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation": a Final-layer
mechanical reviewer that audits each authored wireframe against a
deterministic subset of WCAG AA rules. No LLM, no vision — the parser
is regex-based for the same reasons the wireframe linter is (small
documents, substring-shaped rules, no DOM-walk needed for the checks
we ship).

Rules covered:

- **WCAG 1.1.1 Non-text Content** — every ``<img>`` carries a
  non-empty ``alt=`` attribute.
- **WCAG 1.3.1 Info and Relationships** — every form-control element
  (``<input>``, ``<select>``, ``<textarea>``) has either a matching
  ``<label for=>`` / parent ``<label>``, an ``aria-label``, or an
  ``aria-labelledby``.
- **WCAG 1.3.1 Heading Hierarchy** — heading levels descend
  monotonically (no h1 → h3 jump).
- **WCAG 2.4.4 Link/Button Purpose** — every ``<button>`` has either
  non-empty text content OR an ``aria-label``.
- **WCAG 1.4.3 Contrast (Minimum)** — for any inline color reference
  the linter doesn't already forbid (defense-in-depth — the linter
  rejects most), require ≥ 4.5:1 contrast ratio between foreground +
  background. Implemented as a regex sweep over hex pairs in close
  proximity (foreground / background usually co-occur in a single
  inline declaration).
- **WCAG 3.1.1 Language of Page** — ``<html lang=...>`` is present and
  non-empty.
- **WCAG 2.4.1 Bypass Blocks** — page either has a skip-to-content
  link OR a ``<main>`` landmark element so assistive tech can jump
  past navigation.

Each violation emits a structured ``accessibility-violation`` comment
with a ``wcag_rule_id`` field naming the WCAG criterion the operator
needs to satisfy. The reviewer is **default-on** for Final-layer
tickets when ``ticket.visual_references`` is non-empty; the dispatch
table wires the call.
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

__all__ = [
    "ACCESSIBILITY_REVIEWER_ID",
    "AccessibilityReviewer",
    "contrast_ratio",
]


ACCESSIBILITY_REVIEWER_ID = "accessibility"


# ---------------------------------------------------------------------------
# Regexes — keep them grouped + commented so the next maintainer sees the
# rule each one services.
# ---------------------------------------------------------------------------

# <img ...> opening tag (case-insensitive). We capture the attribute
# blob so a follow-up regex can pull out alt= without re-scanning.
_IMG_TAG_RE = re.compile(r"<img\b([^>]*)>", re.IGNORECASE)

# alt attribute. Three flavors: alt="...", alt='...', alt=bareword.
# The empty-string branch (``alt=""``) matters — that's a present-but-
# empty alt which WCAG explicitly allows for decorative imagery, so
# we accept it here. Operators chasing AAA can layer a stricter check.
_ALT_ATTR_RE = re.compile(
    r"\balt\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|(\S+))",
    re.IGNORECASE,
)

# Form-control elements per WCAG 1.3.1.
_INPUT_TAG_RE = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.IGNORECASE)
# id attribute extractor; form fields need it for label association.
_ID_ATTR_RE = re.compile(
    r"\bid\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|(\S+))",
    re.IGNORECASE,
)
# aria-label / aria-labelledby attribute presence. Matches both with
# a single regex because the suffix variation between the two
# attributes is just "ledby" — but we need a real alternation since
# they don't share a common stem after "aria-label".
_ARIA_LABEL_RE = re.compile(r"\baria-(?:label|labelledby)\s*=", re.IGNORECASE)
# Hidden / type=submit / type=button / type=hidden don't need a visible
# label by name — they get the attribute as the accessible label
# automatically. We allowlist these so the reviewer doesn't false-
# positive on a Post button.
_INPUT_TYPE_RE = re.compile(r"\btype\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
_LABEL_FREE_INPUT_TYPES: frozenset[str] = frozenset(
    {"hidden", "submit", "button", "reset", "image"}
)

# <label for="..."> mapping. We collect every for= value to test
# coverage of the input ids.
_LABEL_FOR_RE = re.compile(
    r"<label\b[^>]*\bfor\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|(\S+))",
    re.IGNORECASE,
)
# A field nested inside a <label> is also labeled by association.
_LABEL_BLOCK_RE = re.compile(r"<label\b[^>]*>.*?</label>", re.IGNORECASE | re.DOTALL)

# Heading hierarchy.
_HEADING_RE = re.compile(r"<(h[1-6])\b[^>]*>", re.IGNORECASE)

# Buttons (need text or aria-label).
_BUTTON_BLOCK_RE = re.compile(
    r"<button\b([^>]*)>(.*?)</button>", re.IGNORECASE | re.DOTALL
)

# <html lang=...>
_HTML_LANG_RE = re.compile(
    r"<html\b[^>]*\blang\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)

# Skip-link landmark. Two acceptable forms: an anchor with text
# matching skip-to-content patterns, or a <main> landmark anywhere.
_SKIP_LINK_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*[\"'](#[^\"']*)[\"'][^>]*>"
    r"\s*(?:skip|jump)\s+to\s+(?:main|content)",
    re.IGNORECASE,
)
_MAIN_TAG_RE = re.compile(r"<main\b[^>]*>", re.IGNORECASE)

# Inline color-pair regex — picks two hex colors close together (e.g.
# ``color:#fff;background:#aaa``). The wireframe linter normally
# rejects inline styles outright; this is belt-and-suspenders for
# wireframes that slipped through (operator escape-hatch) or that
# carry colors in <style> blocks the linter doesn't strip.
_HEX_COLOR_PAIR_RE = re.compile(
    r"#([0-9a-fA-F]{3,8})[^#<>\n]{0,80}?#([0-9a-fA-F]{3,8})"
)


# ---------------------------------------------------------------------------
# Contrast helper — pure function, exposed for unit testing.
# ---------------------------------------------------------------------------


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Compute the WCAG contrast ratio between two hex colors.

    Inputs accept ``#`` prefix or not, 3-digit shorthand or 6-digit
    full form. WCAG formula:

        L = 0.2126*R + 0.7152*G + 0.0722*B
        ratio = (L_lighter + 0.05) / (L_darker + 0.05)

    where each channel is sRGB-linearized first. Returns the ratio
    (always ≥ 1.0). Tests expect well-known pairs: black-on-white →
    21.0, white-on-white → 1.0.
    """
    fg = _parse_hex(fg_hex)
    bg = _parse_hex(bg_hex)
    l_fg = _relative_luminance(fg)
    l_bg = _relative_luminance(bg)
    lighter, darker = max(l_fg, l_bg), min(l_fg, l_bg)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_hex(hex_str: str) -> tuple[int, int, int]:
    s = hex_str.lstrip("#")
    # 4-digit / 8-digit forms include alpha — strip it; alpha doesn't
    # contribute to luminance. The linter regex captures up to 8 chars.
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) == 4:
        s = "".join(ch * 2 for ch in s[:3])
    if len(s) == 8:
        s = s[:6]
    if len(s) != 6:
        raise ValueError(f"unparseable hex color {hex_str!r}")
    return (
        int(s[0:2], 16),
        int(s[2:4], 16),
        int(s[4:6], 16),
    )


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def _channel(c: int) -> float:
        srgb = c / 255.0
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------


# WCAG AA minimum contrast for normal-size text. Large-text 3:1 is a
# Final v2.x refinement; for now we apply 4.5:1 universally — false-
# positives on heading text are easier to triage than missing genuine
# violations.
_WCAG_AA_CONTRAST_MIN: float = 4.5


class AccessibilityReviewer:
    """Mechanical WCAG AA reviewer for authored wireframes.

    Stateless. Skips silently when ``ticket.visual_references`` is
    empty so a direct call from a test stays safe. The dispatch
    layer also gates on the same condition for symmetry with the
    visual-compliance reviewer.
    """

    reviewer_id: str = ACCESSIBILITY_REVIEWER_ID

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
        *,
        worktree_path: Path | None = None,  # noqa: ARG002 — symmetry w/ peers
        base_ref: str = "main",  # noqa: ARG002 — symmetry w/ peers
    ) -> list[ReviewerComment]:
        comments: list[ReviewerComment] = []

        for screen_id in ticket.visual_references:
            wf_path = wireframe_path(project_root, screen_id)
            if not wf_path.is_file():
                # Visual-compliance reviewer already emits the missing-
                # wireframe critical; we skip silently here to avoid
                # double-reporting. The accessibility reviewer's job
                # starts at "wireframe present" — its complement is
                # the visual reviewer.
                continue

            html = wf_path.read_text()
            comments.extend(_audit_html(html, ticket_id=ticket.id, screen_id=screen_id))

        return comments


def _audit_html(html: str, *, ticket_id: str, screen_id: str) -> list[ReviewerComment]:
    """Run every WCAG AA check on one wireframe; return all violations."""
    violations: list[ReviewerComment] = []

    violations.extend(_check_alt_text(html, ticket_id, screen_id))
    violations.extend(_check_form_labels(html, ticket_id, screen_id))
    violations.extend(_check_heading_hierarchy(html, ticket_id, screen_id))
    violations.extend(_check_button_text(html, ticket_id, screen_id))
    violations.extend(_check_color_contrast(html, ticket_id, screen_id))
    violations.extend(_check_html_lang(html, ticket_id, screen_id))
    violations.extend(_check_skip_or_main(html, ticket_id, screen_id))

    return violations


def _violation(
    *,
    rule_id: str,
    message: str,
    ticket_id: str,
    severity: Severity = Severity.IMPORTANT,
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.ACCESSIBILITY_VIOLATION,
        severity=severity,
        reviewer=ACCESSIBILITY_REVIEWER_ID,
        prose=message,
        ticket_id=ticket_id,
        wcag_rule_id=rule_id,
    )


def _check_alt_text(html: str, ticket_id: str, screen_id: str) -> list[ReviewerComment]:
    out: list[ReviewerComment] = []
    for m in _IMG_TAG_RE.finditer(html):
        attrs = m.group(1)
        alt_match = _ALT_ATTR_RE.search(attrs)
        if alt_match is None:
            out.append(
                _violation(
                    rule_id="WCAG 1.1.1 Non-text Content",
                    message=(
                        f"<img> in wireframe {screen_id!r} is missing an "
                        'alt attribute. Add alt="..." describing the '
                        'image (or alt="" for purely decorative images).'
                    ),
                    ticket_id=ticket_id,
                )
            )
        # Present-but-empty alt is allowed (decorative).
    return out


def _check_form_labels(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    """Each input/select/textarea needs a label by id, aria-label, or wrapping label."""
    label_for_ids: set[str] = set()
    for m in _LABEL_FOR_RE.finditer(html):
        for_id = m.group(1) or m.group(2) or m.group(3)
        if for_id:
            label_for_ids.add(for_id)

    # Collect ranges of <label>...</label> blocks so we can detect a
    # field nested inside a label.
    label_ranges: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in _LABEL_BLOCK_RE.finditer(html)
    ]

    out: list[ReviewerComment] = []
    for m in _INPUT_TAG_RE.finditer(html):
        tag = m.group(1).lower()
        attrs = m.group(2)

        # Skip input types that don't require labels.
        if tag == "input":
            type_match = _INPUT_TYPE_RE.search(attrs)
            if type_match is not None:
                t = (type_match.group(1) or type_match.group(2) or "").lower()
                if t in _LABEL_FREE_INPUT_TYPES:
                    continue

        if _ARIA_LABEL_RE.search(attrs):
            continue

        id_match = _ID_ATTR_RE.search(attrs)
        field_id = ""
        if id_match is not None:
            field_id = id_match.group(1) or id_match.group(2) or id_match.group(3) or ""

        if field_id and field_id in label_for_ids:
            continue

        # Nested-in-label association.
        pos = m.start()
        if any(start <= pos <= end for (start, end) in label_ranges):
            continue

        out.append(
            _violation(
                rule_id="WCAG 1.3.1 Info and Relationships",
                message=(
                    f"<{tag}> in wireframe {screen_id!r} has no accessible "
                    'label. Add a <label for="<id>"> matching the '
                    "field's id, wrap the field in a <label>, or add "
                    "aria-label / aria-labelledby."
                ),
                ticket_id=ticket_id,
            )
        )
    return out


def _check_heading_hierarchy(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    levels: list[int] = []
    for m in _HEADING_RE.finditer(html):
        levels.append(int(m.group(1)[1]))

    out: list[ReviewerComment] = []
    if not levels:
        return out

    prev: int | None = None
    for level in levels:
        if prev is not None and level > prev + 1:
            out.append(
                _violation(
                    rule_id="WCAG 1.3.1 Heading Hierarchy",
                    message=(
                        f"Heading level skipped in wireframe {screen_id!r}: "
                        f"h{prev} jumps to h{level}. Insert the missing "
                        "intermediate level(s) so screen readers can build "
                        "an accurate document outline."
                    ),
                    ticket_id=ticket_id,
                )
            )
        prev = level
    return out


def _check_button_text(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    out: list[ReviewerComment] = []
    for m in _BUTTON_BLOCK_RE.finditer(html):
        attrs = m.group(1)
        body = m.group(2)
        if _ARIA_LABEL_RE.search(attrs):
            continue
        # Strip nested tags from the body so a button containing only
        # <svg></svg> (icon-only) doesn't pass on tag noise.
        text = re.sub(r"<[^>]+>", "", body).strip()
        if text:
            continue
        out.append(
            _violation(
                rule_id="WCAG 2.4.4 Link Purpose",
                message=(
                    f"<button> in wireframe {screen_id!r} has no "
                    "accessible name. Add visible text content or "
                    "aria-label so assistive tech can announce the "
                    "button's purpose."
                ),
                ticket_id=ticket_id,
            )
        )
    return out


def _check_color_contrast(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    """Belt-and-suspenders contrast check on inline color pairs.

    The wireframe linter rejects inline styles outright as a CRITICAL,
    so this check fires mainly on color pairs in <style> blocks
    (which the linter strips before its own checks). For any pair we
    *do* find, require ≥ 4.5:1.
    """
    out: list[ReviewerComment] = []
    for m in _HEX_COLOR_PAIR_RE.finditer(html):
        c1, c2 = f"#{m.group(1)}", f"#{m.group(2)}"
        try:
            ratio = contrast_ratio(c1, c2)
        except ValueError:
            continue
        if ratio < _WCAG_AA_CONTRAST_MIN:
            out.append(
                _violation(
                    rule_id="WCAG 1.4.3 Contrast (Minimum)",
                    message=(
                        f"Contrast ratio {ratio:.2f}:1 between {c1} and "
                        f"{c2} in wireframe {screen_id!r} is below WCAG "
                        f"AA's {_WCAG_AA_CONTRAST_MIN}:1 minimum. "
                        "Tighten the foreground/background pair or use "
                        "design-system tokens that satisfy the floor."
                    ),
                    ticket_id=ticket_id,
                )
            )
    return out


def _check_html_lang(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    out: list[ReviewerComment] = []
    if _HTML_TAG_RE.search(html) is None:
        # No <html> tag at all — visual reviewer / linter would catch
        # this for other reasons; we don't double-report.
        return out
    lang_match = _HTML_LANG_RE.search(html)
    if lang_match is None:
        out.append(
            _violation(
                rule_id="WCAG 3.1.1 Language of Page",
                message=(
                    f"<html> in wireframe {screen_id!r} is missing a "
                    'lang attribute. Add lang="en" (or the page\'s '
                    "primary language) so assistive tech can pronounce "
                    "content correctly."
                ),
                ticket_id=ticket_id,
            )
        )
        return out
    lang_value = lang_match.group(1) or lang_match.group(2) or ""
    if not lang_value.strip():
        out.append(
            _violation(
                rule_id="WCAG 3.1.1 Language of Page",
                message=(
                    f'<html lang=""> in wireframe {screen_id!r} is '
                    'empty. Add a real language tag (e.g. "en").'
                ),
                ticket_id=ticket_id,
            )
        )
    return out


def _check_skip_or_main(
    html: str, ticket_id: str, screen_id: str
) -> list[ReviewerComment]:
    if _SKIP_LINK_RE.search(html) is not None:
        return []
    if _MAIN_TAG_RE.search(html) is not None:
        return []
    return [
        _violation(
            rule_id="WCAG 2.4.1 Bypass Blocks",
            message=(
                f"Wireframe {screen_id!r} has no skip-to-content link "
                "and no <main> landmark. Add one so keyboard / screen-"
                "reader users can bypass the navigation block."
            ),
            ticket_id=ticket_id,
        )
    ]
