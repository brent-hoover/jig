"""Jig CLI."""

from pathlib import Path

import click

from jig.persistence import init_project


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
        click.echo(f"Initialized Jig in {path / '.jig'}")
    except FileExistsError:
        raise click.ClickException(f"Already initialized: {path / '.jig'}")
    except ValueError as e:
        raise click.ClickException(str(e))
