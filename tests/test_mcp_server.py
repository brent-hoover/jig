from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import AgentInstance, Issue, Task
from jig.mcp_server import create_agent_mcp_server, create_orchestrator_mcp_server
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
