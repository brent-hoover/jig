import os
import subprocess
from pathlib import Path

import pytest

from jig.worktree import (
    MergeConflictError,
    commit_worktree,
    create_worktree,
    remove_worktree,
    sync_project_claude_md,
)


def test_merge_conflict_error_conflicted_files_default_empty() -> None:
    err = MergeConflictError("t1", "jig/t1")
    assert err.conflicted_files == []


def test_merge_conflict_error_conflicted_files_passed_through() -> None:
    err = MergeConflictError("t1", "jig/t1", conflicted_files=["src/a.py", "src/b.py"])
    assert err.conflicted_files == ["src/a.py", "src/b.py"]


def test_merge_conflict_error_none_becomes_empty_list() -> None:
    err = MergeConflictError("t1", "jig/t1", conflicted_files=None)
    assert err.conflicted_files == []


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, capture_output=True)
    (repo / ".jig" / "worktrees").mkdir(parents=True)
    return repo


class TestCreateWorktree:
    async def test_creates_worktree_directory(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "main")
        assert wt_path.is_dir()
        assert (wt_path / "README.md").is_file()

    async def test_worktree_is_on_new_branch(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "main")
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "jig/issue-1"

    async def test_worktree_path(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "main")
        expected = git_repo / ".jig" / "worktrees" / "issue-1"
        assert wt_path == expected


class TestCommitWorktree:
    async def test_commits_changes(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "main")
        (wt_path / "design.md").write_text("# Design\n")
        sha = await commit_worktree(wt_path, "spec: draft design doc")
        assert sha  # non-empty string
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=wt_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "spec: draft design doc" in result.stdout

    async def test_returns_none_if_no_changes(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "main")
        sha = await commit_worktree(wt_path, "nothing to commit")
        assert sha is None


class TestRemoveWorktree:
    async def test_removes_worktree(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "main")
        assert wt_path.is_dir()
        await remove_worktree(git_repo, "issue-1")
        assert not wt_path.is_dir()


def _make_project_with_claude_md(tmp_path: Path, content: str | None) -> Path:
    """Build a minimal <project>/.jig/CLAUDE.md tree for sync tests.
    ``content=None`` produces a project without the file."""
    project = tmp_path / "project"
    project.mkdir()
    if content is not None:
        (project / ".jig").mkdir()
        (project / ".jig" / "CLAUDE.md").write_text(content)
    return project


def _resolve_exclude_path(repo: Path) -> Path:
    """Resolve a repo's per-worktree info/exclude via git itself (mirrors
    production). Used by tests instead of hardcoded ``.git/info/exclude`` so
    the assertion is robust to linked-worktree layout differences."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    p = Path(result.stdout.strip())
    return p if p.is_absolute() else repo / p


class TestSyncProjectClaudeMd:
    async def test_copies_project_source_into_worktree(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "project content\n")
        await sync_project_claude_md(git_repo, project)
        assert (git_repo / "CLAUDE.md").read_text() == "project content\n"

    async def test_writes_stub_when_project_source_missing(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, None)
        await sync_project_claude_md(git_repo, project)
        content = (git_repo / "CLAUDE.md").read_text()
        assert "jig-managed" in content
        assert ".jig/CLAUDE.md" in content

    async def test_skip_worktree_set_when_claude_md_tracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        # Commit a CLAUDE.md so it's tracked in the index.
        (git_repo / "CLAUDE.md").write_text("original\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add CLAUDE.md"], cwd=git_repo, check=True
        )

        project = _make_project_with_claude_md(tmp_path, "overwritten\n")
        await sync_project_claude_md(git_repo, project)

        result = subprocess.run(
            ["git", "ls-files", "-v", "CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        # Lowercase letter prefix indicates skip-worktree bit set (S/s).
        assert result.stdout.startswith(("S ", "s "))

    async def test_info_exclude_used_when_claude_md_untracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "untracked content\n")
        await sync_project_claude_md(git_repo, project)

        exclude_path = _resolve_exclude_path(git_repo)
        # Anchored pattern — leading "/" in gitignore syntax matches root only.
        assert "/CLAUDE.md" in exclude_path.read_text().splitlines()

    async def test_info_exclude_is_idempotent(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "content\n")
        for _ in range(3):
            await sync_project_claude_md(git_repo, project)

        exclude_path = _resolve_exclude_path(git_repo)
        lines = exclude_path.read_text().splitlines()
        assert lines.count("/CLAUDE.md") == 1

    async def test_anchored_exclude_keeps_nested_claude_md_visible(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """The exclude entry must be ``/CLAUDE.md`` (root-anchored), not
        ``CLAUDE.md`` (unanchored). An unanchored entry would silently
        suppress nested files like ``docs/CLAUDE.md`` or
        ``feature-work/foo/CLAUDE.md`` — agents could create those and they'd
        never get committed."""
        project = _make_project_with_claude_md(tmp_path, "content\n")
        await sync_project_claude_md(git_repo, project)

        # Plant a nested CLAUDE.md that should remain visible to git.
        (git_repo / "docs").mkdir()
        (git_repo / "docs" / "CLAUDE.md").write_text("nested content\n")

        # Use git check-ignore — definitive answer on whether a path is
        # ignored, independent of status's directory collapsing.
        nested_check = subprocess.run(
            ["git", "check-ignore", "docs/CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        # Exit 1 = not ignored (what we want); exit 0 = ignored (the bug).
        assert nested_check.returncode == 1, (
            f"nested docs/CLAUDE.md should not be ignored; "
            f"got rc={nested_check.returncode} stdout={nested_check.stdout!r}"
        )

        # Root CLAUDE.md must still be ignored.
        root_check = subprocess.run(
            ["git", "check-ignore", "CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert root_check.returncode == 0

    async def test_legacy_unanchored_exclude_is_migrated(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """Earlier versions of this code wrote unanchored ``CLAUDE.md`` to
        info/exclude — that pattern matches every CLAUDE.md anywhere in the
        repo, silently suppressing nested docs. When sync runs against a
        worktree that has the legacy entry, it must remove the legacy form
        and add the anchored ``/CLAUDE.md`` form so nested files are no
        longer ignored."""
        # Seed the exclude with the legacy unanchored entry.
        exclude_path = _resolve_exclude_path(git_repo)
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        exclude_path.write_text("# pre-existing comment\nCLAUDE.md\n")

        # Confirm nested files ARE ignored before migration.
        (git_repo / "docs").mkdir()
        (git_repo / "docs" / "CLAUDE.md").write_text("nested\n")
        before = subprocess.run(
            ["git", "check-ignore", "docs/CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert before.returncode == 0  # ignored

        project = _make_project_with_claude_md(tmp_path, "content\n")
        await sync_project_claude_md(git_repo, project)

        # Legacy line removed, anchored line added.
        lines = exclude_path.read_text().splitlines()
        assert "CLAUDE.md" not in lines
        assert "/CLAUDE.md" in lines

        # And nested files are now visible.
        after = subprocess.run(
            ["git", "check-ignore", "docs/CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
        )
        assert after.returncode == 1  # not ignored

    async def test_source_swap_during_sync_does_not_leak_outside(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """Deterministic check that source-side symlink rejection happens on
        the SAME file descriptor as the read (O_NOFOLLOW + fstat). We can't
        race a real swap reliably, but we can confirm a non-regular source
        (here: a FIFO) is also refused, which exercises the same
        ``stat.S_ISREG`` branch that closes the TOCTOU."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".jig").mkdir()
        fifo_path = project / ".jig" / "CLAUDE.md"
        os.mkfifo(fifo_path)
        assert not fifo_path.is_file()

        await sync_project_claude_md(git_repo, project)

        # Worktree CLAUDE.md falls back to the stub — the FIFO was not read.
        worktree_claude = (git_repo / "CLAUDE.md").read_text()
        assert "jig-managed" in worktree_claude

    async def test_symlinked_project_source_is_rejected(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """Same class of attack as the dst symlink, but on the READ side:
        a project (or attacker who can write <project>/.jig/) points
        .jig/CLAUDE.md at a file outside the project root. Sync must not
        follow the link — falls back to the stub and logs a warning."""
        # Sensitive file outside the project that an attacker wants leaked
        # into the agent worktree.
        outside_secret = tmp_path / "outside-secret.txt"
        outside_secret.write_text("OPERATOR_SECRET_DO_NOT_LEAK\n")

        # Set up a project whose .jig/CLAUDE.md is a SYMLINK to the secret.
        project = tmp_path / "project"
        project.mkdir()
        (project / ".jig").mkdir()
        (project / ".jig" / "CLAUDE.md").symlink_to(outside_secret)
        assert (project / ".jig" / "CLAUDE.md").is_symlink()

        await sync_project_claude_md(git_repo, project)

        # Worktree CLAUDE.md must NOT contain the secret.
        worktree_claude = (git_repo / "CLAUDE.md").read_text()
        assert "OPERATOR_SECRET" not in worktree_claude
        # Falls back to the missing-project stub.
        assert "jig-managed" in worktree_claude

    async def test_git_status_clean_after_sync_untracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "content\n")
        await sync_project_claude_md(git_repo, project)

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "CLAUDE.md" not in result.stdout

    async def test_git_status_clean_after_sync_tracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        (git_repo / "CLAUDE.md").write_text("original\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add CLAUDE.md"], cwd=git_repo, check=True
        )

        project = _make_project_with_claude_md(tmp_path, "overwritten\n")
        await sync_project_claude_md(git_repo, project)

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "CLAUDE.md" not in result.stdout

    async def test_symlink_at_destination_does_not_overwrite_target(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """If a project plants a CLAUDE.md SYMLINK at the worktree root —
        tracked or untracked — naive ``write_text`` would follow the link and
        clobber whatever the link points to (potentially a file outside the
        worktree). Sync must unlink the symlink first, then write a regular
        file at that path."""
        # File OUTSIDE the worktree that an attacker symlinks into the
        # worktree as CLAUDE.md.
        outside_target = tmp_path / "outside-target.txt"
        outside_target.write_text("operator's important file\n")

        # Plant the symlink inside the worktree.
        symlink_path = git_repo / "CLAUDE.md"
        symlink_path.symlink_to(outside_target)
        assert symlink_path.is_symlink()

        project = _make_project_with_claude_md(tmp_path, "jig content\n")
        await sync_project_claude_md(git_repo, project)

        # Outside file is untouched — link was unlinked, not followed.
        assert outside_target.read_text() == "operator's important file\n"
        # Inside the worktree, CLAUDE.md is now a regular file with jig content.
        assert not symlink_path.is_symlink()
        assert symlink_path.is_file()
        assert symlink_path.read_text() == "jig content\n"

    async def test_project_root_spawn_is_a_noop(self, git_repo: Path) -> None:
        """Some jig spawns (init/spec-generator/concierge) use the project root
        AS their worktree. Sync must skip rather than clobber the operator's
        checked-in root CLAUDE.md."""
        (git_repo / "CLAUDE.md").write_text("operator's real root CLAUDE.md\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add CLAUDE.md"], cwd=git_repo, check=True
        )

        # worktree_path == project_path simulates the init/spec_generator path.
        await sync_project_claude_md(git_repo, git_repo)

        # Content untouched.
        assert (
            git_repo / "CLAUDE.md"
        ).read_text() == "operator's real root CLAUDE.md\n"
        # No skip-worktree applied — operator can still edit + commit normally.
        result = subprocess.run(
            ["git", "ls-files", "-v", "CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.startswith("H ")

    async def test_linked_worktree_info_exclude_is_shared(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """``info/exclude`` is shared between the main repo and any linked
        worktrees created by ``git worktree add`` — git has no per-worktree
        exclude file. ``git rev-parse --git-path info/exclude`` returns the
        main repo's path from both.

        Acceptable concession for jig: writing ``CLAUDE.md`` to the exclude
        from a linked worktree also makes the main checkout ignore it.
        Operators rarely want to track a root CLAUDE.md anyway (and can
        edit info/exclude by hand if they do). The important property is
        that ``.git`` in a linked worktree is a FILE not a directory, so
        the original `<worktree>/.git/info/exclude` path would FAIL —
        resolving via ``git rev-parse --git-path`` is required."""
        main_exclude_path = _resolve_exclude_path(git_repo)
        main_exclude_before = (
            main_exclude_path.read_text() if main_exclude_path.is_file() else ""
        )
        assert "CLAUDE.md" not in main_exclude_before.splitlines()

        subprocess.run(
            ["git", "worktree", "add", "../linked", "-b", "linked-branch"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        linked = git_repo.parent / "linked"
        try:
            # .git is a file in linked worktrees, not a directory. This is the
            # property that breaks naive `<worktree>/.git/info/exclude` access.
            assert (linked / ".git").is_file()

            project = _make_project_with_claude_md(tmp_path, "linked content\n")
            await sync_project_claude_md(linked, project)

            # rev-parse from the linked worktree resolves to the shared exclude.
            linked_exclude = _resolve_exclude_path(linked)
            assert linked_exclude.resolve() == main_exclude_path.resolve()

            # /CLAUDE.md (anchored) appears exactly once.
            lines = linked_exclude.read_text().splitlines()
            assert lines.count("/CLAUDE.md") == 1

            # git status in the linked worktree is clean.
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=linked,
                capture_output=True,
                text=True,
                check=True,
            )
            assert "CLAUDE.md" not in status.stdout
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(linked)],
                cwd=git_repo,
                capture_output=True,
            )
