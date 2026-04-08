import pytest
from pathlib import Path

from jig.models import ProjectConfig, Issue, IssueStatus, Message, MessageType
from jig.persistence import init_project, load_project, save_issue, load_issue, list_issues, append_message, load_messages


class TestInitProject:
    def test_creates_jig_directory(self, tmp_project: Path):
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert jig_dir.is_dir()
        assert (jig_dir / "config.yaml").is_file()
        assert (jig_dir / "issues").is_dir()
        assert (jig_dir / "agent_types").is_dir()
        assert (jig_dir / "workflows").is_dir()
        assert (jig_dir / "worktrees").is_dir()

    def test_writes_config(self, tmp_project: Path):
        init_project(tmp_project, default_branch="develop")
        config = load_project(tmp_project)
        assert config.repo_path == str(tmp_project)
        assert config.default_branch == "develop"

    def test_default_branch(self, tmp_project: Path):
        init_project(tmp_project)
        config = load_project(tmp_project)
        assert config.default_branch == "main"

    def test_raises_if_already_initialized(self, tmp_jig_project: Path):
        with pytest.raises(FileExistsError):
            init_project(tmp_jig_project)

    def test_raises_if_not_git_repo(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not a git repository"):
            init_project(tmp_path)


class TestIssuePersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        loaded = load_issue(tmp_jig_project, "issue-1")
        assert loaded.id == "issue-1"
        assert loaded.title == "Add auth"
        assert loaded.status == IssueStatus.PENDING

    def test_creates_issue_directory(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        issue_dir = tmp_jig_project / ".jig" / "issues" / "issue-1"
        assert issue_dir.is_dir()
        assert (issue_dir / "issue.yaml").is_file()
        assert (issue_dir / "tasks").is_dir()

    def test_list_empty(self, tmp_jig_project: Path):
        issues = list_issues(tmp_jig_project)
        assert issues == []

    def test_list_multiple(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="a", title="First"))
        save_issue(tmp_jig_project, Issue(id="b", title="Second"))
        issues = list_issues(tmp_jig_project)
        ids = {i.id for i in issues}
        assert ids == {"a", "b"}

    def test_update_existing(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        issue.status = IssueStatus.IN_PROGRESS
        issue.current_phase = "spec"
        save_issue(tmp_jig_project, issue)
        loaded = load_issue(tmp_jig_project, "issue-1")
        assert loaded.status == IssueStatus.IN_PROGRESS
        assert loaded.current_phase == "spec"

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_issue(tmp_jig_project, "nope")


class TestMessagePersistence:
    def test_append_and_load(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)

        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.STATUS,
            payload={"info": "started"},
        )
        append_message(tmp_jig_project, "issue-1", msg)
        messages = load_messages(tmp_jig_project, "issue-1")
        assert len(messages) == 1
        assert messages[0].sender == "dev-agent"
        assert messages[0].payload == {"info": "started"}

    def test_append_multiple(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)

        for i in range(3):
            msg = Message(
                sender=f"agent-{i}",
                recipient="orchestrator",
                type=MessageType.STATUS,
            )
            append_message(tmp_jig_project, "issue-1", msg)
        messages = load_messages(tmp_jig_project, "issue-1")
        assert len(messages) == 3
        assert messages[0].sender == "agent-0"
        assert messages[2].sender == "agent-2"

    def test_load_empty(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        messages = load_messages(tmp_jig_project, "issue-1")
        assert messages == []

    def test_preserves_order(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)

        for i in range(5):
            msg = Message(
                sender="agent",
                recipient="orchestrator",
                type=MessageType.STATUS,
                payload={"seq": i},
            )
            append_message(tmp_jig_project, "issue-1", msg)
        messages = load_messages(tmp_jig_project, "issue-1")
        seqs = [m.payload["seq"] for m in messages]
        assert seqs == [0, 1, 2, 3, 4]


from jig.models import AgentTypeConfig
from jig.persistence import save_agent_type, load_agent_type, list_agent_types


class TestAgentTypePersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        config = AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        save_agent_type(tmp_jig_project, config)
        loaded = load_agent_type(tmp_jig_project, "dev")
        assert loaded.name == "dev"
        assert loaded.system_prompt == "You are a dev agent."
        assert loaded.allowed_tools == ["Read", "Edit"]

    def test_saves_to_correct_path(self, tmp_jig_project: Path):
        config = AgentTypeConfig(name="test", system_prompt="Test agent.")
        save_agent_type(tmp_jig_project, config)
        yaml_path = tmp_jig_project / ".jig" / "agent_types" / "test.yaml"
        assert yaml_path.is_file()

    def test_list_empty(self, tmp_jig_project: Path):
        types = list_agent_types(tmp_jig_project)
        assert types == []

    def test_list_multiple(self, tmp_jig_project: Path):
        save_agent_type(tmp_jig_project, AgentTypeConfig(name="dev", system_prompt="Dev."))
        save_agent_type(tmp_jig_project, AgentTypeConfig(name="test", system_prompt="Test."))
        types = list_agent_types(tmp_jig_project)
        names = {t.name for t in types}
        assert names == {"dev", "test"}

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_agent_type(tmp_jig_project, "nope")


from jig.persistence import save_default_agent_types


class TestDefaultAgentTypes:
    def test_creates_four_types(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        types = list_agent_types(tmp_jig_project)
        names = {t.name for t in types}
        assert names == {"spec", "test", "dev", "review"}

    def test_each_has_system_prompt(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_jig_project, name)
            assert len(config.system_prompt) > 0

    def test_each_has_allowed_tools(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_jig_project, name)
            assert len(config.allowed_tools) > 0

    def test_each_has_default_context(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_jig_project, name)
            assert len(config.default_context) > 0


from jig.models import Task, CompletionState
from jig.persistence import save_task, load_task


class TestTaskPersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        task = Task(
            id="task-1",
            description="Implement feature",
            acceptance_criteria="Tests pass",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)
        loaded = load_task(tmp_jig_project, "issue-1", "task-1")
        assert loaded.id == "task-1"
        assert loaded.description == "Implement feature"
        assert loaded.agent_type == "dev"

    def test_saves_to_correct_path(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        task = Task(id="task-1", description="Do thing", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        task_path = tmp_jig_project / ".jig" / "issues" / "issue-1" / "tasks" / "task-1.yaml"
        assert task_path.is_file()

    def test_update_with_completion(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        task = Task(id="task-1", description="Do thing", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        task.completion_state = CompletionState.SUCCESS
        task.completion_reason = "All tests pass"
        save_task(tmp_jig_project, "issue-1", task)
        loaded = load_task(tmp_jig_project, "issue-1", "task-1")
        assert loaded.completion_state == CompletionState.SUCCESS
        assert loaded.completion_reason == "All tests pass"

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        with pytest.raises(FileNotFoundError):
            load_task(tmp_jig_project, "issue-1", "nope")
