"""Tests for the review-routing fix-loop router.

Step 7 of feature-work/review-routing/plan.md. The new helpers replace
the hard-coded ``_write_roles = {"dev"}`` in ``_find_fix_phase`` with
per-finding routing that consults, in order:

1. ``comment.target_role`` if set (reviewer-declared escalation).
2. The phase whose ``writes:`` glob matches ``comment.file``.
3. Multi-match disambiguation via commit trailer
   (``git log --pretty=format:%(trailers:key=Phase) -- <file>``).
4. Fallback to most-recent dev with ``block_reason="unowned-finding"``.

These helpers are added but no caller switches to them yet (step 8
does that). Tests use mock workflows + a real git repo for the
trailer-lookup case.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.models import PhaseConfig, WorkflowConfig
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity


def _phase(name: str, role: str, writes: list[str] | None = None) -> PhaseConfig:
    return PhaseConfig(name=name, role=role, writes=writes or [])


def _review_phase(name: str, reviewers: list[str]) -> PhaseConfig:
    return PhaseConfig(name=name, role="review", reviewers=reviewers)


def _comment(
    *,
    target_role: str | None = None,
    file: str | None = None,
    type_: str = "pattern-divergence",
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType(type_),
        severity=Severity.IMPORTANT,
        reviewer="reviewer-pattern-conformance",
        prose="finding",
        confidence=0.8,
        file=file,
        target_role=target_role,
    )


# ---------- _most_recent_phase_with_role ----------------------------------


class TestMostRecentPhaseWithRole:
    def test_returns_most_recent_before_blocked(self) -> None:
        from jig.reviewer_routing import _most_recent_phase_with_role

        wf = WorkflowConfig(
            name="w",
            phases=[
                _phase("spec", "spec"),
                _phase("test", "test"),
                _phase("scaffold", "dev"),
                _phase("implement", "dev"),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )
        # blocked at "review" (idx 4); most recent "dev" before that is idx 3
        assert _most_recent_phase_with_role(wf, blocked_phase_idx=4, role="dev") == 3

    def test_returns_none_when_role_absent(self) -> None:
        from jig.reviewer_routing import _most_recent_phase_with_role

        wf = WorkflowConfig(
            name="w",
            phases=[
                _phase("spec", "spec"),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )
        assert _most_recent_phase_with_role(wf, blocked_phase_idx=1, role="dev") is None

    def test_excludes_blocked_phase_itself(self) -> None:
        """The router must not route back to the phase that just blocked."""
        from jig.reviewer_routing import _most_recent_phase_with_role

        wf = WorkflowConfig(
            name="w",
            phases=[
                _phase("test", "test"),
                _phase("implement", "dev"),
            ],
        )
        # blocked at "implement" (idx 1); searching for "dev" should NOT
        # return idx 1 — that's the blocked phase.
        assert _most_recent_phase_with_role(wf, blocked_phase_idx=1, role="dev") is None

    def test_blocked_phase_idx_zero_returns_none(self) -> None:
        """First phase blocks → no earlier phases to route to."""
        from jig.reviewer_routing import _most_recent_phase_with_role

        wf = WorkflowConfig(
            name="w",
            phases=[_phase("spec", "spec"), _phase("implement", "dev")],
        )
        assert _most_recent_phase_with_role(wf, blocked_phase_idx=0, role="dev") is None


# ---------- _last_touching_phase ------------------------------------------


@pytest.fixture()
def git_worktree(tmp_path: Path) -> Path:
    """Init a real git repo and configure committer identity."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@jig.local"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "commit.gpgsign", "false"],
        check=True,
    )
    return tmp_path


def _commit(repo: Path, file: str, content: str, message: str) -> None:
    (repo / file).parent.mkdir(parents=True, exist_ok=True)
    (repo / file).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", file], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
    )


class TestLastTouchingPhase:
    async def test_returns_phase_trailer_value(self, git_worktree: Path) -> None:
        from jig.reviewer_routing import _last_touching_phase

        _commit(
            git_worktree,
            "src/foo.py",
            "x = 1\n",
            "feat: add foo\n\nPhase: implement\nAgent: dev\n",
        )
        assert await _last_touching_phase(git_worktree, "src/foo.py") == "implement"

    async def test_returns_most_recent_phase_across_multiple_commits(
        self, git_worktree: Path
    ) -> None:
        from jig.reviewer_routing import _last_touching_phase

        _commit(
            git_worktree,
            "tests/test_x.py",
            "def test_a():\n    pass\n",
            "test: add x\n\nPhase: test\nAgent: test\n",
        )
        _commit(
            git_worktree,
            "tests/test_x.py",
            "def test_a():\n    assert True\n",
            "test: tweak x\n\nPhase: implement\nAgent: dev\n",
        )
        # Most recent phase that touched this file is implement.
        assert (
            await _last_touching_phase(git_worktree, "tests/test_x.py") == "implement"
        )

    async def test_returns_none_when_no_trailer(self, git_worktree: Path) -> None:
        from jig.reviewer_routing import _last_touching_phase

        _commit(git_worktree, "src/bar.py", "y = 2\n", "feat: add bar")
        assert await _last_touching_phase(git_worktree, "src/bar.py") is None

    async def test_returns_none_for_untracked_file(self, git_worktree: Path) -> None:
        from jig.reviewer_routing import _last_touching_phase

        assert await _last_touching_phase(git_worktree, "nonexistent.py") is None


# ---------- _route_one ----------------------------------------------------


class TestRouteOne:
    def _wf(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="default",
            phases=[
                _phase("spec", "spec", writes=["docs/spec/**"]),
                _phase("test", "test", writes=["tests/**"]),
                _review_phase("review-tests", ["reviewer-test-adequacy"]),
                _phase(
                    "implement",
                    "dev",
                    writes=["src/**", "pyproject.toml"],
                ),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )

    async def test_target_role_overrides_glob(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_one

        wf = self._wf()
        c = _comment(target_role="spec", file="src/foo.py")
        idx, reason = await _route_one(
            wf, blocked_phase_idx=4, comment=c, worktree_path=tmp_path
        )
        assert idx == 0  # "spec" phase
        assert "target_role" in reason

    async def test_unknown_target_role_falls_through_to_glob(
        self, tmp_path: Path
    ) -> None:
        from jig.reviewer_routing import _route_one

        wf = self._wf()
        c = _comment(target_role="nonexistent-role", file="tests/test_x.py")
        idx, reason = await _route_one(
            wf, blocked_phase_idx=4, comment=c, worktree_path=tmp_path
        )
        # Falls through to test phase via writes-glob.
        assert idx == 1
        assert "writes-glob" in reason

    async def test_unknown_target_role_without_file_falls_to_dev(
        self, tmp_path: Path
    ) -> None:
        """target_role unknown + comment.file is None → fall all the way
        through to the dev fallback. The intermediate glob-routing step
        skips when there's no file to match."""
        from jig.reviewer_routing import _route_one

        wf = self._wf()
        c = _comment(target_role="nonexistent-role", file=None)
        idx, reason = await _route_one(
            wf, blocked_phase_idx=4, comment=c, worktree_path=tmp_path
        )
        assert idx == 3  # implement (dev)
        assert reason == "unowned-finding"

    async def test_single_glob_match(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_one

        wf = self._wf()
        c = _comment(file="tests/test_x.py")
        idx, reason = await _route_one(
            wf, blocked_phase_idx=4, comment=c, worktree_path=tmp_path
        )
        assert idx == 1  # "test" phase
        assert "writes-glob" in reason

    async def test_unowned_finding_falls_back_to_dev(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_one

        wf = self._wf()
        c = _comment(file="some/unowned/file.txt")
        idx, reason = await _route_one(
            wf, blocked_phase_idx=4, comment=c, worktree_path=tmp_path
        )
        assert idx == 3  # "implement" (dev role)
        assert reason == "unowned-finding"

    async def test_no_route_when_no_file_and_no_dev_phase(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_one

        # Workflow with no dev phase
        wf = WorkflowConfig(
            name="docs-only",
            phases=[
                _phase("draft", "spec", writes=["docs/**"]),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )
        c = _comment(file=None)
        idx, reason = await _route_one(
            wf, blocked_phase_idx=1, comment=c, worktree_path=tmp_path
        )
        assert idx is None
        assert reason == "no-route"


# ---------- _route_one out-of-scope defence-in-depth -----------------------


class TestRouteOneOutOfScope:
    """When a reviewer with ``reads_glob`` files a finding on a file
    OUTSIDE that scope, ``_route_one`` drops the finding (returns
    ``(None, "out-of-scope-finding")``) and logs a warning.

    This is the defence-in-depth layer for reviewer-scoping
    (feature-work/reviewer-scoping). The MCP tools
    (``reviewer_get_diff`` / ``reviewer_read_file``) prevent the
    reviewer from SEEING out-of-scope content; this routing-layer
    check catches LLM-hallucinated findings that cite files the
    reviewer never read.
    """

    def _wf(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="default",
            phases=[
                _phase("test", "test", writes=["tests/**"]),
                _review_phase("review-tests", ["reviewer-test-adequacy"]),
                _phase("implement", "dev", writes=["src/**"]),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )

    def _scoped_reviewer_role(
        self, project_path: Path, role: str
    ) -> None:
        """Write a role config with ``reads_glob`` so the routing
        layer's load_role call finds it."""
        from jig.models import RoleConfig
        from jig.persistence import init_project, save_role

        (project_path / ".git").mkdir(exist_ok=True)
        if not (project_path / ".jig").is_dir():
            init_project(project_path)
        save_role(
            project_path,
            RoleConfig(
                role=role,
                phase_prompt="…",
                reads_glob=["src/**"],
                reads_exclude=["tests/**"],
                allowed_tools=[
                    "reviewer_read_file",
                    "reviewer_get_diff",
                    "reviewer_post_comment",
                ],
            ),
        )

    async def test_out_of_scope_finding_dropped(
        self, tmp_path: Path
    ) -> None:
        """A non-test reviewer files on tests/test_x.py — the routing
        layer drops it instead of bouncing back to test."""
        from jig.reviewer_routing import _route_one

        self._scoped_reviewer_role(tmp_path, "reviewer-pattern-conformance")
        wf = self._wf()
        c = _comment(file="tests/test_x.py")
        idx, reason = await _route_one(
            wf,
            blocked_phase_idx=3,
            comment=c,
            worktree_path=tmp_path,
            project_path=tmp_path,
        )
        assert idx is None
        assert reason == "out-of-scope-finding"

    async def test_in_scope_finding_routes_normally(
        self, tmp_path: Path
    ) -> None:
        """Same reviewer, file in scope (src/foo.py) — routes
        normally via writes-glob."""
        from jig.reviewer_routing import _route_one

        self._scoped_reviewer_role(tmp_path, "reviewer-pattern-conformance")
        wf = self._wf()
        c = _comment(file="src/foo.py")
        idx, reason = await _route_one(
            wf,
            blocked_phase_idx=3,
            comment=c,
            worktree_path=tmp_path,
            project_path=tmp_path,
        )
        assert idx == 2  # "implement" phase via writes-glob
        assert "writes-glob" in reason

    async def test_without_project_path_skips_scope_check(
        self, tmp_path: Path
    ) -> None:
        """Back-compat: callers (e.g. ``fix_loop_bundle``) that don't
        pass ``project_path`` get the legacy behaviour — the scope
        check is silently skipped. Finding routes via writes-glob."""
        from jig.reviewer_routing import _route_one

        wf = self._wf()
        c = _comment(file="tests/test_x.py")
        # No project_path passed.
        idx, reason = await _route_one(
            wf, blocked_phase_idx=3, comment=c, worktree_path=tmp_path
        )
        # Routes via tests/** writes-glob — defence skipped.
        assert idx == 0

    async def test_role_without_reads_glob_skips_check(
        self, tmp_path: Path
    ) -> None:
        """A reviewer that doesn't declare ``reads_glob`` is
        considered unscoped — the routing check doesn't second-guess
        its findings."""
        from jig.models import RoleConfig
        from jig.persistence import init_project, save_role
        from jig.reviewer_routing import _route_one

        (tmp_path / ".git").mkdir(exist_ok=True)
        init_project(tmp_path)
        # Unscoped role (legacy / pre-feature config).
        save_role(
            tmp_path,
            RoleConfig(
                role="reviewer-pattern-conformance",
                phase_prompt="…",
                allowed_tools=["Read", "Bash(git diff*)"],
            ),
        )
        wf = self._wf()
        c = _comment(file="tests/test_x.py")
        idx, _reason = await _route_one(
            wf,
            blocked_phase_idx=3,
            comment=c,
            worktree_path=tmp_path,
            project_path=tmp_path,
        )
        # Legacy role — routes via writes-glob to test phase.
        assert idx == 0


# ---------- _route_one multi-glob tie-break via trailers -------------------


class TestRouteOneMultiGlobTieBreak:
    """When multiple phases declare overlapping ``writes:`` globs, use
    the commit trailer to identify the phase that most recently
    touched the file."""

    def _wf_with_overlap(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="w",
            phases=[
                # Shared "fixtures" path under tests/ — both test and dev
                # declare it as theirs (intentional overlap to test the
                # tie-break).
                _phase("test", "test", writes=["tests/**"]),
                _phase("implement", "dev", writes=["tests/**", "src/**"]),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )

    async def test_trailer_picks_winning_candidate(self, git_worktree: Path) -> None:
        from jig.reviewer_routing import _route_one

        _commit(
            git_worktree,
            "tests/fixtures.py",
            "x = 1\n",
            "test: add fixtures\n\nPhase: test\nAgent: test\n",
        )
        _commit(
            git_worktree,
            "tests/fixtures.py",
            "x = 2\n",
            "feat: dev tweaked fixture\n\nPhase: implement\nAgent: dev\n",
        )

        wf = self._wf_with_overlap()
        c = _comment(file="tests/fixtures.py")
        idx, reason = await _route_one(
            wf, blocked_phase_idx=2, comment=c, worktree_path=git_worktree
        )
        # Both test (0) and implement (1) match; trailer says implement
        # touched it most recently.
        assert idx == 1
        assert "last-touched" in reason

    async def test_no_trailer_falls_back_to_earliest_candidate(
        self, git_worktree: Path
    ) -> None:
        from jig.reviewer_routing import _route_one

        # Commits with no Phase: trailer (operator-authored or pre-hook).
        _commit(git_worktree, "tests/fixtures.py", "x = 1\n", "test: add fixtures")
        _commit(git_worktree, "tests/fixtures.py", "x = 2\n", "fix: bump fixture")

        wf = self._wf_with_overlap()
        c = _comment(file="tests/fixtures.py")
        idx, reason = await _route_one(
            wf, blocked_phase_idx=2, comment=c, worktree_path=git_worktree
        )
        # Both test (0) and implement (1) match. With no trailer, the
        # earliest candidate (test = 0) wins, flagged as ambiguous.
        assert idx == 0
        assert reason == "ambiguous-ownership"


# ---------- _route_blocking_comments --------------------------------------


class TestRouteBlockingComments:
    def _wf(self) -> WorkflowConfig:
        return WorkflowConfig(
            name="w",
            phases=[
                _phase("spec", "spec", writes=["docs/**"]),
                _phase("test", "test", writes=["tests/**"]),
                _phase("implement", "dev", writes=["src/**"]),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )

    async def test_returns_none_when_no_blocking_comments(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_blocking_comments

        result = await _route_blocking_comments(
            self._wf(), blocked_phase_idx=3, comments=[], worktree_path=tmp_path
        )
        assert result is None

    async def test_single_comment_returns_its_target(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_blocking_comments

        c = _comment(file="src/foo.py")
        result = await _route_blocking_comments(
            self._wf(),
            blocked_phase_idx=3,
            comments=[c],
            worktree_path=tmp_path,
        )
        assert result is not None
        idx, reason = result
        assert idx == 2  # implement
        assert "writes-glob" in reason

    async def test_multiple_comments_pick_earliest_phase(self, tmp_path: Path) -> None:
        """When findings span multiple phases, retry at the EARLIEST so
        the flow walks forward through the workflow on each cycle."""
        from jig.reviewer_routing import _route_blocking_comments

        c1 = _comment(file="src/foo.py")  # → implement (idx 2)
        c2 = _comment(file="tests/test_x.py")  # → test (idx 1)
        result = await _route_blocking_comments(
            self._wf(),
            blocked_phase_idx=3,
            comments=[c1, c2],
            worktree_path=tmp_path,
        )
        assert result is not None
        idx, reason = result
        assert idx == 1  # test, the earliest
        # Both reasons surface in the combined string.
        assert "writes-glob" in reason

    async def test_returns_none_when_no_comment_routes(self, tmp_path: Path) -> None:
        from jig.reviewer_routing import _route_blocking_comments

        # No file → falls to dev. But here we drop dev from the workflow.
        wf = WorkflowConfig(
            name="docs-only",
            phases=[
                _phase("spec", "spec", writes=["docs/**"]),
                _review_phase("review", ["reviewer-pattern-conformance"]),
            ],
        )
        c = _comment(file=None)
        result = await _route_blocking_comments(
            wf, blocked_phase_idx=1, comments=[c], worktree_path=tmp_path
        )
        assert result is None
