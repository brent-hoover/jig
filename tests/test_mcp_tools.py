import json
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import (
    AgentInstance,
    AgentMessage,
    AgentStatus,
    CompletionReport,
    CompletionStatus,
    Issue,
    IssueStatus,
    MessageDirection,
    PhaseConfig,
    Task,
    WorkflowConfig,
)
from jig.mcp_tools import (
    handle_send_message,
    handle_report_completion,
    handle_request_context,
    handle_check_messages,
    handle_save_memory,
    handle_load_memory,
    handle_get_workflow_status,
)
from jig.persistence import save_issue, save_task, load_task, save_agent_instance, load_agent_instance, save_workflow


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


class TestHandleRequestContext:
    async def test_reads_file(self, tmp_jig_project: Path):
        (tmp_jig_project / "src.py").write_text("print('hello')")
        result = await handle_request_context(
            worktree_path=tmp_jig_project,
            args={"path": "src.py"},
        )
        assert "print('hello')" in result

    async def test_file_not_found(self, tmp_jig_project: Path):
        result = await handle_request_context(
            worktree_path=tmp_jig_project,
            args={"path": "nonexistent.py"},
        )
        assert "not found" in result.lower()


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
        queue = await bus.subscribe("issue-1", "dev-1")
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

        result = await handle_check_messages(bus=bus, issue_id="issue-1", agent_id="dev-1")
        assert len(result) == 3

        result2 = await handle_check_messages(bus=bus, issue_id="issue-1", agent_id="dev-1")
        assert result2 == []


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
