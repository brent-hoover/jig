"""Prompt-style eval CLI.

Step-1 scaffold: defines the three subcommands so ``--help`` is discoverable.
Each subcommand raises a typed "not yet implemented" error until the
corresponding plan step lands.
"""

import click


@click.group()
def cli() -> None:
    """Compare hand-authored prompt variants on hidden-test tasks."""


@cli.command()
def run() -> None:
    """Run cells until each has the requested sample count."""
    raise click.ClickException("not yet implemented (plan step 11)")


@cli.command()
def report() -> None:
    """Aggregate persisted runs and print a comparison table."""
    raise click.ClickException("not yet implemented (plan step 12)")


@cli.command()
def rescore() -> None:
    """Re-judge persisted code against a different rubric version."""
    raise click.ClickException("not yet implemented (plan step 13)")
