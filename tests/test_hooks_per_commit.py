"""Tests for ``jig.hooks.per_commit.install_per_commit_hook`` (Track G MVP)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.hooks.per_commit import (
    PER_COMMIT_SENTINEL,
    install_per_commit_hook,
    is_executable,
    is_per_commit_hook,
)


async def _git(cwd: Path, *args: str) -> int:
    proc = await asyncio.create_subprocess_shell(
        "git " + " ".join(args),
        cwd=cwd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode


@pytest.fixture
def fake_worktree(tmp_path: Path) -> Path:
    """Construct a worktree-like directory with a local .git/hooks dir.

    The installer's "real worktree" branch reads ``.git`` as a file
    pointer; the installer also tolerates a plain ``.git/hooks`` dir
    for tests, which is what this fixture provides.
    """
    wt = tmp_path / "worktrees" / "tkt-1"
    wt.mkdir(parents=True)
    (wt / ".git").mkdir()
    (wt / ".git" / "hooks").mkdir()
    return wt


class TestInstall:
    def test_writes_post_commit_hook(self, fake_worktree: Path) -> None:
        path = install_per_commit_hook(fake_worktree)
        assert path.is_file()
        assert path.name == "post-commit"
        body = path.read_text()
        assert PER_COMMIT_SENTINEL in body
        assert "tkt-1" in body
        assert "exit 0" in body

    def test_hook_is_executable(self, fake_worktree: Path) -> None:
        path = install_per_commit_hook(fake_worktree)
        assert is_executable(path)

    def test_idempotent_overwrite(self, fake_worktree: Path) -> None:
        first = install_per_commit_hook(fake_worktree)
        body1 = first.read_text()
        second = install_per_commit_hook(fake_worktree)
        assert second == first
        assert second.read_text() == body1


class TestSentinelDetection:
    def test_recognizes_installed_hook(self, fake_worktree: Path) -> None:
        path = install_per_commit_hook(fake_worktree)
        assert is_per_commit_hook(path)

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        assert not is_per_commit_hook(tmp_path / "nope")

    def test_rejects_foreign_hook(self, tmp_path: Path) -> None:
        foreign = tmp_path / "post-commit"
        foreign.write_text("#!/bin/sh\necho hi\n")
        assert not is_per_commit_hook(foreign)


class TestRealWorktreeIntegration:
    async def test_install_into_real_worktree(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        await _git(repo, "init", "-q")
        await _git(repo, "config", "user.email", "t@t.t")
        await _git(repo, "config", "user.name", "t")
        await _git(repo, "config", "commit.gpgsign", "false")
        (repo / "f").write_text("x")
        await _git(repo, "add", "-A")
        await _git(repo, "commit", "-q", "-m", "init")

        wt = repo / ".jig" / "worktrees" / "tkt-99"
        wt.parent.mkdir(parents=True)
        await _git(repo, "worktree", "add", "-b", "jig/tkt-99", str(wt))

        path = install_per_commit_hook(wt)
        assert path.is_file()
        assert is_per_commit_hook(path)
        assert is_executable(path)
