from jig.models import (
    ProjectConfig,
    IssueStatus,
    CompletionState,
    MessageType,
)


class TestProjectConfig:
    def test_defaults(self):
        config = ProjectConfig(repo_path="/tmp/myrepo")
        assert config.repo_path == "/tmp/myrepo"
        assert config.default_branch == "main"

    def test_custom_branch(self):
        config = ProjectConfig(repo_path="/tmp/myrepo", default_branch="develop")
        assert config.default_branch == "develop"


class TestEnums:
    def test_issue_status_values(self):
        assert IssueStatus.PENDING == "pending"
        assert IssueStatus.IN_PROGRESS == "in_progress"
        assert IssueStatus.COMPLETED == "completed"
        assert IssueStatus.FAILED == "failed"

    def test_completion_state_values(self):
        assert CompletionState.SUCCESS == "success"
        assert CompletionState.NEEDS_INFO == "needs_info"
        assert CompletionState.BLOCKED == "blocked"
        assert CompletionState.FAILED == "failed"

    def test_message_type_values(self):
        assert MessageType.TASK_ASSIGNMENT == "task_assignment"
        assert MessageType.TASK_COMPLETION == "task_completion"
        assert MessageType.QUESTION == "question"
        assert MessageType.ANSWER == "answer"
        assert MessageType.CONTEXT_UPDATE == "context_update"
        assert MessageType.STATUS == "status"


from jig.models import Issue


class TestIssue:
    def test_defaults(self):
        issue = Issue(id="issue-1", title="Add auth")
        assert issue.id == "issue-1"
        assert issue.title == "Add auth"
        assert issue.status == IssueStatus.PENDING
        assert issue.current_phase is None
        assert issue.base_branch == "main"

    def test_custom_fields(self):
        issue = Issue(
            id="issue-2",
            title="Fix bug",
            status=IssueStatus.IN_PROGRESS,
            current_phase="implement",
            base_branch="develop",
        )
        assert issue.status == IssueStatus.IN_PROGRESS
        assert issue.current_phase == "implement"
        assert issue.base_branch == "develop"


from jig.models import Task


class TestTask:
    def test_defaults(self):
        task = Task(
            id="task-1",
            description="Write auth module",
            acceptance_criteria="All auth tests pass",
            agent_type="dev",
        )
        assert task.id == "task-1"
        assert task.input_context == []
        assert task.completion_state is None
        assert task.completion_reason is None

    def test_with_completion(self):
        task = Task(
            id="task-2",
            description="Review code",
            acceptance_criteria="No critical issues",
            agent_type="review",
            completion_state=CompletionState.NEEDS_INFO,
            completion_reason="Missing test coverage data",
        )
        assert task.completion_state == CompletionState.NEEDS_INFO
        assert task.completion_reason == "Missing test coverage data"


from datetime import datetime, timezone
from jig.models import Message


class TestMessage:
    def test_defaults(self):
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.STATUS,
        )
        assert msg.sender == "dev-agent"
        assert msg.recipient == "orchestrator"
        assert msg.type == MessageType.STATUS
        assert msg.payload == {}
        assert msg.correlation_id is None
        assert msg.id  # auto-generated
        assert msg.timestamp  # auto-generated

    def test_with_payload(self):
        msg = Message(
            sender="orchestrator",
            recipient="test-agent",
            type=MessageType.TASK_ASSIGNMENT,
            payload={"task_id": "task-1"},
            correlation_id="corr-123",
        )
        assert msg.payload == {"task_id": "task-1"}
        assert msg.correlation_id == "corr-123"

    def test_serialization_roundtrip(self):
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.TASK_COMPLETION,
            payload={"status": "done"},
        )
        data = msg.model_dump(mode="json")
        restored = Message.model_validate(data)
        assert restored.sender == msg.sender
        assert restored.recipient == msg.recipient
        assert restored.type == msg.type
        assert restored.payload == msg.payload
        assert restored.id == msg.id


from jig.models import WorkflowPhase, PhaseConfig, WorkflowConfig


class TestWorkflowPhase:
    def test_values(self):
        assert WorkflowPhase.SPEC == "spec"
        assert WorkflowPhase.TEST == "test"
        assert WorkflowPhase.IMPLEMENT == "implement"
        assert WorkflowPhase.REVIEW == "review"


class TestPhaseConfig:
    def test_creation(self):
        phase = PhaseConfig(
            name=WorkflowPhase.SPEC,
            agent_type="spec",
        )
        assert phase.name == "spec"
        assert phase.agent_type == "spec"


class TestWorkflowConfig:
    def test_creation(self):
        workflow = WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
                PhaseConfig(name=WorkflowPhase.TEST, agent_type="test"),
            ],
        )
        assert workflow.name == "default"
        assert len(workflow.phases) == 2
        assert workflow.phases[0].name == "spec"
        assert workflow.phases[1].agent_type == "test"

    def test_serialization_roundtrip(self):
        workflow = WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
            ],
        )
        data = workflow.model_dump()
        restored = WorkflowConfig.model_validate(data)
        assert restored.name == workflow.name
        assert len(restored.phases) == 1


from jig.models import AgentTypeConfig


class TestAgentTypeConfig:
    def test_defaults(self):
        config = AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
        )
        assert config.name == "dev"
        assert config.system_prompt == "You are a dev agent."
        assert config.allowed_tools == []
        assert config.denied_tools == []
        assert config.default_context == []

    def test_full_config(self):
        config = AgentTypeConfig(
            name="test",
            system_prompt="You are a test agent.",
            allowed_tools=["Read", "Bash", "Grep"],
            denied_tools=["Write"],
            default_context=["issue://design", "**/*_test.py"],
        )
        assert config.allowed_tools == ["Read", "Bash", "Grep"]
        assert config.denied_tools == ["Write"]
        assert config.default_context == ["issue://design", "**/*_test.py"]

    def test_serialization_roundtrip(self):
        config = AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        data = config.model_dump()
        restored = AgentTypeConfig.model_validate(data)
        assert restored.name == config.name
        assert restored.allowed_tools == config.allowed_tools
