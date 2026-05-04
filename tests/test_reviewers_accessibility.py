"""Tests for AccessibilityReviewer (Track D Final, WCAG AA mechanical).

One test per WCAG check + happy-path pass + dispatch wiring + the
``contrast_ratio`` helper (well-known endpoints + bad-input).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.reviewers.accessibility import (
    ACCESSIBILITY_REVIEWER_ID,
    AccessibilityReviewer,
    contrast_ratio,
)
from jig.reviewers.dispatch import (
    ACCESSIBILITY_REVIEWER_ID as DISPATCH_A11Y_ID,
    VISUAL_COMPLIANCE_REVIEWER_ID,
    dispatch_for_cadence,
    select_reviewers_for_ticket,
)
from jig.spec_loader import save_wireframe
from jig.ticket import Ticket, WorkType


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
    )


def _meta(screen_id: str = "signup") -> str:
    return (
        f'<!-- wireframe-meta: {{"screen_id": "{screen_id}", '
        f'"title": "T", "persona_targets": [], "journey_refs": []}} -->'
    )


def _clean_html(screen_id: str = "signup") -> str:
    """Wireframe that passes every WCAG AA check."""
    return f"""\
{_meta(screen_id)}
<!doctype html>
<html lang="en">
<head><title>T</title></head>
<body>
  <a href="#main">Skip to content</a>
  <main>
    <h1>Title</h1>
    <h2>Sub</h2>
    <form>
      <label for="title">Title</label>
      <input type="text" id="title" name="title">
      <button type="submit">Post</button>
    </form>
    <img src="x.png" alt="An x">
  </main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# contrast_ratio helper
# ---------------------------------------------------------------------------


class TestContrastRatio:
    def test_black_on_white_is_21(self) -> None:
        assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0

    def test_white_on_white_is_1(self) -> None:
        assert round(contrast_ratio("#ffffff", "#ffffff"), 1) == 1.0

    def test_short_form_hex_works(self) -> None:
        assert round(contrast_ratio("#000", "#fff"), 1) == 21.0

    def test_lacks_hash_prefix_works(self) -> None:
        assert round(contrast_ratio("000000", "ffffff"), 1) == 21.0

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            contrast_ratio("not-a-color", "#fff")


# ---------------------------------------------------------------------------
# Reviewer — per-rule behaviors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNoOp:
    async def test_empty_visual_references_returns_empty(
        self, tmp_path: Path
    ) -> None:
        reviewer = AccessibilityReviewer()
        comments = await reviewer.review(_ticket([]), tmp_path)
        assert comments == []

    async def test_missing_wireframe_skips_silently(self, tmp_path: Path) -> None:
        # The visual-compliance reviewer owns the missing-wireframe
        # critical; the a11y reviewer skips so we don't double-report.
        reviewer = AccessibilityReviewer()
        comments = await reviewer.review(_ticket(["nope"]), tmp_path)
        assert comments == []


@pytest.mark.asyncio
class TestHappyPath:
    async def test_clean_wireframe_passes(self, tmp_path: Path) -> None:
        save_wireframe(tmp_path, "signup", _clean_html("signup"))
        reviewer = AccessibilityReviewer()
        comments = await reviewer.review(_ticket(["signup"]), tmp_path)
        assert comments == [], (
            "expected zero violations on the canonical clean wireframe; "
            f"got: {[c.prose for c in comments]}"
        )


@pytest.mark.asyncio
class TestAltText:
    async def test_img_without_alt_emits_violation(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<img src="x.png" alt="An x">', '<img src="x.png">'
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        a11y = [c for c in comments if c.type == "accessibility-violation"]
        rules = {c.wcag_rule_id for c in a11y}
        assert any("1.1.1" in (r or "") for r in rules)

    async def test_img_with_empty_alt_passes(self, tmp_path: Path) -> None:
        # Empty alt is the WCAG-blessed signal for decorative imagery.
        html = _clean_html().replace(
            '<img src="x.png" alt="An x">', '<img src="x.png" alt="">'
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any("1.1.1" in (r or "") for r in rules)


@pytest.mark.asyncio
class TestFormLabels:
    async def test_input_without_label_or_aria_emits_violation(
        self, tmp_path: Path
    ) -> None:
        html = _clean_html().replace(
            '<label for="title">Title</label>\n      '
            '<input type="text" id="title" name="title">',
            '<input type="text" id="title" name="title">',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("1.3.1" in (r or "") and "Info" in (r or "") for r in rules)

    async def test_input_with_aria_label_passes(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<label for="title">Title</label>\n      '
            '<input type="text" id="title" name="title">',
            '<input type="text" name="title" aria-label="Title">',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any(
            "1.3.1" in (r or "") and "Info" in (r or "") for r in rules
        )

    async def test_input_nested_in_label_passes(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<label for="title">Title</label>\n      '
            '<input type="text" id="title" name="title">',
            '<label>Title <input type="text" name="title"></label>',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any(
            "1.3.1" in (r or "") and "Info" in (r or "") for r in rules
        )

    async def test_hidden_input_does_not_need_label(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<label for="title">Title</label>\n      '
            '<input type="text" id="title" name="title">',
            '<input type="hidden" name="csrf">',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any(
            "1.3.1" in (r or "") and "Info" in (r or "") for r in rules
        )


@pytest.mark.asyncio
class TestHeadingHierarchy:
    async def test_h1_to_h3_skip_emits_violation(self, tmp_path: Path) -> None:
        html = _clean_html().replace("<h2>Sub</h2>", "<h3>Sub</h3>")
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any(
            "1.3.1" in (r or "") and "Heading" in (r or "") for r in rules
        )

    async def test_no_skip_passes(self, tmp_path: Path) -> None:
        save_wireframe(tmp_path, "signup", _clean_html())
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any(
            "1.3.1" in (r or "") and "Heading" in (r or "") for r in rules
        )


@pytest.mark.asyncio
class TestButtonText:
    async def test_empty_button_emits_violation(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<button type="submit">Post</button>',
            '<button type="submit"></button>',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("2.4.4" in (r or "") for r in rules)

    async def test_button_with_aria_label_passes(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<button type="submit">Post</button>',
            '<button type="submit" aria-label="Post"><svg></svg></button>',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any("2.4.4" in (r or "") for r in rules)

    async def test_icon_only_button_without_aria_emits(
        self, tmp_path: Path
    ) -> None:
        html = _clean_html().replace(
            '<button type="submit">Post</button>',
            '<button type="submit"><svg></svg></button>',
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("2.4.4" in (r or "") for r in rules)


@pytest.mark.asyncio
class TestColorContrast:
    async def test_low_contrast_pair_in_style_block_emits(
        self, tmp_path: Path
    ) -> None:
        # Linter strips <style> blocks before its own checks, so this
        # is exactly the surface a11y reviewer needs to cover.
        html = _clean_html().replace(
            "<head><title>T</title></head>",
            "<head><title>T</title>"
            "<style>.x { color: #aaa; background: #bbb; }</style></head>",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("1.4.3" in (r or "") for r in rules)

    async def test_high_contrast_pair_passes(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            "<head><title>T</title></head>",
            "<head><title>T</title>"
            "<style>.x { color: #000; background: #fff; }</style></head>",
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any("1.4.3" in (r or "") for r in rules)


@pytest.mark.asyncio
class TestHtmlLang:
    async def test_missing_lang_emits_violation(self, tmp_path: Path) -> None:
        html = _clean_html().replace('<html lang="en">', "<html>")
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("3.1.1" in (r or "") for r in rules)

    async def test_empty_lang_emits_violation(self, tmp_path: Path) -> None:
        html = _clean_html().replace('<html lang="en">', '<html lang="">')
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("3.1.1" in (r or "") for r in rules)


@pytest.mark.asyncio
class TestSkipOrMain:
    async def test_no_main_no_skip_emits_violation(self, tmp_path: Path) -> None:
        # Drop both the skip link and the main landmark.
        html = (
            _clean_html()
            .replace('<a href="#main">Skip to content</a>', "")
            .replace("<main>", "<div>")
            .replace("</main>", "</div>")
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = {c.wcag_rule_id for c in comments}
        assert any("2.4.1" in (r or "") for r in rules)

    async def test_main_landmark_alone_passes(self, tmp_path: Path) -> None:
        html = _clean_html().replace(
            '<a href="#main">Skip to content</a>', ""
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any("2.4.1" in (r or "") for r in rules)

    async def test_skip_link_alone_passes(self, tmp_path: Path) -> None:
        html = (
            _clean_html()
            .replace("<main>", "<div>")
            .replace("</main>", "</div>")
        )
        save_wireframe(tmp_path, "signup", html)
        comments = await AccessibilityReviewer().review(
            _ticket(["signup"]), tmp_path
        )
        rules = [c.wcag_rule_id for c in comments]
        assert not any("2.4.1" in (r or "") for r in rules)


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------


class TestDispatchSelection:
    def test_accessibility_added_when_visual_references_present(self) -> None:
        ticket = _ticket(["signup"])
        ids = select_reviewers_for_ticket(ticket)
        assert DISPATCH_A11Y_ID in ids

    def test_accessibility_not_selected_for_non_ui_ticket(self) -> None:
        ticket = _ticket([])
        ids = select_reviewers_for_ticket(ticket)
        assert DISPATCH_A11Y_ID not in ids


@pytest.mark.asyncio
class TestDispatchInvocation:
    async def test_dispatch_runs_accessibility_at_per_commit(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(tmp_path, "signup", _clean_html("signup"))

        ticket = _ticket(["signup"], layer="final")
        out = await dispatch_for_cadence(ticket, tmp_path, "per_commit")
        assert DISPATCH_A11Y_ID in out
        assert out[DISPATCH_A11Y_ID] == []  # clean wireframe
        # Visual-compliance is also in the output (its own gating).
        assert VISUAL_COMPLIANCE_REVIEWER_ID in out

    async def test_dispatch_runs_accessibility_at_end_of_ticket(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(tmp_path, "signup", _clean_html("signup"))

        ticket = _ticket(["signup"], layer="final")
        out = await dispatch_for_cadence(ticket, tmp_path, "end_of_ticket")
        assert DISPATCH_A11Y_ID in out


def test_reviewer_id_constants_match() -> None:
    """Sanity: the dispatch constant matches the reviewer module's id."""
    assert DISPATCH_A11Y_ID == ACCESSIBILITY_REVIEWER_ID
