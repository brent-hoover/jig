"""Tests for VisualComplianceReviewer + dispatch wiring (Track D MVP)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.reviewers.dispatch import (
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    VISUAL_COMPLIANCE_REVIEWER_ID,
    dispatch_for_cadence,
    select_reviewers_for_ticket,
)
from jig.reviewers.visual_compliance import VisualComplianceReviewer
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


def _broken_wireframe(meta: WireframeMeta) -> str:
    """Wireframe with a critical-severity violation (inline style)."""
    return (
        f"{render_meta_comment(meta)}\n"
        '<html><body><div style="color:red"></div></body></html>'
    )


def _seed_worktree_with_diff(
    project_root: Path, ticket_id: str, body_text: str
) -> Path:
    """Create a worktree at the orchestrator-conventional path with a diff.

    Mirrors the pattern from tests/test_reviewers_contract_compliance.py
    so the visual_compliance reviewer's reference check has something
    to match against.
    """
    wt = project_root / ".jig" / "worktrees" / ticket_id
    wt.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=wt, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@jig"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "base"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", f"jig/{ticket_id}"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    (wt / "impl.py").write_text(body_text)
    subprocess.run(
        ["git", "add", "-A"], cwd=wt, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "impl"],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    return wt


def _ticket(
    *,
    visual_references: list[str],
    layer: str | None = "mvp",
    reviewer_set: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id="ui-ticket",
        work_type=WorkType.FEATURE,
        title="UI ticket",
        created_by="test",
        layer=layer,
        visual_references=visual_references,
        reviewer_set=reviewer_set or [],
    )


# ---------------------------------------------------------------------------
# Reviewer — per-comment behaviors
# ---------------------------------------------------------------------------


class TestNoOp:
    async def test_empty_visual_references_returns_empty(
        self, tmp_path: Path
    ) -> None:
        reviewer = VisualComplianceReviewer()
        ticket = _ticket(visual_references=[])
        comments = await reviewer.review(ticket, tmp_path)
        assert comments == []


class TestWireframeNotFound:
    async def test_missing_wireframe_emits_critical(
        self, tmp_path: Path
    ) -> None:
        reviewer = VisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        comments = await reviewer.review(ticket, tmp_path)
        assert len(comments) >= 1
        wnf = [
            c for c in comments if c.type == "wireframe-not-found"
        ]
        assert len(wnf) == 1
        assert wnf[0].severity == "critical"
        assert "signup" in wnf[0].prose


class TestWireframeLintFailed:
    async def test_lint_failure_emits_critical(self, tmp_path: Path) -> None:
        save_wireframe(
            tmp_path, "signup", _broken_wireframe(_meta("signup"))
        )
        # Diff mentions signup — so the reference check passes,
        # leaving only the lint failure.
        _seed_worktree_with_diff(
            tmp_path, "ui-ticket", "# implements signup screen\n"
        )

        reviewer = VisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        comments = await reviewer.review(ticket, tmp_path)
        codes = {c.type for c in comments}
        assert "wireframe-lint-failed" in codes


class TestWireframeNotReferenced:
    async def test_diff_without_screen_id_emits_important(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        _seed_worktree_with_diff(
            tmp_path, "ui-ticket", "# unrelated diff\n"
        )

        reviewer = VisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        comments = await reviewer.review(ticket, tmp_path)
        wnr = [c for c in comments if c.type == "wireframe-not-referenced"]
        assert len(wnr) == 1
        assert wnr[0].severity == "important"

    async def test_diff_with_screen_id_passes_reference_check(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        _seed_worktree_with_diff(
            tmp_path, "ui-ticket", "# implements the signup wireframe\n"
        )

        reviewer = VisualComplianceReviewer()
        ticket = _ticket(visual_references=["signup"])
        comments = await reviewer.review(ticket, tmp_path)
        # All checks pass — empty.
        assert comments == []


# ---------------------------------------------------------------------------
# Dispatch logic
# ---------------------------------------------------------------------------


class TestDispatchSelection:
    def test_visual_compliance_added_when_visual_references_present(self) -> None:
        ticket = _ticket(visual_references=["signup"], layer="mvp")
        ids = select_reviewers_for_ticket(ticket)
        assert VISUAL_COMPLIANCE_REVIEWER_ID in ids
        # The MVP/final defaults should still be selected.
        assert BONES_REVIEWER_ID in ids
        assert CROSS_CUTTING_REVIEWER_ID in ids
        assert SPEC_COMPLIANCE_REVIEWER_ID in ids

    def test_visual_compliance_not_selected_for_non_ui_ticket(self) -> None:
        ticket = _ticket(visual_references=[], layer="mvp")
        ids = select_reviewers_for_ticket(ticket)
        assert VISUAL_COMPLIANCE_REVIEWER_ID not in ids

    def test_visual_compliance_appended_to_planner_authored_set(self) -> None:
        # Planner authored a custom reviewer_set — visual-compliance is
        # still appended because the gating signal is the references list.
        ticket = _ticket(
            visual_references=["signup"],
            reviewer_set=[BONES_REVIEWER_ID],
        )
        ids = select_reviewers_for_ticket(ticket)
        assert VISUAL_COMPLIANCE_REVIEWER_ID in ids
        assert BONES_REVIEWER_ID in ids

    def test_visual_compliance_not_duplicated_when_planner_already_added(
        self,
    ) -> None:
        ticket = _ticket(
            visual_references=["signup"],
            reviewer_set=[
                BONES_REVIEWER_ID,
                VISUAL_COMPLIANCE_REVIEWER_ID,
            ],
        )
        ids = select_reviewers_for_ticket(ticket)
        assert ids.count(VISUAL_COMPLIANCE_REVIEWER_ID) == 1


@pytest.mark.asyncio
class TestDispatchInvocation:
    async def test_dispatch_runs_visual_compliance_at_per_commit(
        self, tmp_path: Path
    ) -> None:
        save_wireframe(
            tmp_path, "signup", _clean_wireframe(_meta("signup"))
        )
        _seed_worktree_with_diff(
            tmp_path, "ui-ticket", "# implements signup wireframe\n"
        )

        ticket = _ticket(visual_references=["signup"], layer="mvp")
        out = await dispatch_for_cadence(
            ticket, tmp_path, "per_commit"
        )
        assert VISUAL_COMPLIANCE_REVIEWER_ID in out
        # Comments empty (everything passed) but the reviewer was
        # invoked — its key is in the output map.
        assert out[VISUAL_COMPLIANCE_REVIEWER_ID] == []
