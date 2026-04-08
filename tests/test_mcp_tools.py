import json
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import Issue, Message, MessageType, Task, CompletionState
from jig.mcp_tools import handle_publish_message, handle_report_completion, handle_request_context
from jig.persistence import save_issue, save_task, load_task


class TestHandlePublishMessage:
    @pytest.fixture
    def bus(self, tmp_jig_project: Path) -> MessageBus:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        return MessageBus(tmp_jig_project)

    async def test_publishes_message(self, bus: MessageBus, tmp_jig_project: Path):
        queue = await bus.subscribe("issue-1", "orchestrator")
        result = await handle_publish_message(
            bus=bus,
            issue_id="issue-1",
            sender="dev-agent",
            args={
                "recipient": "orchestrator",
                "message_type": "status",
                "payload": json.dumps({"info": "working"}),
            },
        )
        assert "sent" in result.lower()
        msg = await queue.get()
        assert msg.sender == "dev-agent"
        assert msg.recipient == "orchestrator"
        assert msg.type == MessageType.STATUS
        assert msg.payload == {"info": "working"}

    async def test_empty_payload(self, bus: MessageBus, tmp_jig_project: Path):
        queue = await bus.subscribe("issue-1", "orchestrator")
        result = await handle_publish_message(
            bus=bus,
            issue_id="issue-1",
            sender="dev-agent",
            args={
                "recipient": "orchestrator",
                "message_type": "status",
            },
        )
        msg = await queue.get()
        assert msg.payload == {}


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

    async def test_marks_task_success(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        result = await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            args={"status": "success", "reason": "All tests pass"},
        )
        assert "success" in result.lower()
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == CompletionState.SUCCESS
        assert task.completion_reason == "All tests pass"

    async def test_marks_task_needs_info(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        result = await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            args={"status": "needs_info", "reason": "Missing API spec"},
        )
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == CompletionState.NEEDS_INFO
        assert task.completion_reason == "Missing API spec"


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


from jig.mcp_tools import create_jig_mcp_server


class TestCreateJigMcpServer:
    def test_returns_server_config(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(id="task-1", description="Do", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        bus = MessageBus(tmp_jig_project)
        server = create_jig_mcp_server(
            bus=bus,
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            agent_name="dev-agent",
            worktree_path=tmp_jig_project,
        )
        assert server is not None
