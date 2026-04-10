"""Jig MCP tool handler functions.

Core logic for MCP tools. Handlers are plain async functions for testability.
Server factories that wrap these with @tool decorators live in mcp_server.py.
"""

import json
from pathlib import Path

from jig.store import Message, MessageBus, MessageType
from jig.models import (
    AgentMessage,
    CompletionReport,
    CompletionStatus,
    MessageDirection,
)
from jig.persistence import (
    load_task, save_task,
    load_agent_instance, save_agent_instance,
    load_issue, load_workflow, list_agent_instances,
)


async def handle_send_message(
    bus: MessageBus,
    issue_id: str,
    sender_id: str,
    args: dict,
) -> str:
    """Send a structured AgentMessage via the bus.

    AgentMessage is a domain concept with its own fields (direction, content,
    subject topic). We wrap it in a store-level Message so it can traverse
    the topic-partitioned bus: the AgentMessage payload is serialized into
    the store Message's ``payload`` field, and the bus ``topic`` is set to
    the issue_id so subscribers to an issue see all messages for it.
    """
    agent_msg = AgentMessage(
        sender_id=sender_id,
        recipient_id=args["recipient_id"],
        direction=MessageDirection(args["direction"]),
        topic=args["topic"],
        content=args["content"],
        correlation_id=args.get("correlation_id"),
    )
    store_type = (
        MessageType.QUESTION
        if agent_msg.direction == MessageDirection.REQUEST
        else MessageType.ANSWER
    )
    msg = Message(
        sender=sender_id,
        to=agent_msg.recipient_id,
        type=store_type,
        payload=agent_msg.model_dump(mode="json"),
        correlation_id=agent_msg.correlation_id,
        topic=issue_id,
    )
    await bus.publish(msg)
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


async def handle_check_messages(
    bus: MessageBus,
    issue_id: str,
    agent_id: str,
) -> list[AgentMessage]:
    """Check for and return any pending messages for this agent.

    Subscribes to the issue topic (if not already) and drains messages
    addressed to this agent (or broadcast). Store-level Messages are
    decoded back into their original AgentMessage payloads.
    """
    queue = await _get_or_create_subscription(bus, issue_id, agent_id)
    messages: list[AgentMessage] = []
    while not queue.empty():
        item = await queue.get()
        if not isinstance(item, Message):
            continue
        if item.to not in (agent_id, "broadcast"):
            continue
        try:
            messages.append(AgentMessage.model_validate(item.payload))
        except Exception:
            continue
    return messages


# Per-(bus, issue, agent) subscription cache so repeated calls to
# handle_check_messages see the same queue and don't drop messages.
_subscription_cache: dict[tuple[int, str, str], object] = {}


async def _get_or_create_subscription(
    bus: MessageBus, issue_id: str, agent_id: str
):
    key = (id(bus), issue_id, agent_id)
    queue = _subscription_cache.get(key)
    if queue is None:
        queue = await bus.subscribe(issue_id)
        _subscription_cache[key] = queue
    return queue


async def handle_save_memory(
    project_path: Path,
    agent_id: str,
    args: dict,
) -> str:
    """Save a memory entry for an agent."""
    instance = load_agent_instance(project_path, agent_id)
    instance.memory.append(args["entry"])
    save_agent_instance(project_path, instance)
    return f"Memory saved for {agent_id}"


async def handle_load_memory(
    project_path: Path,
    agent_id: str,
) -> list[str]:
    """Load all memory entries for an agent."""
    instance = load_agent_instance(project_path, agent_id)
    return instance.memory


async def handle_get_workflow_status(
    project_path: Path,
    issue_id: str,
    workflow_name: str = "default",
) -> dict:
    """Get the current workflow status for an issue."""
    issue = load_issue(project_path, issue_id)
    workflow = load_workflow(project_path, workflow_name)
    agents = list_agent_instances(project_path)

    return {
        "issue_id": issue.id,
        "issue_title": issue.title,
        "issue_status": issue.status.value,
        "current_phase": issue.current_phase,
        "phases": [
            {"name": p.name, "role": p.role}
            for p in workflow.phases
        ],
        "agents": [
            {"id": a.id, "agent_type": a.agent_type, "status": a.status.value, "current_task_id": a.current_task_id}
            for a in agents
        ],
    }
