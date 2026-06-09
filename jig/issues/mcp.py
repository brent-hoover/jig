"""Standalone stdio MCP server — the issue front door for non-jig agents.

External Claude Code agents (not spawned by jig's orchestrator) register this
in their own ``.mcp.json`` and create / read / link issues over the shared
``IssueService``. Unlike the in-process per-agent server (`mcp_server.py`),
this is a launchable stdio process.

It deliberately exposes **no** approve tool: promotion of a PROPOSED issue to
OPEN is operator-only. The ``issue_update`` tool also cannot bypass the gate —
the store rejects PROPOSED -> OPEN regardless of caller.

The tool bodies delegate to the module-level ``*_issue`` functions, which are
plain and directly testable.
"""

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from jig.issues.discovery import find_project_root
from jig.issues.service import IssueService
from jig.thread import ThreadEntry
from jig.ticket import Ticket


def _ticket_dict(t: Ticket) -> dict[str, Any]:
    return {
        "key": t.key,
        "id": t.id,
        "status": t.status.value,
        "work_type": t.work_type.value,
        "size": t.size.value,
        "title": t.title,
        "description": t.description,
        "assignee": t.assignee,
        "labels": list(t.labels),
        "blocked_by": list(t.blocked_by),
        "blocks": list(t.blocks),
        "parent_id": t.parent_id,
        "created_by": t.created_by,
    }


def _comment_dict(c: ThreadEntry) -> dict[str, Any]:
    return {
        "id": c.id,
        "author": c.author,
        "kind": c.kind,
        "text": getattr(c, "text", ""),
    }


async def create_issue(
    svc: IssueService,
    *,
    title: str,
    work_type: str,
    description: str,
    size: str = "m",
    created_by: str = "mcp",
    labels: list[str] | None = None,
    parent: str | None = None,
    blocked_by: list[str] | None = None,
) -> dict[str, Any]:
    ticket = await svc.create(
        title=title,
        work_type=work_type,
        description=description,
        size=size,
        created_by=created_by,
        labels=labels,
        parent=parent,
        blocked_by=blocked_by,
    )
    return _ticket_dict(ticket)


async def list_issues(
    svc: IssueService,
    *,
    status: str | None = None,
    work_type: str | None = None,
    label: str | None = None,
    assignee: str | None = None,
) -> list[dict[str, Any]]:
    tickets = await svc.list(
        status=status, work_type=work_type, label=label, assignee=assignee
    )
    return [_ticket_dict(t) for t in tickets]


async def show_issue(svc: IssueService, *, ref: str) -> dict[str, Any]:
    ticket = await svc.get(ref)
    comments = await svc.comments(ref)
    return {**_ticket_dict(ticket), "comments": [_comment_dict(c) for c in comments]}


async def update_issue(
    svc: IssueService,
    *,
    ref: str,
    status: str | None = None,
    assignee: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if assignee is not None:
        fields["assignee"] = assignee
    if title is not None:
        fields["title"] = title
    return _ticket_dict(await svc.update(ref, **fields))


async def close_issue(svc: IssueService, *, ref: str) -> dict[str, Any]:
    return _ticket_dict(await svc.close(ref))


async def comment_issue(
    svc: IssueService, *, ref: str, text: str, author: str = "mcp"
) -> dict[str, Any]:
    cid = await svc.comment(ref, text, author=author)
    return {"comment_id": cid, "ref": ref}


async def link_issue(
    svc: IssueService,
    *,
    ref: str,
    blocks: list[str] | None = None,
    blocked_by: list[str] | None = None,
    parent: str | None = None,
    remove: bool = False,
) -> dict[str, Any]:
    ticket = await svc.link(
        ref, blocks=blocks, blocked_by=blocked_by, parent=parent, remove=remove
    )
    return _ticket_dict(ticket)


def build_server(root: Path) -> FastMCP:
    """Build the stdio MCP server bound to a single project root.

    Note: no ``issue_approve`` tool — approval is operator-only.

    Each tool builds a FRESH ``IssueService`` per call. The service loads the
    JSONL stores into memory once per instance, so a single long-lived service
    would serve stale reads after the first call as the CLI / orchestrator /
    other processes mutate the store. A new instance per invocation reloads
    current on-disk state.
    """
    server = FastMCP("jig-issues")

    def svc() -> IssueService:
        return IssueService(root)

    @server.tool(name="issue_create")
    async def _create(
        title: str,
        work_type: str,
        description: str,
        size: str = "m",
        created_by: str = "mcp",
        labels: list[str] | None = None,
        parent: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an issue (lands as PROPOSED). Description must contain an AC section."""
        return await create_issue(
            svc(),
            title=title,
            work_type=work_type,
            description=description,
            size=size,
            created_by=created_by,
            labels=labels,
            parent=parent,
            blocked_by=blocked_by,
        )

    @server.tool(name="issue_list")
    async def _list(
        status: str | None = None,
        work_type: str | None = None,
        label: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        """List issues, optionally filtered."""
        return await list_issues(
            svc(), status=status, work_type=work_type, label=label, assignee=assignee
        )

    @server.tool(name="issue_show")
    async def _show(ref: str) -> dict[str, Any]:
        """Show one issue and its comments. ref is a jig-N key or UUID."""
        return await show_issue(svc(), ref=ref)

    @server.tool(name="issue_update")
    async def _update(
        ref: str,
        status: str | None = None,
        assignee: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Update issue fields. Cannot promote PROPOSED -> OPEN (operator-only)."""
        return await update_issue(
            svc(), ref=ref, status=status, assignee=assignee, title=title
        )

    @server.tool(name="issue_close")
    async def _close(ref: str) -> dict[str, Any]:
        """Close an issue."""
        return await close_issue(svc(), ref=ref)

    @server.tool(name="issue_comment")
    async def _comment(ref: str, text: str, author: str = "mcp") -> dict[str, Any]:
        """Add a comment to an issue."""
        return await comment_issue(svc(), ref=ref, text=text, author=author)

    @server.tool(name="issue_link")
    async def _link(
        ref: str,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        parent: str | None = None,
        remove: bool = False,
    ) -> dict[str, Any]:
        """Add or remove dependency / parent edges on an issue."""
        return await link_issue(
            svc(),
            ref=ref,
            blocks=blocks,
            blocked_by=blocked_by,
            parent=parent,
            remove=remove,
        )

    return server


def main() -> None:
    """Entry point: discover the project from cwd and serve over stdio."""
    root = find_project_root(Path.cwd())
    build_server(root).run()


if __name__ == "__main__":
    main()
