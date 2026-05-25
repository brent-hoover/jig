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


class TestSyncProjectClaudeMd:
    def test_copies_project_source_into_worktree(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "project content\n")
        sync_project_claude_md(git_repo, project)
        assert (git_repo / "CLAUDE.md").read_text() == "project content\n"

    def test_writes_stub_when_project_source_missing(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, None)
        sync_project_claude_md(git_repo, project)
        content = (git_repo / "CLAUDE.md").read_text()
        assert "jig-managed" in content
        assert ".jig/CLAUDE.md" in content

    def test_skip_worktree_set_when_claude_md_tracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        # Commit a CLAUDE.md so it's tracked in the index.
        (git_repo / "CLAUDE.md").write_text("original\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add CLAUDE.md"], cwd=git_repo, check=True
        )

        project = _make_project_with_claude_md(tmp_path, "overwritten\n")
        sync_project_claude_md(git_repo, project)

        result = subprocess.run(
            ["git", "ls-files", "-v", "CLAUDE.md"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        # Lowercase letter prefix indicates skip-worktree bit set (S/s).
        assert result.stdout.startswith(("S ", "s "))

    def test_info_exclude_used_when_claude_md_untracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "untracked content\n")
        sync_project_claude_md(git_repo, project)

        exclude_path = git_repo / ".git" / "info" / "exclude"
        assert "CLAUDE.md" in exclude_path.read_text().splitlines()

    def test_info_exclude_is_idempotent(self, git_repo: Path, tmp_path: Path) -> None:
        project = _make_project_with_claude_md(tmp_path, "content\n")
        for _ in range(3):
            sync_project_claude_md(git_repo, project)

        exclude_path = git_repo / ".git" / "info" / "exclude"
        lines = exclude_path.read_text().splitlines()
        assert lines.count("CLAUDE.md") == 1

    def test_git_status_clean_after_sync_untracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        project = _make_project_with_claude_md(tmp_path, "content\n")
        sync_project_claude_md(git_repo, project)

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "CLAUDE.md" not in result.stdout

    def test_git_status_clean_after_sync_tracked(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        (git_repo / "CLAUDE.md").write_text("original\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add CLAUDE.md"], cwd=git_repo, check=True
        )

        project = _make_project_with_claude_md(tmp_path, "overwritten\n")
        sync_project_claude_md(git_repo, project)

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "CLAUDE.md" not in result.stdout

    def test_project_root_spawn_is_a_noop(self, git_repo: Path) -> None:
        """Some jig spawns (init/spec-generator/concierge) use the project root
        AS their worktree. Sync must skip rather than clobber the operator's
        checked-in root CLAUDE.md."""
        (git_repo / "CLAUDE.md").write_text("operator's real root CLAUDE.md\n")
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add CLAUDE.md"], cwd=git_repo, check=True
        )

        # worktree_path == project_path simulates the init/spec_generator path.
        sync_project_claude_md(git_repo, git_repo)

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

    def test_linked_worktree_info_exclude_is_per_worktree(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """In a real linked worktree (created by ``git worktree add``),
        ``.git`` is a file pointing at the per-worktree gitdir, and
        ``info/exclude`` is per-worktree. Resolving via
        ``git rev-parse --git-path info/exclude`` must land in the linked
        worktree's exclude, not the main repo's."""
        subprocess.run(
            ["git", "worktree", "add", "../linked", "-b", "linked-branch"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        linked = git_repo.parent / "linked"
        try:
            # .git is a file in linked worktrees, not a directory.
            assert (linked / ".git").is_file()

            project = _make_project_with_claude_md(tmp_path, "linked content\n")
            sync_project_claude_md(linked, project)

            # The linked worktree's per-worktree info/exclude should now contain
            # CLAUDE.md, NOT the main repo's exclude.
            git_dir_result = subprocess.run(
                ["git", "rev-parse", "--git-path", "info/exclude"],
                cwd=linked,
                capture_output=True,
                text=True,
                check=True,
            )
            exclude_relpath = git_dir_result.stdout.strip()
            exclude_path = Path(exclude_relpath)
            if not exclude_path.is_absolute():
                exclude_path = linked / exclude_path
            assert "CLAUDE.md" in exclude_path.read_text().splitlines()

            # And git status in the linked worktree is clean.
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
