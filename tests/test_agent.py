from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jig.agent import _sanitize_for_tui, run_agent
from jig.models import (
    AgentInstance,
    AgentStatus,
    AgentTypeConfig,
    Issue,
    Task,
)
from jig.persistence import save_issue, save_task


class TestRunAgent:
    @pytest.fixture
    def agent_type(self) -> AgentTypeConfig:
        return AgentTypeConfig(
            role="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )

    @pytest.fixture
    def setup_issue(self, tmp_jig_project: Path) -> tuple[Path, str, str]:
        """Create an issue and task, return (project_path, issue_id, task_id)."""
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(
            id="task-1",
            description="Implement feature X",
            acceptance_criteria="Tests pass",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)
        return tmp_jig_project, "issue-1", "task-1"

    @patch("jig.agent.create_agent_mcp_server", return_value=MagicMock())
    @patch("jig.agent.query")
    async def test_calls_sdk_with_correct_options(
        self, mock_query, mock_mcp, agent_type, setup_issue
    ):
        project_path, issue_id, task_id = setup_issue

        mock_result = MagicMock()
        mock_result.result = "Done"
        mock_result.stop_reason = "end_turn"

        async def fake_query(*args, **kwargs):
            yield mock_result

        mock_query.return_value = fake_query()

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert "Implement feature X" in call_kwargs.kwargs["prompt"]
        options = call_kwargs.kwargs["options"]
        assert "Read" in options.allowed_tools
        assert "Edit" in options.allowed_tools
        assert options.cwd == str(project_path)
        assert "You are a dev agent." in options.system_prompt

    @patch("jig.agent.create_agent_mcp_server", return_value=MagicMock())
    @patch("jig.agent.query")
    async def test_returns_result_text(self, mock_query, mock_mcp, agent_type, setup_issue):
        project_path, issue_id, task_id = setup_issue

        mock_result = MagicMock()
        mock_result.result = "Feature implemented successfully"
        mock_result.stop_reason = "end_turn"

        async def fake_query(*args, **kwargs):
            yield mock_result

        mock_query.return_value = fake_query()

        result = await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
        )

        assert result == "Feature implemented successfully"


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
        from jig.persistence import save_agent_instance
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(id="task-1", description="Implement feature X", acceptance_criteria="Tests pass", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        instance = AgentInstance(id="dev-1", agent_type="dev", status=AgentStatus.ACTIVE)
        save_agent_instance(tmp_jig_project, instance)
        return tmp_jig_project, "issue-1", "task-1", instance

    @patch("jig.agent.query")
    @patch("jig.agent.create_agent_mcp_server")
    async def test_uses_instance_id_for_mcp(self, mock_mcp, mock_query, agent_type, setup_with_instance):
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

        mock_mcp.assert_called_once()
        call_kwargs = mock_mcp.call_args.kwargs
        assert call_kwargs["agent_id"] == "dev-1"


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
    async def test_injects_memories_into_prompt(self, mock_mcp, mock_query, agent_type, setup_with_memory):
        project_path, issue_id, task_id, instance = setup_with_memory
        mock_mcp.return_value = "mock-server"

        captured_prompt = None

        async def fake_query(*args, **kwargs):
            nonlocal captured_prompt
            captured_prompt = kwargs.get("prompt", args[0] if args else "")
            mock_result = MagicMock()
            mock_result.result = "Done"
            yield mock_result

        mock_query.side_effect = fake_query

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
            agent_instance=instance,
        )

        assert captured_prompt is not None
        assert "Always use pytest fixtures" in captured_prompt
        assert "Project uses FastAPI" in captured_prompt
        assert "Your Memories" in captured_prompt


class TestSanitizeForTui:
    def test_strips_newlines(self):
        assert _sanitize_for_tui("line1\nline2\nline3") == "line1 line2 line3"

    def test_strips_ansi_escapes(self):
        assert _sanitize_for_tui("\x1b[31mred\x1b[0m text") == "red text"

    def test_strips_control_chars(self):
        assert _sanitize_for_tui("hello\x00\x07\x08world") == "helloworld"

    def test_collapses_whitespace(self):
        assert _sanitize_for_tui("a\t\t b  \n  c") == "a b c"

    def test_truncates_with_ellipsis(self):
        out = _sanitize_for_tui("x" * 200, limit=10)
        assert len(out) == 10
        assert out.endswith("…")

    def test_empty_input(self):
        assert _sanitize_for_tui("") == ""
        assert _sanitize_for_tui("   \n\t  ") == ""
