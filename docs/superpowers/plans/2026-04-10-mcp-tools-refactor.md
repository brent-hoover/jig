# MCP Tools Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite MCP tool handlers to use the new protocol models (AgentMessage, CompletionReport) and add new tools (send_message, check_messages, save_memory, load_memory, get_workflow_status) so all agent data access goes through MCP.

**Architecture:** The existing `jig/mcp_tools.py` is refactored: `handle_publish_message` becomes `handle_send_message` using `AgentMessage`, `handle_report_completion` uses `CompletionReport`. New handlers are added for memory and workflow status. The `create_jig_mcp_server` factory is updated. A separate `create_orchestrator_mcp_server` factory is added with coordination-only tools.

**Tech Stack:** Python 3.12+, claude-agent-sdk, Pydantic, pytest

**Depends on:** Plan 5 (AgentMessage, CompletionReport, AgentInstance, AgentPool models + persistence)

---

## File Structure

```
jig/
├── mcp_tools.py         # (rewrite) Handler functions using protocol models
├── mcp_server.py         # (create) Server factories — agent MCP + orchestrator MCP
tests/
├── test_mcp_tools.py     # (rewrite) Tests for refactored handlers
├── test_mcp_server.py    # (create) Tests for server factories
```

Note: The current `mcp_tools.py` has both handlers AND the `create_jig_mcp_server` factory. We split: handlers stay in `mcp_tools.py`, factories move to `mcp_server.py`.

---

### Task 1: Refactor send_message Handler

**Files:**
- Modify: `jig/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

Replace `handle_publish_message` with `handle_send_message` that uses `AgentMessage`.

- [ ] **Step 1: Write failing tests**

Replace the `TestHandlePublishMessage` class in `tests/test_mcp_tools.py` with:

```python
import json
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import (
    AgentMessage,
    CompletionReport,
    CompletionStatus,
    Issue,
    MessageDirection,
    Task,
)
from jig.mcp_tools import handle_send_message, handle_report_completion, handle_request_context
from jig.persistence import save_issue, save_task, load_task


class TestHandleSendMessage:
    @pytest.fixture
    def bus(self, tmp_jig_project: Path) -> MessageBus:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        return MessageBus(tmp_jig_project)

    async def test_sends_structured_message(self, bus: MessageBus, tmp_jig_project: Path):
        queue = await bus.subscribe("issue-1", "test-1")
        result = await handle_send_message(
            bus=bus,
            issue_id="issue-1",
            sender_id="dev-1",
            args={
                "recipient_id": "test-1",
                "direction": "request",
                "topic": "api_design",
                "content": "What endpoints do we need?",
            },
        )
        assert "sent" in result.lower()
        msg = await queue.get()
        assert isinstance(msg, AgentMessage)
        assert msg.sender_id == "dev-1"
        assert msg.recipient_id == "test-1"
        assert msg.direction == MessageDirection.REQUEST
        assert msg.topic == "api_design"
        assert msg.content == "What endpoints do we need?"

    async def test_sends_response_with_correlation(self, bus: MessageBus, tmp_jig_project: Path):
        queue = await bus.subscribe("issue-1", "dev-1")
        result = await handle_send_message(
            bus=bus,
            issue_id="issue-1",
            sender_id="test-1",
            args={
                "recipient_id": "dev-1",
                "direction": "response",
                "topic": "api_design",
                "content": "GET /users and POST /users",
                "correlation_id": "msg-123",
            },
        )
        msg = await queue.get()
        assert msg.direction == MessageDirection.RESPONSE
        assert msg.correlation_id == "msg-123"

    async def test_broadcasts(self, bus: MessageBus, tmp_jig_project: Path):
        q1 = await bus.subscribe("issue-1", "agent-a")
        q2 = await bus.subscribe("issue-1", "agent-b")
        await handle_send_message(
            bus=bus,
            issue_id="issue-1",
            sender_id="dev-1",
            args={
                "recipient_id": "broadcast",
                "direction": "request",
                "topic": "status",
                "content": "Starting work",
            },
        )
        m1 = await q1.get()
        m2 = await q2.get()
        assert m1.content == "Starting work"
        assert m2.content == "Starting work"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleSendMessage -v`
Expected: FAIL with `ImportError` (handle_send_message doesn't exist yet)

- [ ] **Step 3: Implement handle_send_message**

In `jig/mcp_tools.py`, replace the entire file content with:

```python
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
```

Note: `handle_report_completion` now takes `agent_id` as a parameter and builds a full `CompletionReport`. The task's `completion_state` field stores the string value.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleSendMessage -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: replace handle_publish_message with handle_send_message using AgentMessage"
```

---

### Task 2: Refactor report_completion Handler

**Files:**
- Modify: `tests/test_mcp_tools.py`

The handler is already updated in Task 1. This task updates the tests.

- [ ] **Step 1: Replace TestHandleReportCompletion tests**

Replace the `TestHandleReportCompletion` class in `tests/test_mcp_tools.py`:

```python
class TestHandleReportCompletion:
    def _setup_issue_and_task(self, tmp_jig_project: Path) -> None:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(
            id="task-1",
            description="Do thing",
            acceptance_criteria="Done",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)

    async def test_reports_success_with_summary(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        result = await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            agent_id="dev-1",
            args={
                "status": "success",
                "summary": "Implemented auth module",
                "artifacts": ["src/auth.py", "tests/test_auth.py"],
            },
        )
        assert "success" in result.lower()
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == "success"

    async def test_reports_needs_info_with_question(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        result = await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            agent_id="dev-1",
            args={
                "status": "needs_info",
                "summary": "Cannot determine API schema",
                "reason": "No API spec available",
                "needs_from": "spec",
                "question": "What are the required endpoints?",
            },
        )
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == "needs_info"
        assert task.completion_reason == "No API spec available"

    async def test_reports_failed(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            agent_id="dev-1",
            args={
                "status": "failed",
                "summary": "Tests failed",
                "reason": "Import error in auth module",
            },
        )
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == "failed"
        assert task.completion_reason == "Import error in auth module"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_mcp_tools.py -v`
Expected: all passed (send_message tests + report_completion tests + request_context tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp_tools.py
git commit -m "test: update report_completion tests to use CompletionReport protocol"
```

---

### Task 3: Add check_messages Handler

**Files:**
- Modify: `jig/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_tools.py`:

```python
from jig.mcp_tools import handle_check_messages


class TestHandleCheckMessages:
    @pytest.fixture
    def bus(self, tmp_jig_project: Path) -> MessageBus:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        return MessageBus(tmp_jig_project)

    async def test_returns_empty_when_no_messages(self, bus: MessageBus):
        result = await handle_check_messages(
            bus=bus,
            issue_id="issue-1",
            agent_id="dev-1",
        )
        assert result == []

    async def test_returns_messages_for_agent(self, bus: MessageBus):
        # Subscribe first so messages get queued
        queue = await bus.subscribe("issue-1", "dev-1")

        # Send a message to dev-1
        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.REQUEST,
            topic="question",
            content="How should I test this?",
        )
        await bus.publish("issue-1", msg)

        result = await handle_check_messages(
            bus=bus,
            issue_id="issue-1",
            agent_id="dev-1",
        )
        assert len(result) == 1
        assert result[0].sender_id == "test-1"
        assert result[0].content == "How should I test this?"

    async def test_drains_queue(self, bus: MessageBus):
        queue = await bus.subscribe("issue-1", "dev-1")

        for i in range(3):
            msg = AgentMessage(
                sender_id="test-1",
                recipient_id="dev-1",
                direction=MessageDirection.REQUEST,
                topic="q",
                content=f"Message {i}",
            )
            await bus.publish("issue-1", msg)

        result = await handle_check_messages(
            bus=bus,
            issue_id="issue-1",
            agent_id="dev-1",
        )
        assert len(result) == 3

        # Queue should be empty now
        result2 = await handle_check_messages(
            bus=bus,
            issue_id="issue-1",
            agent_id="dev-1",
        )
        assert result2 == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleCheckMessages -v`
Expected: FAIL

- [ ] **Step 3: Implement handle_check_messages**

Append to `jig/mcp_tools.py`:

```python
async def handle_check_messages(
    bus: MessageBus,
    issue_id: str,
    agent_id: str,
) -> list[AgentMessage]:
    """Check for and return any pending messages for this agent.

    Drains the agent's subscription queue. Returns an empty list if no messages.
    Subscribes the agent if not already subscribed.
    """
    queue = await bus.subscribe(issue_id, agent_id)
    messages = []
    while not queue.empty():
        item = await queue.get()
        if isinstance(item, AgentMessage):
            messages.append(item)
    return messages
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleCheckMessages -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: add handle_check_messages for agent-to-agent communication"
```

---

### Task 4: Add Memory Handlers

**Files:**
- Modify: `jig/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_tools.py`:

```python
from jig.models import AgentInstance, AgentStatus
from jig.mcp_tools import handle_save_memory, handle_load_memory
from jig.persistence import save_agent_instance, load_agent_instance


class TestHandleSaveMemory:
    async def test_saves_memory_entry(self, tmp_jig_project: Path):
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-1", agent_type="dev"))
        result = await handle_save_memory(
            project_path=tmp_jig_project,
            agent_id="dev-1",
            args={"entry": "Always use pytest fixtures for DB tests"},
        )
        assert "saved" in result.lower()
        instance = load_agent_instance(tmp_jig_project, "dev-1")
        assert "Always use pytest fixtures for DB tests" in instance.memory

    async def test_appends_to_existing(self, tmp_jig_project: Path):
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev", memory=["First memory"],
        ))
        await handle_save_memory(
            project_path=tmp_jig_project,
            agent_id="dev-1",
            args={"entry": "Second memory"},
        )
        instance = load_agent_instance(tmp_jig_project, "dev-1")
        assert instance.memory == ["First memory", "Second memory"]


class TestHandleLoadMemory:
    async def test_loads_memories(self, tmp_jig_project: Path):
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev",
            memory=["Use pytest", "Project uses FastAPI"],
        ))
        result = await handle_load_memory(
            project_path=tmp_jig_project,
            agent_id="dev-1",
        )
        assert len(result) == 2
        assert "Use pytest" in result
        assert "Project uses FastAPI" in result

    async def test_loads_empty(self, tmp_jig_project: Path):
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-1", agent_type="dev"))
        result = await handle_load_memory(
            project_path=tmp_jig_project,
            agent_id="dev-1",
        )
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleSaveMemory -v`
Expected: FAIL

- [ ] **Step 3: Implement memory handlers**

Append to `jig/mcp_tools.py`:

```python
from jig.persistence import load_agent_instance, save_agent_instance


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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_tools.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: add save_memory and load_memory MCP handlers"
```

---

### Task 5: Add get_workflow_status Handler

**Files:**
- Modify: `jig/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_tools.py`:

```python
from jig.models import IssueStatus
from jig.mcp_tools import handle_get_workflow_status
from jig.persistence import save_workflow
from jig.models import WorkflowConfig, PhaseConfig


class TestHandleGetWorkflowStatus:
    def _setup_project(self, tmp_jig_project: Path) -> None:
        save_issue(tmp_jig_project, Issue(
            id="issue-1", title="Test", status=IssueStatus.IN_PROGRESS, current_phase="test",
        ))
        save_workflow(tmp_jig_project, WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name="spec", role="spec"),
                PhaseConfig(name="test", role="test"),
                PhaseConfig(name="implement", role="dev"),
            ],
        ))

    async def test_returns_status(self, tmp_jig_project: Path):
        self._setup_project(tmp_jig_project)
        result = await handle_get_workflow_status(
            project_path=tmp_jig_project,
            issue_id="issue-1",
        )
        assert result["issue_id"] == "issue-1"
        assert result["issue_status"] == "in_progress"
        assert result["current_phase"] == "test"
        assert len(result["phases"]) == 3
        assert result["phases"][0]["name"] == "spec"
        assert result["phases"][0]["role"] == "spec"

    async def test_includes_agent_instances(self, tmp_jig_project: Path):
        self._setup_project(tmp_jig_project)
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.ACTIVE,
        ))
        result = await handle_get_workflow_status(
            project_path=tmp_jig_project,
            issue_id="issue-1",
        )
        assert len(result["agents"]) == 1
        assert result["agents"][0]["id"] == "dev-1"
        assert result["agents"][0]["status"] == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleGetWorkflowStatus -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Append to `jig/mcp_tools.py`:

```python
from jig.persistence import load_issue, load_workflow, list_agent_instances


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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_tools.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: add get_workflow_status MCP handler"
```

---

### Task 6: Agent MCP Server Factory

**Files:**
- Create: `jig/mcp_server.py`
- Create: `tests/test_mcp_server.py`

Move the server factory out of `mcp_tools.py` into its own file. Update it to use the new handlers.

- [ ] **Step 1: Write failing tests**

`tests/test_mcp_server.py`:

```python
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import AgentInstance, Issue, Task
from jig.mcp_server import create_agent_mcp_server
from jig.persistence import save_agent_instance, save_issue, save_task


class TestCreateAgentMcpServer:
    def test_returns_server_config(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(id="task-1", description="Do", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-1", agent_type="dev"))
        bus = MessageBus(tmp_jig_project)
        server = create_agent_mcp_server(
            bus=bus,
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            agent_id="dev-1",
            worktree_path=tmp_jig_project,
        )
        assert server is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement create_agent_mcp_server**

`jig/mcp_server.py`:

```python
"""MCP server factories for agent and orchestrator tools."""

from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig.bus import MessageBus
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add agent MCP server factory with all tools"
```

---

### Task 7: Orchestrator MCP Server Factory

**Files:**
- Modify: `jig/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_mcp_server.py`:

```python
from jig.mcp_server import create_orchestrator_mcp_server


class TestCreateOrchestratorMcpServer:
    def test_returns_server_config(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        bus = MessageBus(tmp_jig_project)
        server = create_orchestrator_mcp_server(
            bus=bus,
            project_path=tmp_jig_project,
            issue_id="issue-1",
        )
        assert server is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py::TestCreateOrchestratorMcpServer -v`
Expected: FAIL

- [ ] **Step 3: Implement create_orchestrator_mcp_server**

Append to `jig/mcp_server.py`:

```python
def create_orchestrator_mcp_server(
    bus: MessageBus,
    project_path: Path,
    issue_id: str,
):
    """Create a Jig MCP server for the orchestrator agent.

    Coordination-only tools. No code tools (Read, Edit, Write, Bash).
    Tools: get_workflow_status, send_message, check_messages,
    save_memory, load_memory, get_message_history
    """
    orchestrator_id = "orchestrator"

    @tool(
        "get_workflow_status",
        "Get the current workflow status including phases and agent states",
        {},
    )
    async def get_workflow_status_tool(args):
        status = await handle_get_workflow_status(project_path=project_path, issue_id=issue_id)
        import json
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
```

Note: The orchestrator's action tools (assign_task, resume_agent, pause_workflow, advance_phase, retry_phase) will be added in Plan 7 when the orchestrator agent integration is built. For now, the orchestrator MCP server has the data-access tools it needs.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: 2 passed

- [ ] **Step 5: Remove old create_jig_mcp_server from mcp_tools.py**

Delete the `create_jig_mcp_server` function and the `from claude_agent_sdk import tool, create_sdk_mcp_server` import from `jig/mcp_tools.py`. This factory has moved to `mcp_server.py`.

Also update `jig/agent.py` to import from the new location:

Change:
```python
from jig.mcp_tools import create_jig_mcp_server
```
To:
```python
from jig.mcp_server import create_agent_mcp_server
```

And update the call in `run_agent` from `create_jig_mcp_server(...)` to `create_agent_mcp_server(...)`. The parameter `agent_name` becomes `agent_id`.

Update any remaining tests that import `create_jig_mcp_server` from `mcp_tools` — they should now import `create_agent_mcp_server` from `mcp_server`.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add jig/mcp_server.py jig/mcp_tools.py jig/agent.py tests/
git commit -m "feat: add orchestrator MCP server, move factories to mcp_server.py"
```
