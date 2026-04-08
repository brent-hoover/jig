"""Git worktree management for agent isolation."""

import asyncio
from pathlib import Path


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def create_worktree(
    project_path: Path,
    issue_id: str,
    phase: str,
    base_branch: str,
) -> Path:
    """Create a git worktree for an agent.

    Returns the path to the worktree directory.
    """
    worktree_path = project_path / ".jig" / "worktrees" / issue_id / phase
    branch_name = f"jig/{issue_id}/{phase}"
    await _run_git(
        project_path,
        "worktree", "add", "-b", branch_name,
        str(worktree_path), base_branch,
    )
    return worktree_path


async def commit_worktree(worktree_path: Path, message: str) -> str | None:
    """Commit all changes in a worktree.

    Returns the commit SHA, or None if there were no changes.
    """
    await _run_git(worktree_path, "add", "-A")

    # Check if there's anything staged
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--cached", "--quiet",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode == 0:
        return None  # Nothing staged

    await _run_git(worktree_path, "commit", "-m", message)
    sha = await _run_git(worktree_path, "rev-parse", "HEAD")
    return sha


async def remove_worktree(
    project_path: Path,
    issue_id: str,
    phase: str,
) -> None:
    """Remove a git worktree and its branch."""
    worktree_path = project_path / ".jig" / "worktrees" / issue_id / phase
    branch_name = f"jig/{issue_id}/{phase}"
    await _run_git(project_path, "worktree", "remove", str(worktree_path), "--force")
    try:
        await _run_git(project_path, "branch", "-D", branch_name)
    except RuntimeError:
        pass  # Branch may already be deleted
