# tests/test_worktree_per_ticket.py
import subprocess
from pathlib import Path

import pytest

from jig.worktree import create_worktree, remove_worktree


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_create_worktree_per_ticket(tmp_path: Path) -> None:
    _init_git(tmp_path)
    wt = await create_worktree(
        project_path=tmp_path, ticket_id="T-42", base_branch="main",
    )
    assert wt == tmp_path / ".jig" / "worktrees" / "T-42"
    assert wt.is_dir()
    # Branch was created
    result = subprocess.run(
        ["git", "branch", "--list", "jig/T-42"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert "jig/T-42" in result.stdout


@pytest.mark.asyncio
async def test_remove_worktree_per_ticket(tmp_path: Path) -> None:
    _init_git(tmp_path)
    wt = await create_worktree(
        project_path=tmp_path, ticket_id="T-42", base_branch="main",
    )
    assert wt.is_dir()
    await remove_worktree(project_path=tmp_path, ticket_id="T-42")
    assert not wt.is_dir()
