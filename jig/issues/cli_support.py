from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import TypeVar

import click

from jig.issues.discovery import find_project_root
from jig.issues.service import IssueService

T = TypeVar("T")

path_option = click.option(
    "--path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Project root. Default: walk up from the current directory.",
)


def service(path: Path | None) -> IssueService:
    if path is not None:
        return IssueService(path)
    try:
        return IssueService(find_project_root(Path.cwd()))
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def run(coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)
