"""Tests for ``Orchestrator._route_blocked_phase`` — step 8 of
feature-work/review-routing/plan.md.

The helper replaces the hard-coded ``_find_fix_phase`` at the
orchestrator's blocked-phase call site. It fetches the latest cycle of
blocking review comments and routes them via ``_route_blocking_comments``
(step 7), then posts a thread Note naming the chosen phase + route reason.

The integration test (``TestReplay240db21fScenario``) is the key one —
it replays the failure pattern that motivated the whole review-routing
work: a reviewer flags a finding in a test file, and the router sends
it back to the test phase instead of dev.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.models import PhaseConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck, FindingAcksStore
from jig.store.review_comments import ReviewCommentsStore


def _save_project(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )


def _phase(name: str, role: str, writes: list[str] | None = None) -> PhaseConfig:
    return PhaseConfig(name=name, role=role, writes=writes or [])


def _review_phase(name: str, reviewers: list[str]) -> PhaseConfig:
    return PhaseConfig(name=name, role="review", reviewers=reviewers)


def _comment(
    *,
    file: str | None = None,
    severity: Severity = Severity.IMPORTANT,
    cycle: int = 0,
    reviewer: str = "reviewer-pattern-conformance",
    type_: str | None = None,
    prose: str = "f",
) -> ReviewerComment:
    # Choose a representative ``type`` per reviewer when the caller
    # doesn't override. Tests that pin routing/filtering shouldn't
    # carry a stale ``pattern-divergence`` on a test-adequacy
    # finding — the type field exists so downstream logic can
    # distinguish reviewer mandates.
    if type_ is None:
        type_ = (
            "test-adequacy"
            if reviewer == "reviewer-test-adequacy"
            else "pattern-divergence"
        )
    return ReviewerComment(
        type=ReviewerCommentType(type_),
        severity=severity,
        reviewer=reviewer,
        prose=prose,
        confidence=0.85,
        file=file,
        cycle=cycle,
    )


async def _seed_store(
    tmp_path: Path, ticket_id: str, comments: list[ReviewerComment]
) -> None:
    """Persist comments in the project's ReviewCommentsStore."""
    store_path = tmp_path / ".jig" / "store" / "review_comments.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = ReviewCommentsStore(store_path)
    await store.load()
    for c in comments:
        stamped = c.model_copy(update={"ticket_id": ticket_id})
        await store.append(stamped)


async def _seed_acks(tmp_path: Path, acks: list[FindingAck]) -> None:
    """Persist acks in the project's FindingAcksStore."""
    store_path = tmp_path / ".jig" / "store" / "finding_acks.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = FindingAcksStore(store_path)
    await store.load()
    for a in acks:
        await store.append(a)


# ---------- baseline behaviour ---------------------------------------------


class TestRouteBlockedPhase:
    @pytest.mark.asyncio
    async def test_falls_back_to_dev_when_no_comments(self, tmp_path: Path) -> None:
        """No reviewer comments — the check-failure path. Falls back
        to the most-recent dev phase (legacy ``_find_fix_phase`` rule)
        so the check-failure route-back continues to work."""
        _save_project(tmp_path)
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            workflow = WorkflowConfig(
                name="w",
                phases=[
                    _phase("test", "test", writes=["tests/**"]),
                    _phase("implement", "dev", writes=["src/**"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )
            result = await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=2,
                ticket_id="tb-empty",
                worktree=tmp_path,
            )
            # No comments → fall back to most-recent dev.
            assert result == 1

        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_comments_and_no_dev_phase(
        self, tmp_path: Path
    ) -> None:
        """No comments AND no dev phase to fall back on → None."""
        _save_project(tmp_path)
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            workflow = WorkflowConfig(
                name="docs-only",
                phases=[
                    _phase("draft", "spec", writes=["docs/**"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )
            result = await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=1,
                ticket_id="tb-no-dev",
                worktree=tmp_path,
            )
            assert result is None
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_filters_to_latest_cycle_only(self, tmp_path: Path) -> None:
        """Stale comments from an earlier cycle must not influence routing."""
        _save_project(tmp_path)
        # Seed before startup so the instance-scoped store picks them up.
        # Use test-adequacy as the reviewer for the test-file finding —
        # pattern-conformance's scope excludes tests/** post-feature, so
        # such a comment would be dropped as out-of-scope (correct
        # behaviour pinned by ``test_reviewer_scoping_e2e``); to exercise
        # cycle-filtering we need a scoping-valid combination.
        await _seed_store(
            tmp_path,
            "tb-cycle",
            [
                # Old cycle: a finding that would route to implement.
                _comment(file="src/foo.py", cycle=0),
                # Latest cycle: a finding that should route to test.
                _comment(
                    file="tests/test_x.py",
                    cycle=1,
                    reviewer="reviewer-test-adequacy",
                ),
            ],
        )
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            workflow = WorkflowConfig(
                name="w",
                phases=[
                    _phase("test", "test", writes=["tests/**"]),
                    _phase("implement", "dev", writes=["src/**"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )
            result = await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=2,
                ticket_id="tb-cycle",
                worktree=tmp_path,
            )
            assert result == 0  # test phase, from the latest cycle's finding

        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_notable_only_falls_back_to_dev(self, tmp_path: Path) -> None:
        """Notables route per-finding only when in scope for the issuing
        reviewer. This notable is from pattern-conformance on a tests/**
        file — out of its reads_glob — so it's dropped as hallucinated
        and routing falls back to most-recent dev (the legacy path)."""
        _save_project(tmp_path)
        await _seed_store(
            tmp_path,
            "tb-notable",
            [_comment(file="tests/test_x.py", severity=Severity.NOTABLE, cycle=0)],
        )
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            workflow = WorkflowConfig(
                name="w",
                phases=[
                    _phase("test", "test", writes=["tests/**"]),
                    _phase("implement", "dev", writes=["src/**"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )
            result = await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=2,
                ticket_id="tb-notable",
                worktree=tmp_path,
            )
            assert result == 1  # implement (dev), via fallback
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_posts_thread_note_with_route_reason(self, tmp_path: Path) -> None:
        """The chosen phase + route reason lands as a thread Note so
        operators can see why a given phase was selected for retry."""
        _save_project(tmp_path)
        # See note in ``test_filters_to_latest_cycle_only``: use
        # test-adequacy for findings on test files so the comment
        # survives the orchestrator's out-of-scope filter.
        await _seed_store(
            tmp_path,
            "tb-note",
            [
                _comment(
                    file="tests/test_x.py",
                    cycle=0,
                    reviewer="reviewer-test-adequacy",
                )
            ],
        )
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            workflow = WorkflowConfig(
                name="w",
                phases=[
                    _phase("test", "test", writes=["tests/**"]),
                    _phase("implement", "dev", writes=["src/**"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )
            await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=2,
                ticket_id="tb-note",
                worktree=tmp_path,
            )
            assert orch.threads is not None
            entries = await orch.threads.for_ticket("tb-note")
            route_notes = [
                e for e in entries if getattr(e, "event_type", None) == "fix_loop_route"
            ]
            assert len(route_notes) == 1
            content = route_notes[0].content
            assert "'review'" in content
            assert "'test'" in content
            assert "writes-glob" in content
        finally:
            await orch.shutdown()


# ---------- notable-only blocks (unacked-notable gate) ---------------------


def _review_tests_workflow() -> WorkflowConfig:
    """Default-workflow shape: review-tests precedes the first dev phase,
    so the legacy most-recent-dev fallback has nothing to walk back to."""
    return WorkflowConfig(
        name="default-like",
        phases=[
            _phase("test", "test", writes=["tests/**"]),
            _review_phase("review-tests", ["reviewer-test-adequacy"]),
            _phase("implement", "dev", writes=["src/**"]),
            _review_phase("review", ["reviewer-pattern-conformance"]),
        ],
    )


class TestReplay240db21fScenario:
    """Replay of the failure pattern that motivated the entire
    review-routing work (issue #42 / hn-cli ticket 240db21f).

    Pre-step-8 behaviour: reviewer flags a finding in ``tests/...``,
    fix-loop routes to ``implement`` (dev role), dev can't legally
    edit test files, thrashes for ``max_fix_cycles`` cycles, ticket
    fails.

    Post-step-8 behaviour: same finding, file-glob routing sends it
    back to the ``test`` phase. The test author can apply the fix.
    """

    @pytest.mark.asyncio
    async def test_test_file_finding_routes_to_test_phase_not_dev(
        self, tmp_path: Path
    ) -> None:
        _save_project(tmp_path)
        # The actual 240db21f finding: pattern-conformance flagged a
        # too-broad per-file-ignore in pyproject.toml that the dev
        # added as a workaround for an unsortable import block in a
        # test file. The reviewer's `file` is the test file
        # (tests/test_filter_flags.py) — and that's the file the dev
        # role cannot legally edit.
        #
        # Post-reviewer-scoping: pattern-conformance can no longer
        # be the reviewer that raises this finding (its reads_glob
        # excludes tests/**, so its findings on tests/ paths are
        # dropped by ``_filter_out_of_scope_comments`` as
        # hallucinations). The legitimate raiser of test-side
        # consistency issues is now ``reviewer-test-adequacy``,
        # whose mandate explicitly covers "duplicate helpers /
        # constants that should live in conftest" — that's the
        # same class of finding.
        await _seed_store(
            tmp_path,
            "tb-240db21f",
            [
                _comment(
                    file="tests/test_filter_flags.py",
                    severity=Severity.IMPORTANT,
                    cycle=0,
                    reviewer="reviewer-test-adequacy",
                    prose=(
                        "Tests should import VALID_TYPES from cli.py "
                        "rather than redefining it locally — the test "
                        "constant has a different type."
                    ),
                ),
            ],
        )
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            # Workflow mirrors the default's shape — spec/test/dev/review.
            workflow = WorkflowConfig(
                name="w",
                phases=[
                    _phase("spec", "spec", writes=["docs/spec/**"]),
                    _phase("test", "test", writes=["tests/**"]),
                    _phase("implement", "dev", writes=["src/**", "pyproject.toml"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )

            result = await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=3,  # review phase
                ticket_id="tb-240db21f",
                worktree=tmp_path,
            )

            # Pre-step-8: would have returned 2 (implement / dev). The
            # whole point of review-routing is for this to flip.
            assert result == 1, (
                f"Expected the test phase (idx=1), got phase idx={result}. "
                "The router should send a test-file finding back to the "
                "test author, not the dev who can't legally fix it."
            )
        finally:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_comment_written_after_startup_via_fresh_store_is_visible(
        self, tmp_path: Path
    ) -> None:
        """Production flow: ``reviewer_mcp.handle_reviewer_post_comment``
        constructs a fresh ``ReviewCommentsStore`` per call (it can't
        reach the orchestrator's instance), so writes go to disk but
        never touch the orchestrator's cached ``_docs``.

        ``_route_blocked_phase`` must therefore reload from disk before
        querying. This test seeds AFTER ``startup()`` using a separate
        store instance to prove the reload happens.
        """
        _save_project(tmp_path)
        orch = Orchestrator(project_path=tmp_path)
        await orch.startup()
        try:
            # Simulate the reviewer_mcp write path: fresh store, write
            # straight to disk, never touches orch.review_comments._docs.
            store_path = tmp_path / ".jig" / "store" / "review_comments.jsonl"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            fresh_store = ReviewCommentsStore(store_path)
            await fresh_store.load()
            await fresh_store.append(
                _comment(
                    file="tests/test_filter_flags.py",
                    severity=Severity.IMPORTANT,
                    cycle=0,
                    reviewer="reviewer-test-adequacy",
                ).model_copy(update={"ticket_id": "tb-post-startup"})
            )

            workflow = WorkflowConfig(
                name="w",
                phases=[
                    _phase("test", "test", writes=["tests/**"]),
                    _phase("implement", "dev", writes=["src/**"]),
                    _review_phase("review", ["reviewer-pattern-conformance"]),
                ],
            )
            result = await orch._route_blocked_phase(
                workflow,
                blocked_phase_idx=2,
                ticket_id="tb-post-startup",
                worktree=tmp_path,
            )
            # If the orchestrator's cached store were used without a
            # reload, this would be 1 (the dev-fallback path) because
            # for_ticket() would return []. With the reload, the
            # test-file comment is seen and the route is 0 (test phase).
            assert result == 0, (
                "Reload-before-query must pick up MCP-written comments. "
                "Got dev fallback — orchestrator is reading stale _docs."
            )
        finally:
            await orch.shutdown()
