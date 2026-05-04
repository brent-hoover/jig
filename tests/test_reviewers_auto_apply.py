"""Tests for the auto-apply path (Track G MVP follow-on).

Covers each skip rule + happy apply + git-apply-failed handling.
Uses a real on-disk git repo so ``git apply`` actually runs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from jig.reviewers.auto_apply import (
    AutoApplyResult,
    _commit_message,
    _should_skip,
    apply_suggested_diffs,
)
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)


def _make(
    *,
    type_: ReviewerCommentType = ReviewerCommentType.CONTRACT_VIOLATION,
    severity: Severity = Severity.IMPORTANT,
    confidence: float = 1.0,
    suggested_diff: str | None = "patch-here",
    file_: str | None = "foo.py",
    line: int | None = 3,
) -> ReviewerComment:
    return ReviewerComment(
        type=type_,
        severity=severity,
        reviewer="contract-compliance",
        prose="finding prose",
        confidence=confidence,
        suggested_diff=suggested_diff,
        file=file_,
        line=line,
    )


async def _spawn(repo: Path, *args: str, stdin: bytes | None = None) -> int:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=repo,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate(stdin)
    return proc.returncode


async def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    await _spawn(repo, "init", "-q")
    await _spawn(repo, "config", "user.email", "test@example.com")
    await _spawn(repo, "config", "user.name", "test")
    await _spawn(repo, "config", "commit.gpgsign", "false")
    (repo / "foo.py").write_text("x = 1\ny = 2\nz = 3\n")
    await _spawn(repo, "add", "-A")
    await _spawn(repo, "commit", "-q", "-m", "initial")
    return repo


_GOOD_DIFF = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,3 +1,3 @@\n"
    "-x = 1\n"
    "+x = 99\n"
    " y = 2\n"
    " z = 3\n"
)


class TestSkipRules:
    def test_skips_low_confidence(self) -> None:
        c = _make(confidence=0.7)
        assert _should_skip(c) == "judgment_confidence"

    def test_skips_missing_diff(self) -> None:
        c = _make(suggested_diff=None)
        assert _should_skip(c) == "no_suggested_diff"

    def test_skips_empty_diff(self) -> None:
        c = _make(suggested_diff="   \n  ")
        assert _should_skip(c) == "no_suggested_diff"

    def test_skips_critical(self) -> None:
        c = _make(severity=Severity.CRITICAL)
        assert _should_skip(c) == "critical_severity"

    def test_passes_when_all_clear(self) -> None:
        c = _make(confidence=1.0, severity=Severity.IMPORTANT)
        assert _should_skip(c) is None


class TestCommitMessage:
    def test_full_format(self) -> None:
        c = _make(file_="foo.py", line=12)
        msg = _commit_message(c)
        assert msg.startswith("chore(auto-apply): ")
        assert "foo.py:12" in msg
        assert ReviewerCommentType.CONTRACT_VIOLATION.value in msg

    def test_no_line(self) -> None:
        c = _make(file_="foo.py", line=None)
        msg = _commit_message(c)
        assert "foo.py" in msg
        assert ":" not in msg.split("at ")[1]

    def test_no_file(self) -> None:
        c = _make(file_=None, line=None)
        msg = _commit_message(c)
        assert msg == f"chore(auto-apply): {ReviewerCommentType.CONTRACT_VIOLATION.value}"


class TestApplyHappyPath:
    async def test_applies_clean_diff_and_commits(self, tmp_path: Path) -> None:
        repo = await _init_repo(tmp_path)
        c = _make(suggested_diff=_GOOD_DIFF)
        result = await apply_suggested_diffs([c], repo)
        assert isinstance(result, AutoApplyResult)
        assert len(result.applied) == 1
        assert not result.skipped
        assert not result.failed
        assert (repo / "foo.py").read_text().splitlines()[0] == "x = 99"

        proc = await asyncio.create_subprocess_exec(
            "git",
            "log",
            "-1",
            "--pretty=%s",
            cwd=repo,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert stdout.decode().strip().startswith("chore(auto-apply):")


class TestApplyFailures:
    async def test_invalid_diff_lands_in_failed(self, tmp_path: Path) -> None:
        repo = await _init_repo(tmp_path)
        c = _make(suggested_diff="this is not a valid unified diff")
        result = await apply_suggested_diffs([c], repo)
        assert not result.applied
        assert len(result.failed) == 1
        comment, reason = result.failed[0]
        assert comment is c
        assert "git_apply_rc" in reason

    async def test_one_failure_does_not_block_next(
        self, tmp_path: Path
    ) -> None:
        repo = await _init_repo(tmp_path)
        bad = _make(suggested_diff="not a diff")
        good = _make(suggested_diff=_GOOD_DIFF)
        result = await apply_suggested_diffs([bad, good], repo)
        assert len(result.applied) == 1
        assert len(result.failed) == 1


class TestApplyMixed:
    async def test_skipped_and_applied_in_same_batch(
        self, tmp_path: Path
    ) -> None:
        repo = await _init_repo(tmp_path)
        skip_low_conf = _make(confidence=0.5, suggested_diff=_GOOD_DIFF)
        skip_critical = _make(severity=Severity.CRITICAL, suggested_diff=_GOOD_DIFF)
        skip_no_diff = _make(suggested_diff=None)
        good = _make(suggested_diff=_GOOD_DIFF)
        result = await apply_suggested_diffs(
            [skip_low_conf, skip_critical, skip_no_diff, good],
            repo,
        )
        assert len(result.applied) == 1
        assert len(result.skipped) == 3
        assert not result.failed
