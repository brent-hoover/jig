"""Tests for ResponsiveDesignReviewer (Track D Final).

Covers each rule + happy-path pass + dispatch wiring +
WireframeMeta.breakpoints round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jig.reviewers.dispatch import (
    RESPONSIVE_REVIEWER_ID as DISPATCH_RESPONSIVE_ID,
    dispatch_for_cadence,
    select_reviewers_for_ticket,
)
from jig.reviewers.responsive import (
    RESPONSIVE_REVIEWER_ID,
    ResponsiveDesignReviewer,
)
from jig.spec_loader import save_wireframe
from jig.ticket import Ticket, WorkType
from jig.wireframes.format import (
    WireframeMeta,
    extract_meta,
    render_meta_comment,
)
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ticket(visual_references: list[str], layer: str = "final") -> Ticket:
    return Ticket(
        id="ui-ticket",
        work_type=WorkType.FEATURE,
        title="UI ticket",
        created_by="test",
        layer=layer,
        visual_references=visual_references,
        description=TICKET_AC_PLACEHOLDER,
    )


def _meta_json(
    screen_id: str = "signup",
    breakpoints: list[str] | None = None,
) -> str:
    bps = breakpoints if breakpoints is not None else ["mobile", "tablet", "desktop"]
    return (
        f'<!-- wireframe-meta: {{"screen_id": "{screen_id}", '
        f'"title": "T", "persona_targets": [], "journey_refs": [], '
        f'"breakpoints": {json.dumps(bps)}}} -->'
    )


def _clean_html(
    screen_id: str = "signup",
    breakpoints: list[str] | None = None,
) -> str:
    """Wireframe that passes every responsive check."""
    return f"""\
{_meta_json(screen_id, breakpoints)}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>T</title>
</head>
<body><main><h1>T</h1></main></body>
</html>
"""


# ---------------------------------------------------------------------------
# WireframeMeta.breakpoints round-trip
# ---------------------------------------------------------------------------


class TestWireframeMetaBreakpoints:
    def test_breakpoints_default_empty(self) -> None:
        m = WireframeMeta(screen_id="x", title="T")
        assert m.breakpoints == []

    def test_breakpoints_round_trip_through_render_extract(self) -> None:
        m = WireframeMeta(
            screen_id="x",
            title="T",
            breakpoints=["mobile", "desktop"],
        )
        comment = render_meta_comment(m)
        loaded = extract_meta(comment)
        assert loaded is not None
        assert loaded.breakpoints == ["mobile", "desktop"]

    def test_extract_legacy_meta_without_breakpoints_works(self) -> None:
        # Backwards compat: existing wireframes don't carry the field.
        legacy = (
            '<!-- wireframe-meta: {"screen_id": "x", "title": "T"} --><html></html>'
        )
        loaded = extract_meta(legacy)
        assert loaded is not None
        assert loaded.breakpoints == []


# ---------------------------------------------------------------------------
# Reviewer — per-rule behaviors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNoOp:
    async def test_empty_visual_references_returns_empty(self, tmp_path: Path) -> None:
        comments = await ResponsiveDesignReviewer().review(_ticket([]), tmp_path)
        assert comments == []

    async def test_missing_wireframe_skips_silently(self, tmp_path: Path) -> None:
        comments = await ResponsiveDesignReviewer().review(_ticket(["nope"]), tmp_path)
        assert comments == []


@pytest.mark.asyncio
class TestHappyPath:
    async def test_clean_wireframe_passes(self, tmp_path: Path) -> None:
        save_wireframe(tmp_path, "signup", _clean_html("signup"))
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert comments == [], (
            f"expected zero violations on the canonical clean wireframe; "
            f"got: {[c.prose for c in comments]}"
        )


@pytest.mark.asyncio
class TestViewportMeta:
    async def test_missing_viewport_meta_emits_critical(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any(
            c.severity == "critical" and "viewport" in c.prose.lower() for c in comments
        )

    async def test_viewport_missing_device_width_emits_critical(
        self, tmp_path: Path
    ) -> None:
        html = _clean_html().replace(
            "width=device-width, initial-scale=1", "initial-scale=1"
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any(
            c.severity == "critical" and "device-width" in c.prose for c in comments
        )

    async def test_viewport_missing_initial_scale_emits_important(
        self, tmp_path: Path
    ) -> None:
        html = _clean_html().replace(
            "width=device-width, initial-scale=1",
            "width=device-width",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any(
            c.severity == "important" and "initial-scale" in c.prose for c in comments
        )


@pytest.mark.asyncio
class TestNoFixedWidths:
    async def test_width_attribute_in_pixels_emits(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            "<main><h1>T</h1></main>",
            '<main><div width="500"><h1>T</h1></div></main>',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        violations = [c for c in comments if "fixed-width" in c.prose]
        assert violations
        assert any(c.breakpoint == "mobile" for c in violations)

    async def test_inline_style_width_px_emits(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            "<main><h1>T</h1></main>",
            '<main><div style="width: 600px"><h1>T</h1></div></main>',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any("inline style" in c.prose for c in comments)

    async def test_style_block_width_px_emits(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            "<title>T</title>",
            "<title>T</title><style>.x { width: 800px; }</style>",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any("<style> block" in c.prose for c in comments)

    async def test_percentage_width_passes(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            "<title>T</title>",
            "<title>T</title><style>.x { width: 100%; }</style>",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        # Only fixed-width-related violations would mention "fixed" or
        # "pixels"; the canonical clean wireframe should still be clean.
        assert not any(
            ("fixed-width" in c.prose or "width in pixels" in c.prose) for c in comments
        )


@pytest.mark.asyncio
class TestBreakpointsDeclared:
    async def test_empty_breakpoints_list_emits_violation(self, tmp_path: Path) -> None:
        html = _clean_html(breakpoints=[])
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any("breakpoints" in c.prose for c in comments)

    async def test_populated_breakpoints_passes(self, tmp_path: Path) -> None:
        save_wireframe(tmp_path, "signup", _clean_html(breakpoints=["mobile"]))
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert not any("breakpoints" in c.prose for c in comments)


@pytest.mark.asyncio
class TestFluidTypography:
    async def test_px_font_without_token_emits(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            "<title>T</title>",
            "<title>T</title><style>body { font-size: 14px; }</style>",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert any("--font-size" in c.prose for c in comments)

    async def test_px_font_with_token_passes(self, tmp_path: Path) -> None:
        # Document references --font-size-* tokens elsewhere — px usage
        # gets the benefit of the doubt (still lower than the WCAG bar).
        html = _clean_html().replace(
            "<title>T</title>",
            "<title>T</title><style>body { font-size: var(--font-size-base); }</style>",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert not any("--font-size" in c.prose for c in comments)

    async def test_no_font_size_at_all_passes(self, tmp_path: Path) -> None:
        save_wireframe(tmp_path, "signup", _clean_html())
        comments = await ResponsiveDesignReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        assert not any("--font-size" in c.prose for c in comments)


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------


class TestDispatchSelection:
    def test_responsive_added_when_visual_references_present(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(["signup"]))
        assert DISPATCH_RESPONSIVE_ID in ids

    def test_responsive_not_selected_for_non_ui_ticket(self) -> None:
        ids = select_reviewers_for_ticket(_ticket([]))
        assert DISPATCH_RESPONSIVE_ID not in ids


@pytest.mark.asyncio
class TestDispatchInvocation:
    async def test_dispatch_runs_responsive_at_per_commit(self, tmp_path: Path) -> None:
        save_wireframe(tmp_path, "signup", _clean_html("signup"))
        out = await dispatch_for_cadence(
            _ticket(["signup"], layer="final"), tmp_path, "per_commit"
        )
        assert DISPATCH_RESPONSIVE_ID in out
        assert out[DISPATCH_RESPONSIVE_ID] == []  # clean wireframe


def test_reviewer_id_constants_match() -> None:
    assert DISPATCH_RESPONSIVE_ID == RESPONSIVE_REVIEWER_ID
