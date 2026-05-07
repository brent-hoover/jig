"""Tests for jig.models — role_cfg, phase, and workflow configs."""

import pytest
from pydantic import ValidationError

from jig.models import RoleConfig, PhaseConfig, WorkflowConfig


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


class TestRoleConfig:
    def test_defaults(self) -> None:
        config = RoleConfig(role="dev", phase_prompt="You are a dev agent.")
        assert config.role == "dev"
        assert config.phase_prompt == "You are a dev agent."
        assert config.response_prompt == ""
        assert config.allowed_tools == []
        assert config.default_context == []
        assert config.allowed_mcps == []

    def test_full_config(self) -> None:
        config = RoleConfig(
            role="test",
            phase_prompt="You are a test agent.",
            response_prompt="You are answering a question.",
            allowed_tools=["Read", "Bash", "Grep"],
            default_context=["ticket://design", "**/*_test.py"],
        )
        assert config.phase_prompt == "You are a test agent."
        assert config.response_prompt == "You are answering a question."
        assert config.allowed_tools == ["Read", "Bash", "Grep"]
        assert config.default_context == ["ticket://design", "**/*_test.py"]

    def test_serialization_roundtrip(self) -> None:
        config = RoleConfig(
            role="dev",
            phase_prompt="You are a dev agent.",
            response_prompt="You are answering a question.",
            allowed_tools=["Read", "Edit"],
        )
        data = config.model_dump()
        restored = RoleConfig.model_validate(data)
        assert restored.role == config.role
        assert restored.phase_prompt == config.phase_prompt
        assert restored.response_prompt == config.response_prompt
        assert restored.allowed_tools == config.allowed_tools

    def test_rejects_unknown_fields(self) -> None:
        """RoleConfig is now ``extra="forbid"`` (SEC-I3) so YAML typos
        and stale fields like the long-removed ``can_message`` fail
        loud at load instead of silently no-opping."""
        data = {
            "role": "dev",
            "phase_prompt": "test",
            "can_message": ["spec"],
        }
        with pytest.raises(ValidationError, match="can_message"):
            RoleConfig.model_validate(data)
