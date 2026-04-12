"""MCP server factory for agent ticket tools."""

import json
from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig import ticket_mcp
from jig.models import AgentTypeConfig
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore


def create_agent_mcp_server(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    memory: MemoryStore,
    bus: MessageBus,
    agent_role: str,
    agent_cfg: AgentTypeConfig,
    worktree_path: Path,
    valid_roles: frozenset[str] = frozenset(),
    package_manager: str = "",
):
    """Create a Jig MCP server for a worker agent exposing 9 ticket-era tools."""

    # Allowed assignees: known roles + orchestrator + user
    _allowed_assignees = valid_roles | {"orchestrator", "user"}

    def _check_assignee(assignee: str | None) -> None:
        if assignee and _allowed_assignees and assignee not in _allowed_assignees:
            raise ValueError(
                f"Unknown role {assignee!r}. Valid roles: {sorted(valid_roles)}"
            )

    @tool(
        "create_ticket",
        "Create a new ticket",
        {"type": str, "title": str, "description": str, "assignee": str, "parent_id": str, "labels": list},
    )
    async def create_ticket(args):
        _check_assignee(args.get("assignee"))
        ticket_id = await ticket_mcp.handle_create_ticket(
            tickets=tickets, comments=comments, bus=bus, sender=agent_role, args=args
        )
        return {"content": [{"type": "text", "text": ticket_id}]}

    @tool(
        "read_ticket",
        "Read a ticket by ID",
        {"ticket_id": str},
    )
    async def read_ticket(args):
        t = await ticket_mcp.handle_read_ticket(tickets=tickets, ticket_id=args["ticket_id"])
        return {"content": [{"type": "text", "text": t.model_dump_json()}]}

    @tool(
        "update_ticket",
        "Update fields on an existing ticket",
        {"ticket_id": str, "status": str, "description": str, "assignee": str, "labels": list},
    )
    async def update_ticket(args):
        _check_assignee(args.get("assignee"))
        updated = await ticket_mcp.handle_update_ticket(
            tickets=tickets, comments=comments, bus=bus, sender=agent_role, args=args
        )
        return {"content": [{"type": "text", "text": updated.model_dump_json()}]}

    @tool(
        "comment_on_ticket",
        "Post a comment or decision on a ticket",
        {"ticket_id": str, "content": str, "kind": str},
    )
    async def comment_on_ticket(args):
        cid = await ticket_mcp.handle_comment_on_ticket(
            tickets=tickets,
            comments=comments,
            bus=bus,
            sender=agent_role,
            sender_cfg=agent_cfg,
            args=args,
        )
        return {"content": [{"type": "text", "text": cid}]}

    @tool(
        "ask_question",
        "Ask the operator a question. Posts question comment(s) and pauses the ticket (needs_info). "
        "The orchestrator will resume you once the operator answers. "
        "Use this instead of creating question tickets or manually setting needs_info.",
        {"ticket_id": str, "question": str, "questions": list},
    )
    async def ask_question(args):
        result = await ticket_mcp.handle_ask_question(
            tickets=tickets, comments=comments, bus=bus, sender=agent_role, args=args
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "list_tickets",
        "List tickets with optional filters",
        {"type": str, "status": str, "assignee": str, "parent_id": str},
    )
    async def list_tickets(args):
        results = await ticket_mcp.handle_list_tickets(tickets=tickets, args=args)
        text = "\n".join(r.model_dump_json() for r in results)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_comments",
        "Read comments on a ticket",
        {"ticket_id": str, "kind": str},
    )
    async def read_comments(args):
        results = await ticket_mcp.handle_read_comments(
            comments=comments, ticket_id=args["ticket_id"], kind=args.get("kind")
        )
        text = "\n".join(r.model_dump_json() for r in results)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "commit_progress",
        "Commit current worktree progress and record it on a ticket. "
        "The message should describe WHAT changed (e.g. 'add CRUD endpoints for todos', "
        "'fix off-by-one in pagination logic'), not just repeat the ticket title.",
        {"ticket_id": str, "message": str},
    )
    async def commit_progress(args):
        result = await ticket_mcp.handle_commit_progress(
            tickets=tickets,
            comments=comments,
            bus=bus,
            sender=agent_role,
            worktree_path=worktree_path,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "record_learning",
        "Record a learning or lesson learned for your role",
        {"content": str},
    )
    async def record_learning(args):
        text = await ticket_mcp.handle_record_learning(
            memory=memory, role=agent_role, args=args
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "request_context",
        "Read a file from the worktree to get additional context",
        {"path": str},
    )
    async def request_context(args):
        text = await ticket_mcp.handle_request_context(
            worktree_path=worktree_path, args=args
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "add_dependency",
        "Add a package dependency to the project using the configured package manager. "
        "Use this instead of running install commands directly.",
        {"packages": list, "dev": bool},
    )
    async def add_dependency(args):
        result = await ticket_mcp.handle_add_dependency(
            worktree_path=worktree_path,
            package_manager=package_manager,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    all_tools = [
        create_ticket,
        read_ticket,
        update_ticket,
        comment_on_ticket,
        ask_question,
        list_tickets,
        read_comments,
        commit_progress,
        record_learning,
        request_context,
    ]
    if package_manager:
        all_tools.append(add_dependency)

    return create_sdk_mcp_server(
        name="jig",
        tools=all_tools,
    )
