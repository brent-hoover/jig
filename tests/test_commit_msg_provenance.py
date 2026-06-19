"""Tests for the prepare-commit-msg hook + worktree context file.

Step 4 of feature-work/review-routing/plan.md. The hook reads
``<worktree>/.jig/worktree.context`` (key=value lines: ``phase=`` and
``agent=``) and appends matching Git trailers to every commit message
made in the worktree. This is the provenance the future fix-loop
router consults via ``git log --pretty=format:%(trailers:key=Phase) -- <file>``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


# ---------- install_commit_msg_hook ----------------------------------------


class TestInstallCommitMsgHook:
    def test_writes_executable_hook_at_expected_path(self, tmp_path: Path) -> None:
        from jig.hooks.commit_msg_provenance import install_commit_msg_hook

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        hook_path = install_commit_msg_hook(tmp_path)
        assert hook_path == tmp_path / ".git" / "hooks" / "prepare-commit-msg"
        assert hook_path.is_file()
        mode = hook_path.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

    def test_hook_carries_sentinel_for_ownership_detection(
        self, tmp_path: Path
    ) -> None:
        from jig.hooks.commit_msg_provenance import (
            COMMIT_MSG_PROVENANCE_SENTINEL,
            install_commit_msg_hook,
        )

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        hook_path = install_commit_msg_hook(tmp_path)
        assert COMMIT_MSG_PROVENANCE_SENTINEL in hook_path.read_text()

    def test_install_is_idempotent(self, tmp_path: Path) -> None:
        from jig.hooks.commit_msg_provenance import (
            COMMIT_MSG_PROVENANCE_SENTINEL,
            install_commit_msg_hook,
        )

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        install_commit_msg_hook(tmp_path)
        # Second install overwrites cleanly with intact contents.
        install_commit_msg_hook(tmp_path)
        hook_path = tmp_path / ".git" / "hooks" / "prepare-commit-msg"
        assert hook_path.is_file()
        assert COMMIT_MSG_PROVENANCE_SENTINEL in hook_path.read_text()


# ---------- write_worktree_context ----------------------------------------


class TestWriteWorktreeContext:
    def test_writes_context_file_with_phase_and_agent(self, tmp_path: Path) -> None:
        from jig.hooks.commit_msg_provenance import write_worktree_context

        ctx_path = write_worktree_context(tmp_path, phase="implement", agent="dev")
        assert ctx_path == tmp_path / ".jig" / "worktree.context"
        text = ctx_path.read_text()
        assert "phase=implement" in text
        assert "agent=dev" in text

    def test_overwrites_existing_context_on_phase_change(self, tmp_path: Path) -> None:
        """Phase boundaries rewrite the context — old values gone."""
        from jig.hooks.commit_msg_provenance import write_worktree_context

        write_worktree_context(tmp_path, phase="test", agent="test")
        write_worktree_context(tmp_path, phase="implement", agent="dev")
        text = (tmp_path / ".jig" / "worktree.context").read_text()
        assert "phase=implement" in text
        assert "agent=dev" in text
        assert "phase=test" not in text


# ---------- hook behaviour (integration via shell) ------------------------


def _run_hook(worktree: Path, msg_file: Path) -> int:
    """Invoke the installed prepare-commit-msg hook directly."""
    hook = worktree / ".git" / "hooks" / "prepare-commit-msg"
    result = subprocess.run(
        [str(hook), str(msg_file)],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return result.returncode


@pytest.fixture()
def worktree_with_hook(tmp_path: Path) -> Path:
    """git init + install hook. The hook uses ``git rev-parse`` to
    resolve the worktree root, so a real repo is required even for the
    no-commit unit tests."""
    from jig.hooks.commit_msg_provenance import install_commit_msg_hook

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    install_commit_msg_hook(tmp_path)
    return tmp_path


class TestHookBehavior:
    def test_appends_both_trailers_when_context_complete(
        self, worktree_with_hook: Path
    ) -> None:
        from jig.hooks.commit_msg_provenance import write_worktree_context

        write_worktree_context(worktree_with_hook, phase="implement", agent="dev")
        msg = worktree_with_hook / "COMMIT_EDITMSG"
        msg.write_text("feat: do thing\n")
        rc = _run_hook(worktree_with_hook, msg)
        assert rc == 0
        body = msg.read_text()
        assert "Phase: implement" in body
        assert "Agent: dev" in body

    def test_appends_only_present_trailer_when_partial_context(
        self, worktree_with_hook: Path
    ) -> None:
        """Missing field → trailer skipped. No empty `Phase: ` lines."""
        (worktree_with_hook / ".jig").mkdir()
        (worktree_with_hook / ".jig" / "worktree.context").write_text("phase=test\n")
        msg = worktree_with_hook / "COMMIT_EDITMSG"
        msg.write_text("test: add fixtures\n")
        rc = _run_hook(worktree_with_hook, msg)
        assert rc == 0
        body = msg.read_text()
        assert "Phase: test" in body
        assert "Agent:" not in body

    def test_idempotent_on_existing_trailers(self, worktree_with_hook: Path) -> None:
        """Re-running the hook on a message that already has the trailers
        doesn't duplicate them (commit --amend, hook chain edge cases)."""
        from jig.hooks.commit_msg_provenance import write_worktree_context

        write_worktree_context(worktree_with_hook, phase="implement", agent="dev")
        msg = worktree_with_hook / "COMMIT_EDITMSG"
        msg.write_text("feat: x\n\nPhase: implement\nAgent: dev\n")
        rc = _run_hook(worktree_with_hook, msg)
        assert rc == 0
        body = msg.read_text()
        assert body.count("Phase: implement") == 1
        assert body.count("Agent: dev") == 1

    def test_noop_when_context_file_missing(self, worktree_with_hook: Path) -> None:
        """Worktrees that pre-date this work, or commits made outside a
        phase, get unchanged messages and a clean exit."""
        # Deliberately do not write .jig/worktree.context.
        msg = worktree_with_hook / "COMMIT_EDITMSG"
        msg.write_text("chore: ad-hoc\n")
        rc = _run_hook(worktree_with_hook, msg)
        assert rc == 0
        body = msg.read_text()
        assert "Phase:" not in body
        assert "Agent:" not in body

    def test_graceful_degradation_when_interpret_trailers_unavailable(
        self, worktree_with_hook: Path
    ) -> None:
        """If ``git interpret-trailers`` fails (old git, stripped build,
        transient error), the hook must exit 0 and leave the commit message
        unchanged. The hook is best-effort metadata; failures must NOT
        abort the commit."""
        from jig.hooks.commit_msg_provenance import write_worktree_context

        write_worktree_context(worktree_with_hook, phase="implement", agent="dev")
        msg = worktree_with_hook / "COMMIT_EDITMSG"
        msg.write_text("feat: x\n")

        # Shim a `git` that fails on `interpret-trailers` but passes
        # `rev-parse --show-toplevel` through to the real git so the
        # hook can locate the worktree.
        shim_dir = worktree_with_hook / "shim-bin"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        real_git = subprocess.run(
            ["which", "git"], capture_output=True, text=True, check=True
        ).stdout.strip()
        shim.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "interpret-trailers" ]; then exit 99; fi\n'
            f'exec "{real_git}" "$@"\n'
        )
        shim.chmod(0o755)

        hook = worktree_with_hook / ".git" / "hooks" / "prepare-commit-msg"
        env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [str(hook), str(msg)],
            cwd=worktree_with_hook,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Commit message survives unchanged — no trailer, no error spill.
        assert msg.read_text() == "feat: x\n"


# ---------- end-to-end: real git repo + commit + trailer lookup -----------


class TestEndToEndGitCommit:
    @pytest.fixture()
    def git_repo(self, tmp_path: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        # Local user identity — required for `git commit`.
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.email", "test@jig.local"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
            check=True,
        )
        # Disable signing in case the host has it enabled globally.
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "commit.gpgsign", "false"],
            check=True,
        )
        return tmp_path

    def test_real_commit_carries_phase_trailer(self, git_repo: Path) -> None:
        """The whole point of step 4: ``git log --pretty=format:%(trailers:key=Phase)``
        returns the phase the commit was authored under."""
        from jig.hooks.commit_msg_provenance import (
            install_commit_msg_hook,
            write_worktree_context,
        )

        install_commit_msg_hook(git_repo)
        write_worktree_context(git_repo, phase="implement", agent="dev")

        (git_repo / "x.txt").write_text("hi\n")
        subprocess.run(["git", "-C", str(git_repo), "add", "x.txt"], check=True)
        # No --no-verify: the hook must fire normally.
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "feat: add x"],
            check=True,
            env={**os.environ, "GIT_COMMITTER_DATE": "2026-05-18T00:00:00Z"},
        )
        result = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo),
                "log",
                "-1",
                "--pretty=format:%(trailers:key=Phase,valueonly)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "implement"
