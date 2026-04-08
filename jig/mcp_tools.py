"""Jig MCP tool handler functions.

These are the core logic functions called by the MCP server tools.
Separated from the server factory for testability.
"""

import json
from pathlib import Path

from jig.bus import MessageBus
from jig.models import Message, MessageType, CompletionState
from jig.persistence import save_task, load_task


async def handle_publish_message(
    bus: MessageBus,
    issue_id: str,
    sender: str,
    args: dict,
) -> str:
    """Publish a message to the bus. Returns confirmation text."""
    payload_str = args.get("payload", "{}")
    try:
        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
    except json.JSONDecodeError:
        payload = {}

    msg = Message(
        sender=sender,
        recipient=args["recipient"],
        type=MessageType(args["message_type"]),
        payload=payload,
    )
    await bus.publish(issue_id, msg)
    return f"Message sent to {args['recipient']}"


async def handle_report_completion(
    project_path: Path,
    issue_id: str,
    task_id: str,
    args: dict,
) -> str:
    """Update task completion state. Returns confirmation text."""
    task = load_task(project_path, issue_id, task_id)
    task.completion_state = CompletionState(args["status"])
    task.completion_reason = args.get("reason", "")
    save_task(project_path, issue_id, task)
    return f"Completion reported: {args['status']}"


async def handle_request_context(
    worktree_path: Path,
    args: dict,
) -> str:
    """Read a file from the worktree. Returns file contents or error."""
    file_path = worktree_path / args["path"]
    if not file_path.is_file():
        return f"File not found: {args['path']}"
    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading {args['path']}: {e}"
