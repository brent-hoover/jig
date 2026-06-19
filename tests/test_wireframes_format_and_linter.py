"""Tests for the wireframe format / linter / wireframe.css generator (Track D MVP)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.schemas.design_system import (
    DEFAULT_DESIGN_SYSTEM,
    DesignSystem,
    DesignToken,
    Tokens,
)
from jig.spec_loader import (
    load_wireframe,
    save_wireframe,
    wireframe_css_path,
    wireframe_notes_path,
    wireframe_path,
    wireframes_dir,
)
from jig.wireframes.format import (
    WireframeMeta,
    extract_meta,
    render_meta_comment,
)
from jig.wireframes.linter import (
    LintErrorCode,
    LintSeverity,
    lint_wireframe,
)
from jig.wireframes.wireframe_css import WIREFRAME_CSS, generate_wireframe_css


def _meta_comment(screen_id: str = "post-a-job", title: str = "Post a job") -> str:
    return render_meta_comment(
        WireframeMeta(
            screen_id=screen_id,
            title=title,
            persona_targets=["merchant"],
            journey_refs=["j-merchant-onboarding"],
        )
    )


def _clean_wireframe() -> str:
    return f"""\
{_meta_comment()}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Wireframe — Post a job</title>
  <link rel="stylesheet" href="../wireframe.css">
  <script src="https://cdn.jsdelivr.net/npm/alpinejs@3" defer></script>
</head>
<body class="min-h-screen flex flex-col">
  <header class="border-b p-4"><small>Header</small></header>
  <main class="container mx-auto max-w-2xl p-6 flex-1">
    <h1 class="text-2xl font-semibold mb-4">Post a job</h1>
  </main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


class TestWireframeMetaExtraction:
    def test_extract_meta_from_well_formed_wireframe(self) -> None:
        meta = extract_meta(_clean_wireframe())
        assert meta is not None
        assert meta.screen_id == "post-a-job"
        assert meta.title == "Post a job"
        assert meta.persona_targets == ["merchant"]

    def test_extract_meta_returns_none_when_absent(self) -> None:
        assert extract_meta("<html><body></body></html>") is None

    def test_extract_meta_raises_on_malformed_json(self) -> None:
        bad = "<!-- wireframe-meta: {not valid json} -->\n<html></html>"
        with pytest.raises(ValueError):
            extract_meta(bad)

    def test_render_meta_round_trip(self) -> None:
        meta = WireframeMeta(
            screen_id="signup",
            title="Sign up",
            persona_targets=["merchant"],
            journey_refs=["j-merchant-onboarding"],
            notes="happy path only",
        )
        rendered = render_meta_comment(meta)
        rebuilt = extract_meta(rendered + "\n<html></html>")
        assert rebuilt == meta


# ---------------------------------------------------------------------------
# Linter
# ---------------------------------------------------------------------------


class TestLinterCleanCase:
    def test_clean_wireframe_returns_no_errors(self) -> None:
        errors = lint_wireframe(_clean_wireframe())
        assert errors == []


class TestLinterMetaChecks:
    def test_missing_meta_is_critical(self) -> None:
        html = "<html><body>no meta here</body></html>"
        errors = lint_wireframe(html)
        codes = {e.code for e in errors}
        assert LintErrorCode.META_MISSING.value in codes
        assert any(
            e.severity == LintSeverity.CRITICAL.value
            for e in errors
            if e.code == LintErrorCode.META_MISSING.value
        )

    def test_invalid_meta_is_critical(self) -> None:
        html = "<!-- wireframe-meta: {oops not json} -->\n<html></html>"
        errors = lint_wireframe(html)
        codes = {e.code for e in errors}
        assert LintErrorCode.META_INVALID.value in codes


class TestLinterInlineStyles:
    def test_inline_style_attribute_is_critical(self) -> None:
        html = (
            f"{_meta_comment()}\n<html><body>"
            '<div style="color: red"></div>'
            "</body></html>"
        )
        errors = lint_wireframe(html)
        assert any(e.code == LintErrorCode.INLINE_STYLE.value for e in errors)

    def test_camelcase_style_also_caught(self) -> None:
        # Defensive: even unusual casings of `style=` are flagged.
        html = (
            f"{_meta_comment()}\n<html><body><div Style=color:red></div></body></html>"
        )
        errors = lint_wireframe(html)
        assert any(e.code == LintErrorCode.INLINE_STYLE.value for e in errors)


class TestLinterColorChecks:
    def test_hex_color_outside_style_is_important(self) -> None:
        html = f"{_meta_comment()}\n<html><body class='bg-#ffffff'></body></html>"
        errors = lint_wireframe(html)
        assert any(e.code == LintErrorCode.REAL_COLOR_VALUE.value for e in errors)

    def test_rgb_color_outside_style_is_important(self) -> None:
        html = (
            f"{_meta_comment()}\n<html><body data-tone='rgba(0,0,0,0.5)'></body></html>"
        )
        errors = lint_wireframe(html)
        assert any(e.code == LintErrorCode.REAL_COLOR_VALUE.value for e in errors)

    def test_hsl_color_outside_style_is_important(self) -> None:
        html = f"{_meta_comment()}\n<html><body data-tone='hsl(120, 50%, 50%)'></body></html>"
        errors = lint_wireframe(html)
        assert any(e.code == LintErrorCode.REAL_COLOR_VALUE.value for e in errors)

    def test_color_inside_style_block_is_allowed(self) -> None:
        # The style block is the operator's escape hatch for one-offs;
        # we don't false-positive on its contents.
        html = (
            f"{_meta_comment()}\n<html>"
            "<head><style>:root { --x: #fff; }</style></head>"
            "<body></body></html>"
        )
        errors = lint_wireframe(html)
        assert not any(e.code == LintErrorCode.REAL_COLOR_VALUE.value for e in errors)


class TestLinterScriptChecks:
    def test_alpine_cdn_is_allowed(self) -> None:
        # Alpine include is the canonical exception per design.md.
        html = (
            f"{_meta_comment()}\n"
            "<html><head>"
            '<script src="https://cdn.jsdelivr.net/npm/alpinejs@3" defer></script>'
            "</head><body></body></html>"
        )
        errors = lint_wireframe(html)
        assert not any(
            e.code == LintErrorCode.DISALLOWED_SCRIPT_TAG.value for e in errors
        )

    def test_arbitrary_script_tag_is_critical(self) -> None:
        html = (
            f"{_meta_comment()}\n<html><body>"
            '<script>alert("nope")</script>'
            "</body></html>"
        )
        errors = lint_wireframe(html)
        assert any(e.code == LintErrorCode.DISALLOWED_SCRIPT_TAG.value for e in errors)


# ---------------------------------------------------------------------------
# wireframe.css generator
# ---------------------------------------------------------------------------


class TestWireframeCssGenerator:
    def test_constant_includes_root_block(self) -> None:
        assert ":root {" in WIREFRAME_CSS

    def test_constant_includes_utility_classes(self) -> None:
        # Sample of the most-used Tailwind-shape names.
        for cls in (".flex", ".gap-4", ".p-6", ".text-lg", ".border"):
            assert cls in WIREFRAME_CSS, f"missing utility class {cls!r}"

    def test_default_call_returns_baseline(self) -> None:
        assert generate_wireframe_css() == WIREFRAME_CSS

    def test_design_system_overrides_color_token(self) -> None:
        # Operator picks a real brand color — generator threads it
        # through into the :root variable.
        ds = DesignSystem(
            tokens=Tokens(
                source="operator_supplied",
                tokens=[
                    DesignToken(id="color-primary", kind="color", value="#0a66c2"),
                ],
            ),
            components=DEFAULT_DESIGN_SYSTEM.components,
            brand=DEFAULT_DESIGN_SYSTEM.brand,
        )
        css = generate_wireframe_css(ds)
        assert "--color-primary: #0a66c2" in css
        # The default value should be replaced, not duplicated.
        assert css.count("--color-primary:") == 1

    def test_extra_token_id_appended_to_root(self) -> None:
        ds = DesignSystem(
            tokens=Tokens(
                source="operator_supplied",
                tokens=[
                    DesignToken(id="color-success", kind="color", value="#27ae60"),
                ],
            ),
            components=DEFAULT_DESIGN_SYSTEM.components,
            brand=DEFAULT_DESIGN_SYSTEM.brand,
        )
        css = generate_wireframe_css(ds)
        assert "--color-success: #27ae60" in css


# ---------------------------------------------------------------------------
# Path helpers + load/save round-trip
# ---------------------------------------------------------------------------


class TestSpecLoaderWireframeHelpers:
    def test_path_helpers_match_layout(self, tmp_path: Path) -> None:
        assert wireframes_dir(tmp_path) == (tmp_path / ".jig" / "spec" / "wireframes")
        assert wireframe_path(tmp_path, "signup").name == "signup.html"
        assert wireframe_css_path(tmp_path).name == "wireframe.css"
        assert wireframe_notes_path(tmp_path, "signup").name == "signup.notes.md"

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        html = _clean_wireframe()
        save_wireframe(tmp_path, "post-a-job", html)
        loaded = load_wireframe(tmp_path, "post-a-job")
        assert loaded == html

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_wireframe(tmp_path, "nope")
