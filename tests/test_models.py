from jig.models import (
    ProjectConfig,
    IssueStatus,
    CompletionState,
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


from jig.models import PhaseConfig, WorkflowConfig


class TestPhaseConfig:
    def test_creation(self):
        phase = PhaseConfig(
            name="spec",
            role="spec",
        )
        assert phase.name == "spec"
        assert phase.role == "spec"
        assert phase.task_template == ""
        assert phase.acceptance_criteria == ""

    def test_with_template(self):
        phase = PhaseConfig(
            name="spec",
            role="spec",
            task_template="Draft a design document for: {issue_title}",
            acceptance_criteria="Design doc covers all requirements",
        )
        assert phase.task_template == "Draft a design document for: {issue_title}"
        assert phase.acceptance_criteria == "Design doc covers all requirements"


class TestWorkflowConfig:
    def test_creation(self):
        workflow = WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name="spec", role="spec"),
                PhaseConfig(name="test", role="test"),
            ],
        )
        assert workflow.name == "default"
        assert len(workflow.phases) == 2
        assert workflow.phases[0].name == "spec"
        assert workflow.phases[1].role == "test"

    def test_serialization_roundtrip(self):
        workflow = WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name="spec", role="spec"),
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
            role="dev",
            phase_prompt="You are a dev agent.",
        )
        assert config.role == "dev"
        assert config.phase_prompt == "You are a dev agent."
        assert config.allowed_tools == []
        assert config.default_context == []

    def test_full_config(self):
        config = AgentTypeConfig(
            role="test",
            phase_prompt="You are a test agent.",
            allowed_tools=["Read", "Bash", "Grep"],
            default_context=["issue://design", "**/*_test.py"],
        )
        assert config.allowed_tools == ["Read", "Bash", "Grep"]
        assert config.default_context == ["issue://design", "**/*_test.py"]

    def test_serialization_roundtrip(self):
        config = AgentTypeConfig(
            role="dev",
            phase_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        data = config.model_dump()
        restored = AgentTypeConfig.model_validate(data)
        assert restored.role == config.role
        assert restored.allowed_tools == config.allowed_tools


def test_agent_type_new_fields() -> None:
    cfg = AgentTypeConfig(
        role="dev",
        phase_prompt="You are a developer.",
        response_prompt="You are answering a question.",
        allowed_tools=["Read", "Edit"],
        can_message=["spec-writer", "user"],
    )
    assert cfg.phase_prompt == "You are a developer."
    assert cfg.response_prompt == "You are answering a question."
    assert cfg.can_message == ["spec-writer", "user"]


def test_agent_type_response_prompt_optional() -> None:
    cfg = AgentTypeConfig(role="dev", phase_prompt="be a dev")
    assert cfg.response_prompt == ""
    assert cfg.can_message == []
    assert cfg.default_context == []


from jig.models import MessageDirection, AgentMessage


class TestMessageDirection:
    def test_values(self):
        assert MessageDirection.REQUEST == "request"
        assert MessageDirection.RESPONSE == "response"


class TestAgentMessage:
    def test_creation(self):
        msg = AgentMessage(
            sender_id="dev-1",
            recipient_id="test-1",
            direction=MessageDirection.REQUEST,
            topic="api_design",
            content="What endpoints do we need?",
        )
        assert msg.sender_id == "dev-1"
        assert msg.recipient_id == "test-1"
        assert msg.direction == MessageDirection.REQUEST
        assert msg.topic == "api_design"
        assert msg.content == "What endpoints do we need?"
        assert msg.correlation_id is None
        assert msg.id
        assert msg.timestamp

    def test_with_correlation(self):
        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.RESPONSE,
            topic="api_design",
            content="We need GET /users and POST /users",
            correlation_id="corr-123",
        )
        assert msg.correlation_id == "corr-123"
        assert msg.direction == MessageDirection.RESPONSE

    def test_serialization_roundtrip(self):
        msg = AgentMessage(
            sender_id="dev-1",
            recipient_id="broadcast",
            direction=MessageDirection.REQUEST,
            topic="status_update",
            content="Starting implementation",
        )
        data = msg.model_dump(mode="json")
        restored = AgentMessage.model_validate(data)
        assert restored.sender_id == msg.sender_id
        assert restored.id == msg.id
        assert restored.topic == msg.topic


from jig.models import AgentStatus, AgentInstance


class TestAgentStatus:
    def test_values(self):
        assert AgentStatus.IDLE == "idle"
        assert AgentStatus.ACTIVE == "active"
        assert AgentStatus.DORMANT == "dormant"


class TestAgentInstance:
    def test_defaults(self):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
        )
        assert instance.id == "dev-1"
        assert instance.agent_type == "dev"
        assert instance.status == AgentStatus.IDLE
        assert instance.session_id is None
        assert instance.current_task_id is None
        assert instance.memory == []

    def test_active_with_task(self):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
            status=AgentStatus.ACTIVE,
            session_id="session-abc",
            current_task_id="task-1",
        )
        assert instance.status == AgentStatus.ACTIVE
        assert instance.session_id == "session-abc"
        assert instance.current_task_id == "task-1"

    def test_dormant_with_memory(self):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
            status=AgentStatus.DORMANT,
            session_id="session-abc",
            memory=["Prefer pytest over unittest", "Project uses FastAPI"],
        )
        assert instance.status == AgentStatus.DORMANT
        assert len(instance.memory) == 2

    def test_serialization_roundtrip(self):
        instance = AgentInstance(
            id="test-2",
            agent_type="test",
            status=AgentStatus.ACTIVE,
            session_id="sess-xyz",
            current_task_id="task-5",
            memory=["Use fixtures for DB setup"],
        )
        data = instance.model_dump(mode="json")
        restored = AgentInstance.model_validate(data)
        assert restored.id == "test-2"
        assert restored.status == AgentStatus.ACTIVE
        assert restored.memory == ["Use fixtures for DB setup"]


from jig.models import CompletionStatus, CompletionReport


class TestCompletionStatus:
    def test_values(self):
        assert CompletionStatus.SUCCESS == "success"
        assert CompletionStatus.NEEDS_INFO == "needs_info"
        assert CompletionStatus.BLOCKED == "blocked"
        assert CompletionStatus.FAILED == "failed"


class TestCompletionReport:
    def test_success(self):
        report = CompletionReport(
            agent_id="dev-1",
            task_id="task-1",
            status=CompletionStatus.SUCCESS,
            summary="Implemented auth module",
        )
        assert report.agent_id == "dev-1"
        assert report.status == CompletionStatus.SUCCESS
        assert report.reason == ""
        assert report.artifacts == []
        assert report.needs_from is None
        assert report.question is None

    def test_needs_info(self):
        report = CompletionReport(
            agent_id="dev-1",
            task_id="task-1",
            status=CompletionStatus.NEEDS_INFO,
            summary="Cannot determine API schema",
            reason="No API spec available",
            needs_from="spec",
            question="What are the required endpoints?",
        )
        assert report.needs_from == "spec"
        assert report.question == "What are the required endpoints?"

    def test_with_artifacts(self):
        report = CompletionReport(
            agent_id="dev-1",
            task_id="task-1",
            status=CompletionStatus.SUCCESS,
            summary="Wrote tests",
            artifacts=["tests/test_auth.py", "tests/test_users.py"],
        )
        assert len(report.artifacts) == 2

    def test_serialization_roundtrip(self):
        report = CompletionReport(
            agent_id="test-1",
            task_id="task-2",
            status=CompletionStatus.FAILED,
            summary="Tests failed",
            reason="Import error in auth module",
        )
        data = report.model_dump(mode="json")
        restored = CompletionReport.model_validate(data)
        assert restored.status == CompletionStatus.FAILED
        assert restored.reason == "Import error in auth module"
