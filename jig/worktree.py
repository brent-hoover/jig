"""Git worktree management for agent isolation."""

import asyncio
from pathlib import Path

from jig.models import MergeStrategy


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
    ticket_id: str,
    base_branch: str,
) -> Path:
    """Create a git worktree for a ticket.

    Returns the path to the worktree directory.
    """
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"

    # Ensure at least one commit exists (worktrees require a valid ref)
    try:
        await _run_git(project_path, "rev-parse", "HEAD")
    except RuntimeError:
        await _run_git(
            project_path,
            "commit", "--allow-empty", "-m", "chore: initialize repository",
        )

    # If the branch already exists (leftover from a previous run), delete it
    # before creating the worktree so `-b` doesn't fail.
    try:
        await _run_git(project_path, "rev-parse", "--verify", branch_name)
        # Branch exists — remove it
        await _run_git(project_path, "branch", "-D", branch_name)
    except RuntimeError:
        pass  # Branch doesn't exist — good

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
    project_path: Path, ticket_id: str, *, keep_branch: bool = False,
) -> None:
    """Remove a git worktree, optionally preserving its branch for merge."""
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"
    await _run_git(project_path, "worktree", "remove", str(worktree_path), "--force")
    if not keep_branch:
        try:
            await _run_git(project_path, "branch", "-D", branch_name)
        except RuntimeError:
            pass  # Branch may already be deleted


async def _run_cmd(cwd: Path, *args: str) -> str:
    """Run an arbitrary command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def merge_ticket(
    project_path: Path,
    ticket_id: str,
    base_branch: str,
    strategy: MergeStrategy,
) -> str:
    """Merge the ticket branch into the base branch.

    Returns a short description of what was done.
    """
    source_branch = f"jig/{ticket_id}"

    if strategy == MergeStrategy.FEATURE_BRANCH:
        feature_branch = f"feature/{ticket_id}"
        try:
            await _run_git(project_path, "branch", "-D", feature_branch)
        except RuntimeError:
            pass
        await _run_git(project_path, "branch", feature_branch, source_branch)
        return f"Created feature branch: {feature_branch}"

    if strategy == MergeStrategy.PR:
        feature_branch = f"feature/{ticket_id}"
        try:
            await _run_git(project_path, "branch", "-D", feature_branch)
        except RuntimeError:
            pass
        await _run_git(project_path, "branch", feature_branch, source_branch)
        try:
            await _run_git(project_path, "push", "-u", "origin", feature_branch)
            stdout = await _run_cmd(
                project_path,
                "gh", "pr", "create",
                "--base", base_branch,
                "--head", feature_branch,
                "--title", f"jig: {ticket_id}",
                "--body", f"Automated PR for ticket {ticket_id}",
            )
            return f"Created PR: {stdout}"
        except (RuntimeError, FileNotFoundError) as e:
            return f"Created feature branch: {feature_branch} (PR failed: {e})"

    # Direct or squash merge — stash any dirty state in the main repo first
    stashed = False
    try:
        status = await _run_git(project_path, "status", "--porcelain")
        if status.strip():
            await _run_git(project_path, "stash", "push", "-m", f"jig: pre-merge {ticket_id}")
            stashed = True
    except RuntimeError:
        pass

    try:
        await _run_git(project_path, "checkout", base_branch)
    except RuntimeError:
        if stashed:
            try:
                await _run_git(project_path, "stash", "pop")
            except RuntimeError:
                pass
        raise

    try:
        if strategy == MergeStrategy.SQUASH:
            await _run_git(project_path, "merge", "--squash", source_branch)
            await _run_git(project_path, "commit", "-m", f"feat: {ticket_id}")
            result = f"Squash-merged {source_branch} into {base_branch}"
        else:
            # MergeStrategy.DIRECT
            await _run_git(project_path, "merge", source_branch, "-m", f"Merge {ticket_id}")
            result = f"Merged {source_branch} into {base_branch}"
    finally:
        if stashed:
            try:
                await _run_git(project_path, "stash", "pop")
            except RuntimeError:
                pass
    return result
