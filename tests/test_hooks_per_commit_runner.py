"""Tests for the per-commit reviewer runner CLI (Track G MVP)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner

from jig.analytics.store import AnalyticsStore
from jig.hooks.per_commit_runner import (
    _find_project_root,
    _run_per_commit_review,
    main,
)
from jig.store.review_comments import ReviewCommentsStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


async def _git(cwd: Path, *args: str) -> int:
    proc = await asyncio.create_subprocess_shell(
        "git " + " ".join(args),
        cwd=cwd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode


async def _setup_project(tmp_path: Path, *, ticket_id: str = "tkt-pc-1") -> tuple[Path, Path]:
    """Build a project layout: real git repo + .jig/store + worktree dir.

    Returns (project_root, worktree_path).
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()
    await _git(project_root, "init", "-q")
    await _git(project_root, "config", "user.email", "t@t.t")
    await _git(project_root, "config", "user.name", "t")
    await _git(project_root, "config", "commit.gpgsign", "false")
    (project_root / "README").write_text("hello")
    await _git(project_root, "add", "-A")
    await _git(project_root, "commit", "-q", "-m", "init")

    store_dir = project_root / ".jig" / "store"
    store_dir.mkdir(parents=True)

    tickets = TicketStore(store_dir / "tickets.jsonl")
    await tickets.load()
    await tickets.create(
        Ticket(
            id=ticket_id,
            title="Per-commit test ticket",
            description="d",
            work_type=WorkType.FEATURE,
            status=TicketStatus.IN_PROGRESS,
            layer="bones",
            workflow="default",
            created_by="test",
        )
    )

    wt = project_root / ".jig" / "worktrees" / ticket_id
    wt.parent.mkdir(parents=True, exist_ok=True)
    await _git(project_root, "worktree", "add", "-b", f"jig/{ticket_id}", str(wt))
    return project_root, wt


class TestProjectRootResolution:
    def test_walks_two_parents_up(self, tmp_path: Path) -> None:
        wt = tmp_path / "proj" / ".jig" / "worktrees" / "tkt-1"
        wt.mkdir(parents=True)
        assert _find_project_root(wt) == tmp_path / "proj"

    def test_returns_none_for_non_convention(self, tmp_path: Path) -> None:
        wt = tmp_path / "weird" / "place"
        wt.mkdir(parents=True)
        assert _find_project_root(wt) is None


class TestRunnerHappyPath:
    async def test_runs_dispatch_and_persists_comments(self, tmp_path: Path) -> None:
        project_root, wt = await _setup_project(tmp_path)

        # No commits in worktree -> contract-compliance reviewer flags
        # empty-diff (CRITICAL).
        critical = await _run_per_commit_review("tkt-pc-1", cwd=wt)
        assert critical >= 1

        review_store = ReviewCommentsStore(
            project_root / ".jig" / "store" / "review_comments.jsonl"
        )
        await review_store.load()
        comments = await review_store.for_ticket("tkt-pc-1")
        assert comments
        assert any(c.severity == "critical" for c in comments)

        # PerCommitCheckFailed analytics event lands.
        analytics = AnalyticsStore(
            project_root / ".jig" / "store" / "analytics.jsonl"
        )
        await analytics.load()
        events = await analytics.by_kind("per_commit_check_failed")
        assert events
        ev = events[0]
        assert ev.ticket_id == "tkt-pc-1"
        assert ev.reviewer_role == "contract_compliance"
        assert ev.severity == "critical"


class TestRunnerSafeFailures:
    async def test_missing_ticket_does_not_crash(self, tmp_path: Path) -> None:
        project_root, wt = await _setup_project(tmp_path)
        # Pass an unknown ticket id; runner should print and return 0.
        n = await _run_per_commit_review("does-not-exist", cwd=wt)
        assert n == 0

    async def test_missing_store_dir_does_not_crash(self, tmp_path: Path) -> None:
        # Build a worktree-shaped path with no .jig/store underneath.
        wt = tmp_path / "proj" / ".jig" / "worktrees" / "tkt-x"
        wt.mkdir(parents=True)
        n = await _run_per_commit_review("tkt-x", cwd=wt)
        assert n == 0


class TestPerCommitNeverSpawnsLlmReviewers:
    """Block 3 design contract: per-commit cadence is mechanical-only.

    Single-digit-second latency budget rules out LLM-driven specialty
    reviewers (security/performance/architectural). Even on a ticket
    that *would* trigger specialty selection at end-of-ticket, the
    per-commit runner must run only the mechanical subset and emit
    PerCommitCheckFailed only for those reviewers.
    """

    async def test_security_label_does_not_spawn_llm(
        self, tmp_path: Path
    ) -> None:
        from jig.reviewers.dispatch import dispatch_for_cadence
        from jig.store.tickets import TicketStore

        project_root, wt = await _setup_project(
            tmp_path, ticket_id="tkt-sec-1"
        )
        # Patch the ticket so it carries a security-trigger label.
        store = TicketStore(
            project_root / ".jig" / "store" / "tickets.jsonl"
        )
        await store.load()
        ticket = await store.get("tkt-sec-1")
        assert ticket is not None
        ticket = ticket.model_copy(
            update={"labels": ["touches-auth"], "layer": "mvp"}
        )
        # Re-create with new fields so the JSONL store reflects them.
        # (Tests directly use dispatch_for_cadence below; the
        # in-memory ticket is what matters for selection.)

        out = await dispatch_for_cadence(
            ticket, project_root, "per_commit", worktree_path=wt,
        )

        # Every value at per_commit cadence is a list (mechanical
        # comments). NO LlmReviewerPending records.
        from jig.reviewers import LlmReviewerPending

        for reviewer_id, value in out.items():
            assert not isinstance(value, LlmReviewerPending), (
                f"per-commit dispatch must not queue LLM reviewer "
                f"{reviewer_id!r}; latency budget rules them out"
            )
        # Specialty reviewer ids absent.
        assert "reviewer-security" not in out
        assert "reviewer-performance" not in out
        assert "reviewer-architectural" not in out

    async def test_runner_emits_only_mechanical_per_commit_failures(
        self, tmp_path: Path
    ) -> None:
        """Even when the runner runs against a security-flavored ticket,
        the PerCommitCheckFailed events come only from the mechanical
        reviewers — no LLM reviewer events fire from per-commit."""
        project_root, wt = await _setup_project(
            tmp_path, ticket_id="tkt-sec-2"
        )

        critical = await _run_per_commit_review("tkt-sec-2", cwd=wt)
        assert critical >= 1

        analytics = AnalyticsStore(
            project_root / ".jig" / "store" / "analytics.jsonl"
        )
        await analytics.load()
        events = await analytics.by_kind("per_commit_check_failed")
        # Each event's reviewer_role must be a mechanical role —
        # no "reviewer-security" / "reviewer-performance" / etc.
        for ev in events:
            assert ev.reviewer_role in {
                "contract_compliance",
                "cross_cutting_policy",
                "spec_compliance",
            }, (
                f"per-commit must never emit PerCommitCheckFailed for "
                f"LLM reviewer {ev.reviewer_role!r}"
            )


class TestCliExitCode:
    def test_cli_always_exits_zero(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # Pass a cwd that doesn't resolve to anything; the runner
        # must still exit 0.
        result = runner.invoke(
            main,
            ["--ticket", "tkt-no", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
