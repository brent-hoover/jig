import subprocess
from pathlib import Path

import pytest

from jig.worktree import create_worktree, remove_worktree, commit_worktree


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
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
            cwd=wt_path, capture_output=True, text=True, check=True,
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
            cwd=wt_path, capture_output=True, text=True, check=True,
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
