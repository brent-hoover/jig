"""`jig issue` CLI — a thin adapter over IssueService.

Each command resolves the project (walk up from cwd, or an explicit --path),
builds an IssueService, runs one async operation, and prints a terse result.
Contract/lookup failures surface as ClickExceptions (non-zero exit, no write).
"""

import asyncio
import sys
from pathlib import Path
from typing import Awaitable, TypeVar

import click

from jig.issues.discovery import find_project_root
from jig.issues.service import IssueService
from jig.thread import entry_content

T = TypeVar("T")

_path_option = click.option(
    "--path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Project root. Default: walk up from the current directory.",
)


def _service(path: Path | None) -> IssueService:
    if path is not None:
        root = path
    else:
        try:
            root = find_project_root(Path.cwd())
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc
    return IssueService(root)


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def _read_body(body: str | None, body_file: str | None) -> str:
    if body is not None:
        return body
    if body_file is not None:
        return sys.stdin.read() if body_file == "-" else Path(body_file).read_text()
    edited = click.edit()
    if not edited:
        raise click.ClickException(
            "no body provided (use --body, --body-file, '-' for stdin, or $EDITOR)"
        )
    return edited


@click.group("issue")
def issue_group() -> None:
    """Create and manage issues in this project's store."""


@issue_group.command("create")
@_path_option
@click.option("--title", required=True)
@click.option("--type", "work_type", required=True, help="Work type, e.g. feature.")
@click.option("--size", default="m", show_default=True)
@click.option("--body", default=None, help="Issue body. Must contain an AC section.")
@click.option(
    "--body-file", default=None, help="Read body from a file, or '-' for stdin."
)
@click.option("--created-by", default="cli", show_default=True)
@click.option("--label", "labels", multiple=True)
@click.option("--parent", default=None)
@click.option("--blocked-by", "blocked_by", multiple=True)
def create(
    path: Path | None,
    title: str,
    work_type: str,
    size: str,
    body: str | None,
    body_file: str | None,
    created_by: str,
    labels: tuple[str, ...],
    parent: str | None,
    blocked_by: tuple[str, ...],
) -> None:
    """Create an issue. It lands as PROPOSED until approved."""
    description = _read_body(body, body_file)
    svc = _service(path)
    try:
        ticket = _run(
            svc.create(
                title=title,
                work_type=work_type,
                description=description,
                size=size,
                created_by=created_by,
                labels=list(labels),
                parent=parent,
                blocked_by=list(blocked_by) or None,
            )
        )
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{ticket.key}  [{ticket.status.value}]  {ticket.title}")


@issue_group.command("list")
@_path_option
@click.option("--status", default=None)
@click.option("--type", "work_type", default=None)
@click.option("--label", default=None)
@click.option("--assignee", default=None)
def list_cmd(
    path: Path | None,
    status: str | None,
    work_type: str | None,
    label: str | None,
    assignee: str | None,
) -> None:
    """List issues, optionally filtered."""
    svc = _service(path)
    tickets = _run(
        svc.list(status=status, work_type=work_type, label=label, assignee=assignee)
    )
    for t in tickets:
        click.echo(f"{t.key}\t{t.status.value}\t{t.work_type.value}\t{t.title}")


@issue_group.command("show")
@_path_option
@click.argument("ref")
def show(path: Path | None, ref: str) -> None:
    """Show one issue and its comments. REF is a jig-N key or UUID."""
    svc = _service(path)

    async def _go():
        ticket = await svc.get(ref)
        return ticket, await svc.comments(ref)

    try:
        ticket, comments = _run(_go())
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"{ticket.key}  [{ticket.status.value}]  {ticket.work_type.value}/{ticket.size.value}"
    )
    click.echo(f"title: {ticket.title}")
    if ticket.assignee:
        click.echo(f"assignee: {ticket.assignee}")
    if ticket.blocked_by:
        click.echo(f"blocked_by: {', '.join(ticket.blocked_by)}")
    if ticket.blocks:
        click.echo(f"blocks: {', '.join(ticket.blocks)}")
    if ticket.labels:
        click.echo(f"labels: {', '.join(ticket.labels)}")
    click.echo("")
    click.echo(ticket.description)
    for c in comments:
        click.echo(f"  - ({c.author}) {entry_content(c)}")


@issue_group.command("update")
@_path_option
@click.argument("ref")
@click.option("--status", default=None)
@click.option("--assignee", default=None)
@click.option("--title", default=None)
@click.option("--add-label", "add_label", multiple=True)
@click.option("--remove-label", "remove_label", multiple=True)
def update(
    path: Path | None,
    ref: str,
    status: str | None,
    assignee: str | None,
    title: str | None,
    add_label: tuple[str, ...],
    remove_label: tuple[str, ...],
) -> None:
    """Update an issue's fields. Cannot promote PROPOSED -> OPEN (use approve)."""
    svc = _service(path)

    async def _go():
        fields: dict = {}
        if status is not None:
            fields["status"] = status
        if assignee is not None:
            fields["assignee"] = assignee
        if title is not None:
            fields["title"] = title
        if add_label or remove_label:
            current = list((await svc.get(ref)).labels)
            for label in add_label:
                if label not in current:
                    current.append(label)
            for label in remove_label:
                if label in current:
                    current.remove(label)
            fields["labels"] = current
        return await svc.update(ref, **fields)

    try:
        ticket = _run(_go())
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{ticket.key} -> {ticket.status.value}")


@issue_group.command("approve")
@_path_option
@click.argument("ref")
def approve(path: Path | None, ref: str) -> None:
    """Approve a PROPOSED issue for work (PROPOSED -> OPEN)."""
    svc = _service(path)
    try:
        ticket = _run(svc.approve(ref))
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{ticket.key} -> {ticket.status.value}")


@issue_group.command("close")
@_path_option
@click.argument("ref")
def close(path: Path | None, ref: str) -> None:
    """Close an issue."""
    svc = _service(path)
    try:
        ticket = _run(svc.close(ref))
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{ticket.key} -> {ticket.status.value}")


@issue_group.command("comment")
@_path_option
@click.argument("ref")
@click.option("--body", default=None)
@click.option(
    "--body-file", default=None, help="Read body from a file, or '-' for stdin."
)
def comment(
    path: Path | None, ref: str, body: str | None, body_file: str | None
) -> None:
    """Add a comment to an issue."""
    text = _read_body(body, body_file)
    svc = _service(path)
    try:
        cid = _run(svc.comment(ref, text))
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"comment {cid} added to {ref}")


@issue_group.command("link")
@_path_option
@click.argument("ref")
@click.option("--blocks", multiple=True)
@click.option("--blocked-by", "blocked_by", multiple=True)
@click.option("--parent", default=None)
@click.option(
    "--remove", is_flag=True, help="Remove the given edges instead of adding."
)
def link(
    path: Path | None,
    ref: str,
    blocks: tuple[str, ...],
    blocked_by: tuple[str, ...],
    parent: str | None,
    remove: bool,
) -> None:
    """Add or remove dependency / parent edges on an issue."""
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
