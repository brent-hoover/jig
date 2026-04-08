"""Jig CLI."""

from pathlib import Path

import click

from jig.persistence import init_project, list_issues, load_project, save_default_agent_types


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
