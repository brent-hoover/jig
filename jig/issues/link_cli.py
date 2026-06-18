from __future__ import annotations

from pathlib import Path

import click

from jig.issues.cli_support import path_option, run, service


@click.command("link")
@path_option
@click.argument("ref")
@click.option("--blocks", multiple=True)
@click.option("--blocked-by", "blocked_by", multiple=True)
@click.option("--parent", default=None)
@click.option(
    "--remove", is_flag=True, help="Remove the given edges instead of adding."
)
def link_cmd(
    path: Path | None,
    ref: str,
    blocks: tuple[str, ...],
    blocked_by: tuple[str, ...],
    parent: str | None,
    remove: bool,
) -> None:
    """Add or remove dependency / parent edges on an issue."""
    svc = service(path)
    try:
        ticket = run(
            svc.link(
                ref,
                blocks=list(blocks) or None,
                blocked_by=list(blocked_by) or None,
                parent=parent,
                remove=remove,
            )
        )
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"updated {ticket.key}")
