"""Tests for the Final-layer visual reviewer + injectable vision provider.

Covers:

- ``StubVisionProvider`` — call recording, canned-result return shape,
  empty default.
- ``FullVisualComplianceReviewer.review_full`` — the Final-layer
  composition: MVP checks first, then per-screen vision diff. Each
  ``VisualDifference`` kind maps to a comment; missing-screenshot
  emits its own IMPORTANT comment; missing-wireframe short-circuits the
  vision step (no point diffing against a missing reference).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.reviewers.visual_compliance_full import FullVisualComplianceReviewer
from jig.reviewers.vision_provider import (
    StubVisionProvider,
    VisionDiffResult,
    VisualDifference,
)
from jig.spec_loader import save_wireframe
from jig.ticket import Ticket, WorkType
from jig.wireframes.format import WireframeMeta, render_meta_comment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(screen_id: str, title: str = "Title") -> WireframeMeta:
    return WireframeMeta(
        screen_id=screen_id,
        title=title,
        persona_targets=["merchant"],
        journey_refs=["j-merchant-onboarding"],
    )


def _clean_wireframe(meta: WireframeMeta) -> str:
    return f"""\
{render_meta_comment(meta)}
<!doctype html>
<html><head>
<link rel="stylesheet" href="../wireframe.css">
<script src="https://cdn.jsdelivr.net/npm/alpinejs@3" defer></script>
</head><body class="min-h-screen">
<main class="p-6"><h1>{meta.title}</h1></main>
</body></html>
"""


def _ticket(visual_references: list[str]) -> Ticket:
    return Ticket(
        id="ui-ticket",
        work_type=WorkType.FEATURE,
        title="UI ticket",
        created_by="test",
        layer="final",
        visual_references=visual_references,
    )


def _png(tag: str = "shot") -> bytes:
    """Tiny PNG-ish blob — content doesn't matter, just bytes."""
    return f"PNG-{tag}".encode("utf-8")


# ---------------------------------------------------------------------------
# StubVisionProvider
# ---------------------------------------------------------------------------


class TestStubVisionProvider:
    @pytest.mark.asyncio
    async def test_default_result_is_empty(self) -> None:
        stub = StubVisionProvider()
        result = await stub.compare_images(b"a", b"b", "ctx")
        assert result.differences == []

    @pytest.mark.asyncio
    async def test_returns_canned_result(self) -> None:
        canned = VisionDiffResult(
            differences=[
                VisualDifference(
                    kind="layout-shift",
                    description="header drifted left",
                    severity="important",
                )
            ]
        )
        stub = StubVisionProvider(result=canned)
        result = await stub.compare_images(b"a", b"b", "ctx")
        assert len(result.differences) == 1
        assert result.differences[0].kind == "layout-shift"

    @pytest.mark.asyncio
    async def test_records_calls(self) -> None:
        stub = StubVisionProvider()
        await stub.compare_images(b"ref", b"cand", "ctx-string")
        assert len(stub.calls) == 1
        assert stub.calls[0]["context"] == "ctx-string"
        assert stub.calls[0]["reference_len"] == 3
        assert stub.calls[0]["candidate_len"] == 4


# ---------------------------------------------------------------------------
# FullVisualComplianceReviewer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFullVisualComplianceReviewerVision:
    """The Final-layer vision-diff path."""

    async def test_no_visual_references_returns_empty(
        self, tmp_path: Path
    ) -> None:
        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=[])
        stub = StubVisionProvider()
        screenshots = tmp_path / "screenshots"
        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        assert comments == []
        # No screens → no provider calls.
        assert stub.calls == []

    async def test_missing_wireframe_skips_vision_call(
        self, tmp_path: Path
    ) -> None:
        # Wireframe never authored → MVP emits CRITICAL
        # wireframe-not-found; vision step short-circuits.
        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        stub = StubVisionProvider()
        screenshots = tmp_path / "screenshots"
        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        # MVP critical comment surfaces.
        types = [c.type for c in comments]
        assert "wireframe-not-found" in types
        # Vision provider was not invoked.
        assert stub.calls == []

    async def test_missing_screenshot_emits_important(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        stub = StubVisionProvider()
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        # Screenshot missing → IMPORTANT screenshot-missing.
        miss = [c for c in comments if c.type == "screenshot-missing"]
        assert len(miss) == 1
        assert miss[0].severity == "important"
        # Vision provider was not invoked.
        assert stub.calls == []

    async def test_clean_diff_returns_no_vision_comments(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        (screenshots / "signup.png").write_bytes(_png("signup"))

        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        stub = StubVisionProvider()  # default: no differences

        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        vision_comments = [
            c for c in comments if c.type == "visual-vision-diff"
        ]
        assert vision_comments == []
        # Provider was invoked once with the right context.
        assert len(stub.calls) == 1
        ctx = stub.calls[0]["context"]
        assert "signup" in ctx
        assert "ui-ticket" in ctx

    @pytest.mark.parametrize(
        "kind,severity",
        [
            ("layout-shift", "important"),
            ("color-mismatch", "notable"),
            ("missing-component", "critical"),
            ("extra-component", "important"),
            ("spacing-deviation", "notable"),
        ],
    )
    async def test_each_visual_difference_kind_emits_comment(
        self, tmp_path: Path, kind: str, severity: str
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        (screenshots / "signup.png").write_bytes(_png("signup"))

        result = VisionDiffResult(
            differences=[
                VisualDifference(
                    kind=kind,  # type: ignore[arg-type]
                    description=f"{kind} description",
                    location_hint="header",
                    severity=severity,  # type: ignore[arg-type]
                )
            ]
        )
        stub = StubVisionProvider(result=result)
        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])

        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        vision_comments = [
            c for c in comments if c.type == "visual-vision-diff"
        ]
        assert len(vision_comments) == 1
        c = vision_comments[0]
        assert c.severity == severity
        assert kind in c.prose
        assert "header" in c.prose

    async def test_multiple_differences_yield_multiple_comments(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        (screenshots / "signup.png").write_bytes(_png("signup"))

        result = VisionDiffResult(
            differences=[
                VisualDifference(
                    kind="layout-shift",
                    description="header shifted",
                    severity="important",
                ),
                VisualDifference(
                    kind="color-mismatch",
                    description="brand color wrong",
                    severity="notable",
                ),
            ]
        )
        stub = StubVisionProvider(result=result)
        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])

        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        vision_comments = [
            c for c in comments if c.type == "visual-vision-diff"
        ]
        assert len(vision_comments) == 2

    async def test_one_screen_missing_screenshot_other_diffs(
        self, tmp_path: Path
    ) -> None:
        # Two screens: one has screenshot, one doesn't.
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        save_wireframe(
            tmp_path, "checkout", _clean_wireframe(_meta("checkout"))
        )
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        (screenshots / "signup.png").write_bytes(_png("signup"))
        # checkout.png deliberately missing.

        result = VisionDiffResult(
            differences=[
                VisualDifference(
                    kind="layout-shift",
                    description="header drift",
                    severity="important",
                )
            ]
        )
        stub = StubVisionProvider(result=result)
        reviewer = FullVisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup", "checkout"])

        comments = await reviewer.review_full(
            ticket, tmp_path, stub, screenshots
        )
        miss = [c for c in comments if c.type == "screenshot-missing"]
        assert len(miss) == 1
        assert "checkout" in miss[0].prose
        vision = [c for c in comments if c.type == "visual-vision-diff"]
        assert len(vision) == 1
        # Provider invoked exactly once (signup).
        assert len(stub.calls) == 1
