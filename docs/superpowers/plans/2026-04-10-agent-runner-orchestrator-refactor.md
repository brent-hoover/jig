# Agent Runner + Orchestrator Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the agent pool into the runner and orchestrator, add session resumption and memory loading, and make the orchestrator a persistent dormant agent with its own session/memory.

**Architecture:** The agent runner (`agent.py`) gains pool integration (acquire/release), session resumption via `resume=session_id`, and memory injection into the prompt. The orchestrator (`orchestrator.py`) uses the pool for `_execute_phase` and manages the orchestrator agent instance through the pool for LLM recovery decisions. A new bus monitor watches for messages to dormant agents and resumes them.

**Tech Stack:** Python 3.12+, claude-agent-sdk, asyncio, pytest

**Depends on:** Plans 5-6 (AgentInstance, AgentPool, protocol-based MCP tools)

---

## File Structure

```
jig/
├── agent.py             # (modify) Add pool integration, session resumption, memory loading
├── orchestrator.py      # (modify) Use pool for phases, persistent orchestrator agent
├── bus_monitor.py       # (create) Watch bus for messages to dormant agents, resume them
tests/
├── test_agent.py        # (modify) Tests for pool/session/memory integration
├── test_orchestrator.py # (modify) Tests for pool-based orchestrator
├── test_bus_monitor.py  # (create) Bus monitor tests
```

---

### Task 1: Agent Runner — Pool Integration

**Files:**
- Modify: `jig/agent.py`
- Modify: `tests/test_agent.py`

Update `run_agent` to accept an `AgentInstance` instead of constructing agent identity ad-hoc. The caller (orchestrator) acquires from the pool and passes the instance. The runner uses `instance.id` for the MCP server's `agent_id`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent.py`:

```python
from jig.models import AgentInstance, AgentStatus


class TestRunAgentWithInstance:
    @pytest.fixture
    def agent_type(self) -> AgentTypeConfig:
        return AgentTypeConfig(
            role="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )

    @pytest.fixture
    def setup_with_instance(self, tmp_jig_project: Path):
        """Create issue, task, and agent instance."""
        from jig.persistence import save_agent_instance
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(
            id="task-1",
            description="Implement feature X",
            acceptance_criteria="Tests pass",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)
        instance = AgentInstance(id="dev-1", agent_type="dev", status=AgentStatus.ACTIVE)
        save_agent_instance(tmp_jig_project, instance)
        return tmp_jig_project, "issue-1", "task-1", instance

    @patch("jig.agent.query")
    @patch("jig.agent.create_agent_mcp_server")
    async def test_uses_instance_id_for_mcp(
        self, mock_mcp, mock_query, agent_type, setup_with_instance
    ):
        project_path, issue_id, task_id, instance = setup_with_instance
        mock_mcp.return_value = "mock-server"

        async def fake_query(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.result = "Done"
            yield mock_result

        mock_query.return_value = fake_query()

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
            agent_instance=instance,
        )

        # MCP server should be created with the instance ID
        mock_mcp.assert_called_once()
        call_kwargs = mock_mcp.call_args.kwargs
        assert call_kwargs["agent_id"] == "dev-1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent.py::TestRunAgentWithInstance -v`
Expected: FAIL (run_agent doesn't accept agent_instance param yet)

- [ ] **Step 3: Update run_agent signature**

In `jig/agent.py`, add `AgentInstance` to the imports:

```python
from jig.models import AgentTypeConfig, AgentInstance, Issue, ProjectContext
```

Update `run_agent` to accept an optional `agent_instance` parameter. When provided, use `instance.id` as the agent_id. When not provided (backward compat), generate one as before:

```python
async def run_agent(
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_type: AgentTypeConfig,
    worktree_path: Path,
    max_turns: int = 50,
    emitter: EventEmitter | None = None,
    issue: Issue | None = None,
    project_context: ProjectContext | None = None,
    agent_instance: AgentInstance | None = None,
) -> str:
```

Then in the body, replace:
```python
    agent_name = f"{agent_type.role}-{task_id}"
```
with:
```python
    agent_id = agent_instance.id if agent_instance else f"{agent_type.role}-{task_id}"
```

And update the `create_agent_mcp_server` call to use `agent_id`:
```python
    mcp_server = create_agent_mcp_server(
        bus=bus,
        project_path=project_path,
        issue_id=issue_id,
        task_id=task_id,
        agent_id=agent_id,
        worktree_path=worktree_path,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_agent.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/agent.py tests/test_agent.py
git commit -m "feat: add agent_instance parameter to run_agent for pool integration"
```

---

### Task 2: Agent Runner — Memory Loading

**Files:**
- Modify: `jig/agent.py`
- Modify: `tests/test_agent.py`

When an `agent_instance` is provided and has memories, inject them into the prompt.

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent.py`:

```python
class TestRunAgentWithMemory:
    @pytest.fixture
    def agent_type(self) -> AgentTypeConfig:
        return AgentTypeConfig(
            role="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )

    @pytest.fixture
    def setup_with_memory(self, tmp_jig_project: Path):
        from jig.persistence import save_agent_instance
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(id="task-1", description="Implement X", acceptance_criteria="Tests pass", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        instance = AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.ACTIVE,
            memory=["Always use pytest fixtures", "Project uses FastAPI"],
        )
        save_agent_instance(tmp_jig_project, instance)
        return tmp_jig_project, "issue-1", "task-1", instance

    @patch("jig.agent.query")
    @patch("jig.agent.create_agent_mcp_server")
    async def test_injects_memories_into_prompt(
        self, mock_mcp, mock_query, agent_type, setup_with_memory
    ):
        project_path, issue_id, task_id, instance = setup_with_memory
        mock_mcp.return_value = "mock-server"

        captured_prompt = None

        async def fake_query(*args, **kwargs):
            nonlocal captured_prompt
            captured_prompt = kwargs.get("prompt", args[0] if args else "")
            mock_result = MagicMock()
            mock_result.result = "Done"
            yield mock_result

        mock_query.return_value = fake_query()

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
            agent_instance=instance,
        )

        assert "Always use pytest fixtures" in captured_prompt
        assert "Project uses FastAPI" in captured_prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_agent.py::TestRunAgentWithMemory -v`
Expected: FAIL (memories not in prompt yet)

- [ ] **Step 3: Add memory section to prompt building**

In `jig/agent.py`, in the `run_agent` function, add a memory section to the prompt. After the project context section and before the task section, add:

```python
    # Inject agent memories if available
    if agent_instance and agent_instance.memory:
        memory_lines = "\n".join(f"- {m}" for m in agent_instance.memory)
        prompt_parts.append(
            f"## Your Memories (from previous sessions)\n\n{memory_lines}\n\n"
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_agent.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/agent.py tests/test_agent.py
git commit -m "feat: inject agent memories into prompt when instance provided"
```

---

### Task 3: Agent Runner — Session Resumption

**Files:**
- Modify: `jig/agent.py`
- Modify: `tests/test_agent.py`

When an `agent_instance` has a `session_id`, use `resume=session_id` in the SDK options for conversation continuity.

- [ ] **Step 1: Write failing test**

Append to `tests/test_agent.py`:

```python
class TestRunAgentSessionResumption:
    @pytest.fixture
    def agent_type(self) -> AgentTypeConfig:
        return AgentTypeConfig(
            role="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )

    @pytest.fixture
    def setup_with_session(self, tmp_jig_project: Path):
        from jig.persistence import save_agent_instance
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(id="task-1", description="Implement X", acceptance_criteria="Tests pass", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        instance = AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.DORMANT,
            session_id="sess-previous-123",
        )
        save_agent_instance(tmp_jig_project, instance)
        return tmp_jig_project, "issue-1", "task-1", instance

    @patch("jig.agent.query")
    @patch("jig.agent.create_agent_mcp_server")
    async def test_passes_resume_session_id(
        self, mock_mcp, mock_query, agent_type, setup_with_session
    ):
        project_path, issue_id, task_id, instance = setup_with_session
        mock_mcp.return_value = "mock-server"

        async def fake_query(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.result = "Done"
            yield mock_result

        mock_query.return_value = fake_query()

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
            agent_instance=instance,
        )

        call_kwargs = mock_query.call_args.kwargs
        options = call_kwargs["options"]
        assert options.resume == "sess-previous-123"

    @patch("jig.agent.query")
    @patch("jig.agent.create_agent_mcp_server")
    async def test_no_resume_without_session_id(
        self, mock_mcp, mock_query, agent_type, setup_with_instance
    ):
        project_path, issue_id, task_id, instance = setup_with_instance
        mock_mcp.return_value = "mock-server"

        async def fake_query(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.result = "Done"
            yield mock_result

        mock_query.return_value = fake_query()

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
            agent_instance=instance,
        )

        call_kwargs = mock_query.call_args.kwargs
        options = call_kwargs["options"]
        # resume should not be set (or be None)
        assert getattr(options, "resume", None) is None
```

NOTE: The second test `test_no_resume_without_session_id` reuses the `setup_with_instance` fixture from Task 1 (which has `session_id=None`). Make sure that fixture is accessible.

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_agent.py::TestRunAgentSessionResumption -v`
Expected: FAIL

- [ ] **Step 3: Add session resumption to run_agent**

In `jig/agent.py`, update the `ClaudeAgentOptions` construction. Add `resume` when the instance has a session_id:

```python
    resume_id = agent_instance.session_id if agent_instance else None

    options = ClaudeAgentOptions(
        cwd=str(worktree_path),
        allowed_tools=agent_type.allowed_tools,
        disallowed_tools=[],
        system_prompt=agent_type.system_prompt,
        mcp_servers={"jig": mcp_server},
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        **({"resume": resume_id} if resume_id else {}),
    )
```

- [ ] **Step 4: Capture session_id from response**

After the agent finishes, capture the session_id from the SystemMessage (init) for future resumption. Update the agent loop to watch for it:

Before the `async for` loop, add:
```python
    captured_session_id = None
```

Inside the loop, add handling for SystemMessage (which is already imported):
```python
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            captured_session_id = message.data.get("session_id")
```

After the loop, update the instance if provided:
```python
    if agent_instance and captured_session_id:
        agent_instance.session_id = captured_session_id
```

Note: We don't persist the instance here — the caller (orchestrator) is responsible for that via the pool.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_agent.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add jig/agent.py tests/test_agent.py
git commit -m "feat: add session resumption and session_id capture to agent runner"
```

---

### Task 4: Orchestrator — Pool Integration

**Files:**
- Modify: `jig/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

Update `_execute_phase` to use `AgentPool.acquire()` and `AgentPool.release()` instead of ad-hoc agent construction.

- [ ] **Step 1: Write failing test**

Append to `tests/test_orchestrator.py`:

```python
from jig.pool import AgentPool
from jig.models import AgentInstance, AgentStatus
from jig.persistence import save_agent_instance, load_agent_instance, list_agent_instances


class TestOrchestratorPoolIntegration:
    @patch("jig.orchestrator.run_agent")
    async def test_acquires_agent_from_pool(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        # run_agent should have been called with an agent_instance
        call_kwargs = mock_run_agent.call_args.kwargs
        assert "agent_instance" in call_kwargs
        instance = call_kwargs["agent_instance"]
        assert isinstance(instance, AgentInstance)
        assert instance.agent_type == "spec"

    @patch("jig.orchestrator.run_agent")
    async def test_releases_agent_after_phase(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        # After completion, agent should be dormant
        instances = list_agent_instances(git_project, agent_type="spec")
        assert len(instances) >= 1
        assert instances[0].status == AgentStatus.DORMANT
```

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_orchestrator.py::TestOrchestratorPoolIntegration -v`
Expected: FAIL

- [ ] **Step 3: Update orchestrator to use pool**

In `jig/orchestrator.py`, add imports:

```python
from jig.pool import AgentPool
from jig.models import AgentInstance
```

In `__init__`, create the pool:

```python
        self._pool = AgentPool(project_path)
```

In `_execute_phase`, acquire an agent from the pool before running:

Replace the line:
```python
        agent_type = load_agent_type(self._project_path, phase.role)
```

With:
```python
        agent_type = load_agent_type(self._project_path, phase.role)
        instance = self._pool.acquire(phase.role)
        instance.current_task_id = task.id
        self._pool.update(instance)
```

Update the `run_agent` call to pass the instance:
```python
        await run_agent(
            project_path=self._project_path,
            issue_id=self._issue_id,
            task_id=task.id,
            agent_type=agent_type,
            worktree_path=worktree_path,
            emitter=self._emitter,
            issue=issue,
            project_context=self._project_context,
            agent_instance=instance,
        )
```

After the phase completes (after commit_worktree), release the agent:
```python
        self._pool.release(instance)
```

Make sure the release happens even on error — wrap the run_agent + commit section in try/finally:
```python
        try:
            await run_agent(...)
            await commit_worktree(...)
        finally:
            self._pool.release(instance)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: all passed (existing tests should still work since pool creates instances on demand)

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: integrate agent pool into orchestrator phase execution"
```

---

### Task 5: Orchestrator — Persistent Orchestrator Agent

**Files:**
- Modify: `jig/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

Refactor `_decide_next_phase` to use a persistent orchestrator agent instance with its own memory and session.

- [ ] **Step 1: Write failing test**

Append to `tests/test_orchestrator.py`:

```python
class TestOrchestratorAgentPersistence:
    @patch("jig.orchestrator.run_agent")
    @patch("jig.orchestrator.query")
    async def test_orchestrator_has_agent_instance(self, mock_query, mock_run_agent, git_project_full_workflow: Path):
        """When orchestrator needs to make a decision, it should use a persistent agent instance."""
        call_count = 0

        async def fake_run_agent(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First phase fails
                task = load_task(kwargs["project_path"], kwargs["issue_id"], kwargs["task_id"])
                task.completion_state = "failed"
                task.completion_reason = "Compile error"
                save_task(kwargs["project_path"], kwargs["issue_id"], task)
            return "Done"

        mock_run_agent.side_effect = fake_run_agent

        # Mock the orchestrator LLM to retry
        async def fake_query(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.result = '{"action": "run_phase", "phase": "spec", "reasoning": "Retrying"}'
            yield mock_result

        mock_query.return_value = fake_query()

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        # Orchestrator agent instance should exist
        orch_instances = list_agent_instances(git_project_full_workflow, agent_type="orchestrator")
        assert len(orch_instances) >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::TestOrchestratorAgentPersistence -v`
Expected: FAIL

- [ ] **Step 3: Create orchestrator agent type and use persistent instance**

In `jig/orchestrator.py`, update `_decide_next_phase` to:

1. Acquire an orchestrator agent instance from the pool (or create one if the "orchestrator" agent type doesn't exist)
2. Use the instance's session_id for session resumption
3. After the decision, update the instance with the session_id and save a memory of what happened

First, ensure an orchestrator agent type exists. In `__init__`, add:

```python
        # Ensure orchestrator agent type exists
        try:
            load_agent_type(project_path, "orchestrator")
        except FileNotFoundError:
            from jig.persistence import save_agent_type
            from jig.models import AgentTypeConfig
            save_agent_type(project_path, AgentTypeConfig(
                role="orchestrator",
                system_prompt="You are a workflow orchestrator. You decide how to recover from phase failures.",
                allowed_tools=[],
            ))
```

In `_decide_next_phase`, get or create the orchestrator instance:

```python
        # Get or create orchestrator agent instance
        orch_instance = self._pool.find_idle("orchestrator")
        if orch_instance is None:
            idle_or_dormant = [
                i for i in list_agent_instances(self._project_path, agent_type="orchestrator")
                if i.status in (AgentStatus.IDLE, AgentStatus.DORMANT)
            ]
            if idle_or_dormant:
                orch_instance = idle_or_dormant[0]
            else:
                orch_instance = self._pool.spawn("orchestrator")

        orch_instance.status = AgentStatus.ACTIVE
        self._pool.update(orch_instance)
```

After the LLM decision is parsed, save a memory and release:

```python
        # Record what happened in orchestrator memory
        memory_entry = f"Phase '{last_phase}' {last.get('result')}: decided to {decision.get('action')} — {decision.get('reasoning', '')}"
        orch_instance.memory.append(memory_entry)
        orch_instance.status = AgentStatus.DORMANT
        self._pool.update(orch_instance)
```

You'll need to add these imports:
```python
from jig.models import AgentInstance, AgentStatus
from jig.persistence import list_agent_instances
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: use persistent orchestrator agent instance with memory"
```

---

### Task 6: Bus Monitor — Wake Dormant Agents on Messages

**Files:**
- Create: `jig/bus_monitor.py`
- Create: `tests/test_bus_monitor.py`

The bus monitor watches the message bus. When a message arrives addressed to a specific agent that is dormant, it triggers a callback so the server can resume that agent.

- [ ] **Step 1: Write failing tests**

`tests/test_bus_monitor.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jig.bus import MessageBus
from jig.bus_monitor import BusMonitor
from jig.models import AgentInstance, AgentMessage, AgentStatus, Issue, MessageDirection
from jig.persistence import save_agent_instance, save_issue


class TestBusMonitor:
    @pytest.fixture
    def setup(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.DORMANT,
        ))
        bus = MessageBus(tmp_jig_project)
        return tmp_jig_project, bus

    async def test_detects_message_to_dormant_agent(self, setup):
        project_path, bus = setup
        callback = AsyncMock()
        monitor = BusMonitor(project_path, bus, "issue-1", on_wake=callback)
        monitor_task = asyncio.create_task(monitor.start())

        # Allow monitor to start
        await asyncio.sleep(0.1)

        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.REQUEST,
            topic="question",
            content="How should I test this?",
        )
        await bus.publish("issue-1", msg)

        # Allow processing
        await asyncio.sleep(0.2)

        callback.assert_called_once_with("dev-1", msg)

        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    async def test_ignores_message_to_active_agent(self, setup, tmp_jig_project: Path):
        project_path, bus = setup
        # Change agent to active
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.ACTIVE,
        ))
        callback = AsyncMock()
        monitor = BusMonitor(project_path, bus, "issue-1", on_wake=callback)
        monitor_task = asyncio.create_task(monitor.start())
        await asyncio.sleep(0.1)

        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.REQUEST,
            topic="question",
            content="question",
        )
        await bus.publish("issue-1", msg)
        await asyncio.sleep(0.2)

        callback.assert_not_called()

        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_bus_monitor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement BusMonitor**

`jig/bus_monitor.py`:

```python
"""Bus monitor — watches for messages to dormant agents and triggers wake-up."""

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from jig.bus import MessageBus
from jig.models import AgentMessage, AgentStatus
from jig.persistence import load_agent_instance


class BusMonitor:
    def __init__(
        self,
        project_path: Path,
        bus: MessageBus,
        issue_id: str,
        on_wake: Callable[[str, AgentMessage], Coroutine[Any, Any, None]],
    ) -> None:
        self._project_path = project_path
        self._bus = bus
        self._issue_id = issue_id
        self._on_wake = on_wake
        self._running = False

    async def start(self) -> None:
        """Start monitoring the bus for messages to dormant agents."""
        self._running = True
        queue = await self._bus.subscribe(self._issue_id, "__monitor__")
        while self._running:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if not isinstance(message, AgentMessage):
                continue

            recipient_id = message.recipient_id
            if recipient_id == "broadcast" or recipient_id == "__monitor__":
                continue

            try:
                instance = load_agent_instance(self._project_path, recipient_id)
                if instance.status == AgentStatus.DORMANT:
                    await self._on_wake(recipient_id, message)
            except FileNotFoundError:
                pass  # Not a known agent

    def stop(self) -> None:
        self._running = False
```

NOTE: The monitor subscribes as `__monitor__` — this is a special subscriber that sees all messages on the issue (because the bus routes to named recipients). For the monitor to see messages, we need the bus to also publish to `__monitor__` for all messages. 

Actually, looking at the bus implementation, the bus only sends to the named recipient or broadcasts to all. The monitor needs a different approach. Instead, make the bus support a "tap" — a subscriber that gets a copy of every message regardless of recipient.

Alternative simpler approach: subscribe the monitor as `"broadcast"` — but that only gets broadcast messages, not targeted ones.

Simplest approach: add a `tap` method to the bus that registers a queue that receives ALL messages. Update the bus first:

In `jig/bus.py`, add:
```python
    def tap(self, issue_id: str) -> asyncio.Queue[BusMessage]:
        """Subscribe a tap that receives ALL messages for an issue."""
        if issue_id not in self._subscribers:
            self._subscribers[issue_id] = {}
        tap_id = f"__tap_{id(object())}__"
        queue: asyncio.Queue[BusMessage] = asyncio.Queue()
        self._subscribers[issue_id][tap_id] = queue
        # Mark this as a tap so publish sends all messages to it
        if not hasattr(self, "_taps"):
            self._taps: dict[str, set[str]] = {}
        self._taps.setdefault(issue_id, set()).add(tap_id)
        return queue
```

And update `publish` to also send to taps:
```python
    async def publish(self, issue_id: str, message: BusMessage) -> None:
        await asyncio.to_thread(append_message, self._project_path, issue_id, message)
        recipient = _get_recipient(message)
        subs = self._subscribers.get(issue_id, {})
        taps = getattr(self, "_taps", {}).get(issue_id, set())

        if recipient == "broadcast":
            for name, queue in subs.items():
                await queue.put(message)
        else:
            if recipient in subs:
                await subs[recipient].put(message)
            # Also send to all taps
            for tap_id in taps:
                if tap_id in subs and tap_id != recipient:
                    await subs[tap_id].put(message)
```

Then in the monitor, use `self._bus.tap(issue_id)` instead of `subscribe`.

This is getting complex. Let me simplify — just have the monitor poll persisted messages instead. But that's less reactive.

Actually, the simplest correct approach: update the bus `publish` to always send to taps regardless of recipient. Let me write this cleanly.

- [ ] **Step 3 (revised): Add tap to bus, then implement monitor**

First, add a `tap` method to `jig/bus.py`. Add this method to `MessageBus`:

```python
    async def tap(self, issue_id: str) -> asyncio.Queue[BusMessage]:
        """Subscribe a tap that receives a copy of ALL messages for an issue, regardless of recipient."""
        if not hasattr(self, "_taps"):
            self._taps: dict[str, list[asyncio.Queue[BusMessage]]] = {}
        queue: asyncio.Queue[BusMessage] = asyncio.Queue()
        self._taps.setdefault(issue_id, []).append(queue)
        return queue
```

Update the `publish` method to also send to taps. After the existing routing logic, add:

```python
        # Also send to all taps
        for tap_queue in getattr(self, "_taps", {}).get(issue_id, []):
            await tap_queue.put(message)
```

Then `jig/bus_monitor.py` uses `tap`:

```python
"""Bus monitor — watches for messages to dormant agents and triggers wake-up."""

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from jig.bus import MessageBus
from jig.models import AgentMessage, AgentStatus
from jig.persistence import load_agent_instance


class BusMonitor:
    def __init__(
        self,
        project_path: Path,
        bus: MessageBus,
        issue_id: str,
        on_wake: Callable[[str, AgentMessage], Coroutine[Any, Any, None]],
    ) -> None:
        self._project_path = project_path
        self._bus = bus
        self._issue_id = issue_id
        self._on_wake = on_wake
        self._running = False

    async def start(self) -> None:
        """Start monitoring the bus for messages to dormant agents."""
        self._running = True
        queue = await self._bus.tap(self._issue_id)
        while self._running:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if not isinstance(message, AgentMessage):
                continue

            recipient_id = message.recipient_id
            if recipient_id == "broadcast":
                continue

            try:
                instance = load_agent_instance(self._project_path, recipient_id)
                if instance.status == AgentStatus.DORMANT:
                    await self._on_wake(recipient_id, message)
            except FileNotFoundError:
                pass

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_bus_monitor.py -v`
Expected: 2 passed

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add jig/bus.py jig/bus_monitor.py tests/test_bus_monitor.py
git commit -m "feat: add bus tap and monitor for waking dormant agents on messages"
```
