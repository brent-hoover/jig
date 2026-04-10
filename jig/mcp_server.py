"""MCP server factories for agent and orchestrator tools."""

import json
from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig.store import MessageBus
from jig.mcp_tools import (
    handle_check_messages,
    handle_get_workflow_status,
    handle_load_memory,
    handle_report_completion,
    handle_request_context,
    handle_save_memory,
    handle_send_message,
)


def create_agent_mcp_server(
    bus: MessageBus,
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_id: str,
    worktree_path: Path,
):
    """Create a Jig MCP server for a worker agent.

    Tools: send_message, check_messages, report_completion,
    request_context, save_memory, load_memory
    """

    @tool(
        "send_message",
        "Send a structured message to another agent",
        {"recipient_id": str, "direction": str, "topic": str, "content": str, "correlation_id": str},
    )
    async def send_message_tool(args):
        text = await handle_send_message(bus=bus, issue_id=issue_id, sender_id=agent_id, args=args)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "check_messages",
        "Check for pending messages from other agents",
        {},
    )
    async def check_messages_tool(args):
        messages = await handle_check_messages(bus=bus, issue_id=issue_id, agent_id=agent_id)
        if not messages:
            return {"content": [{"type": "text", "text": "No pending messages."}]}
        lines = [f"[{m.sender_id}] ({m.topic}) {m.content}" for m in messages]
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "report_completion",
        "Signal that your task is complete with a status, summary, and optional details",
        {"status": str, "summary": str, "reason": str, "needs_from": str, "question": str},
    )
    async def report_completion_tool(args):
        text = await handle_report_completion(
            project_path=project_path, issue_id=issue_id, task_id=task_id, agent_id=agent_id, args=args,
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "request_context",
        "Read a file from the project to get additional context",
        {"path": str},
    )
    async def request_context_tool(args):
        text = await handle_request_context(worktree_path=worktree_path, args=args)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "save_memory",
        "Save a lesson learned or important fact for future reference",
        {"entry": str},
    )
    async def save_memory_tool(args):
        text = await handle_save_memory(project_path=project_path, agent_id=agent_id, args=args)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "load_memory",
        "Load your saved memories from previous sessions",
        {},
    )
    async def load_memory_tool(args):
        memories = await handle_load_memory(project_path=project_path, agent_id=agent_id)
        if not memories:
            return {"content": [{"type": "text", "text": "No memories saved yet."}]}
        return {"content": [{"type": "text", "text": "\n".join(f"- {m}" for m in memories)}]}

    return create_sdk_mcp_server(
        "jig",
        tools=[
            send_message_tool,
            check_messages_tool,
            report_completion_tool,
            request_context_tool,
            save_memory_tool,
            load_memory_tool,
        ],
    )


def create_orchestrator_mcp_server(
    bus: MessageBus,
    project_path: Path,
    issue_id: str,
):
    """Create a Jig MCP server for the orchestrator agent.

    Coordination-only tools. No code tools (Read, Edit, Write, Bash).
    """
    orchestrator_id = "orchestrator"

    @tool(
        "get_workflow_status",
        "Get the current workflow status including phases and agent states",
        {},
    )
    async def get_workflow_status_tool(args):
        status = await handle_get_workflow_status(project_path=project_path, issue_id=issue_id)
        return {"content": [{"type": "text", "text": json.dumps(status, indent=2)}]}

    @tool(
        "send_message",
        "Send a message to an agent",
        {"recipient_id": str, "direction": str, "topic": str, "content": str},
    )
    async def send_message_tool(args):
        args.setdefault("direction", "request")
        text = await handle_send_message(bus=bus, issue_id=issue_id, sender_id=orchestrator_id, args=args)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "check_messages",
        "Check for pending messages from agents",
        {},
    )
    async def check_messages_tool(args):
        messages = await handle_check_messages(bus=bus, issue_id=issue_id, agent_id=orchestrator_id)
        if not messages:
            return {"content": [{"type": "text", "text": "No pending messages."}]}
        lines = [f"[{m.sender_id}] ({m.topic}) {m.content}" for m in messages]
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "save_memory",
        "Save a lesson learned from this exception for future reference",
        {"entry": str},
    )
    async def save_memory_tool(args):
        text = await handle_save_memory(project_path=project_path, agent_id=orchestrator_id, args=args)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "load_memory",
        "Load your saved memories from previous sessions",
        {},
    )
    async def load_memory_tool(args):
        memories = await handle_load_memory(project_path=project_path, agent_id=orchestrator_id)
        if not memories:
            return {"content": [{"type": "text", "text": "No memories saved yet."}]}
        return {"content": [{"type": "text", "text": "\n".join(f"- {m}" for m in memories)}]}

    return create_sdk_mcp_server(
        "jig-orchestrator",
        tools=[
            get_workflow_status_tool,
            send_message_tool,
            check_messages_tool,
            save_memory_tool,
            load_memory_tool,
        ],
    )
