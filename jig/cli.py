"""Jig CLI."""

import asyncio
from pathlib import Path

import click

from jig.models import Issue
from jig.orchestrator import Orchestrator, OrchestratorPaused, OrchestratorFailed
from jig.persistence import init_project, list_issues, load_issue, load_project, save_default_agent_types, save_default_workflow, save_issue
from jig.worktree import remove_worktree


@click.group()
def cli() -> None:
    """Jig: Agent harness for Claude Code."""


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--branch", default="main", help="Default branch name.")
def init(path: Path, branch: str) -> None:
    """Initialize .jig/ in a project."""
    try:
        init_project(path, default_branch=branch)
        save_default_agent_types(path)
        save_default_workflow(path)
        click.echo(f"Initialized Jig in {path / '.jig'}")
    except FileExistsError:
        raise click.ClickException(f"Already initialized: {path / '.jig'}")
    except ValueError as e:
        raise click.ClickException(str(e))


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def status(path: Path) -> None:
    """Show current workflow state."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    config = load_project(path)
    issues = list_issues(path)

    click.echo(f"Project: {config.repo_path}")
    click.echo(f"Branch:  {config.default_branch}")
    click.echo()

    if not issues:
        click.echo("No issues.")
        return

    click.echo(f"{'ID':<15} {'Title':<30} {'Status':<15} {'Phase':<15}")
    click.echo("-" * 75)
    for issue in issues:
        phase = issue.current_phase or "-"
        click.echo(f"{issue.id:<15} {issue.title:<30} {issue.status.value:<15} {phase:<15}")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--issue-id", required=True, help="Unique issue identifier.")
@click.option("--title", default=None, help="Issue title (required for new issues).")
def start(path: Path, issue_id: str, title: str | None) -> None:
    """Start a workflow for an issue."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    # Create or load issue
    try:
        issue = load_issue(path, issue_id)
        click.echo(f"Resuming issue: {issue.id} - {issue.title}")
    except FileNotFoundError:
        if not title:
            raise click.ClickException("--title is required for new issues.")
        issue = Issue(id=issue_id, title=title)
        save_issue(path, issue)
        click.echo(f"Created issue: {issue.id} - {issue.title}")

    click.echo(f"Starting workflow for {issue_id}...")

    orchestrator = Orchestrator(path, issue_id)
    try:
        asyncio.run(orchestrator.run())
        click.echo(f"Workflow completed for {issue_id}.")
    except OrchestratorPaused as e:
        click.echo(f"Workflow paused: {e}")
        click.echo("Resolve the issue and run 'jig start' again to resume.")
    except OrchestratorFailed as e:
        raise click.ClickException(f"Workflow failed: {e}")
    except KeyboardInterrupt:
        click.echo("\nWorkflow interrupted. Run 'jig start' again to resume.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--issue-id", required=True, help="Issue to validate.")
def validate(path: Path, issue_id: str) -> None:
    """Validate an issue and clean up worktrees."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    try:
        issue = load_issue(path, issue_id)
    except FileNotFoundError:
        raise click.ClickException(f"Issue '{issue_id}' not found.")

    # Clean up worktrees for this issue
    worktrees_dir = jig_dir / "worktrees" / issue_id
    if worktrees_dir.is_dir():
        for phase_dir in worktrees_dir.iterdir():
            if phase_dir.is_dir():
                try:
                    asyncio.run(remove_worktree(path, issue_id, phase_dir.name))
                    click.echo(f"  Removed worktree: {phase_dir.name}")
                except RuntimeError:
                    click.echo(f"  Warning: could not remove worktree {phase_dir.name}")

    click.echo(f"Issue {issue_id} validated.")
