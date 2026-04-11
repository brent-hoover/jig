"""Tests for jig.models — agent_type, phase, and workflow configs."""

from jig.models import AgentTypeConfig, PhaseConfig, WorkflowConfig


class TestPhaseConfig:
    def test_creation(self) -> None:
        phase = PhaseConfig(name="spec", role="spec")
        assert phase.name == "spec"
        assert phase.role == "spec"
        assert phase.task_template == ""
        assert phase.acceptance_criteria == ""

    def test_with_template(self) -> None:
        phase = PhaseConfig(
            name="spec",
            role="spec",
            task_template="Draft a design document for: {ticket_title}",
            acceptance_criteria="Design doc covers all requirements",
        )
        assert phase.task_template == "Draft a design document for: {ticket_title}"
        assert phase.acceptance_criteria == "Design doc covers all requirements"


class TestWorkflowConfig:
    def test_creation(self) -> None:
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

    def test_serialization_roundtrip(self) -> None:
        workflow = WorkflowConfig(
            name="default",
            phases=[PhaseConfig(name="spec", role="spec")],
        )
        data = workflow.model_dump()
        restored = WorkflowConfig.model_validate(data)
        assert restored.name == workflow.name
        assert len(restored.phases) == 1


class TestAgentTypeConfig:
    def test_defaults(self) -> None:
        config = AgentTypeConfig(role="dev", phase_prompt="You are a dev agent.")
        assert config.role == "dev"
        assert config.phase_prompt == "You are a dev agent."
        assert config.response_prompt == ""
        assert config.allowed_tools == []
        assert config.can_message == []
        assert config.default_context == []

    def test_full_config(self) -> None:
        config = AgentTypeConfig(
            role="test",
            phase_prompt="You are a test agent.",
            response_prompt="You are answering a question.",
            allowed_tools=["Read", "Bash", "Grep"],
            can_message=["dev", "user"],
            default_context=["ticket://design", "**/*_test.py"],
        )
        assert config.phase_prompt == "You are a test agent."
        assert config.response_prompt == "You are answering a question."
        assert config.allowed_tools == ["Read", "Bash", "Grep"]
        assert config.can_message == ["dev", "user"]
        assert config.default_context == ["ticket://design", "**/*_test.py"]

    def test_serialization_roundtrip(self) -> None:
        config = AgentTypeConfig(
            role="dev",
            phase_prompt="You are a dev agent.",
            response_prompt="You are answering a question.",
            allowed_tools=["Read", "Edit"],
            can_message=["spec", "user"],
        )
        data = config.model_dump()
        restored = AgentTypeConfig.model_validate(data)
        assert restored.role == config.role
        assert restored.phase_prompt == config.phase_prompt
        assert restored.response_prompt == config.response_prompt
        assert restored.allowed_tools == config.allowed_tools
        assert restored.can_message == config.can_message
