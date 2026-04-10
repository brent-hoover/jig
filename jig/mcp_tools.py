"""Jig MCP tool handler functions.

Core logic for MCP tools. Handlers are plain async functions for testability.
Server factories that wrap these with @tool decorators live in mcp_server.py.
"""

import json
from pathlib import Path

from jig.bus import MessageBus
from jig.models import (
    AgentMessage,
    CompletionReport,
    CompletionStatus,
    MessageDirection,
)
from jig.persistence import load_task, save_task


async def handle_send_message(
    bus: MessageBus,
    issue_id: str,
    sender_id: str,
    args: dict,
) -> str:
    """Send a structured AgentMessage via the bus."""
    msg = AgentMessage(
        sender_id=sender_id,
        recipient_id=args["recipient_id"],
        direction=MessageDirection(args["direction"]),
        topic=args["topic"],
        content=args["content"],
        correlation_id=args.get("correlation_id"),
    )
    await bus.publish(issue_id, msg)
    return f"Message sent to {args['recipient_id']}"


async def handle_report_completion(
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_id: str,
    args: dict,
) -> str:
    """File a structured CompletionReport and update the task."""
    report = CompletionReport(
        agent_id=agent_id,
        task_id=task_id,
        status=CompletionStatus(args["status"]),
        summary=args.get("summary", ""),
        reason=args.get("reason", ""),
        artifacts=args.get("artifacts", []),
        needs_from=args.get("needs_from"),
        question=args.get("question"),
    )

    task = load_task(project_path, issue_id, task_id)
    task.completion_state = report.status.value
    task.completion_reason = report.reason
    save_task(project_path, issue_id, task)

    return f"Completion reported: {report.status.value}"


async def handle_request_context(
    worktree_path: Path,
    args: dict,
) -> str:
    """Read a file from the worktree."""
    file_path = worktree_path / args["path"]
    if not file_path.is_file():
        return f"File not found: {args['path']}"
    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading {args['path']}: {e}"
