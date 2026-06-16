from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import click

from jig.issues.discovery import find_project_root
from jig.issues.service import IssueService

T = TypeVar("T")

_path_option = click.option(
    "--path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Project root. Default: walk up from the current directory.",
)


def _service(path: Path | None) -> IssueService:
    if path is not None:
        return IssueService(path)
    try:
        return IssueService(find_project_root(Path.cwd()))
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@click.command("link")
@_path_option
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
    svc = _service(path)
    try:
        ticket = _run(
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
