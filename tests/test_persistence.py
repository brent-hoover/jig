"""Tests for jig.persistence — init_project, agent types, workflows."""

from pathlib import Path

import pytest

from jig.models import AgentTypeConfig, PhaseConfig, WorkflowConfig
from jig.persistence import (
    init_project,
    list_agent_types,
    load_agent_type,
    load_workflow,
    save_agent_type,
    save_default_agent_types,
    save_default_workflow,
    save_workflow,
)


@pytest.fixture
def tmp_new_jig_project(tmp_path: Path) -> Path:
    """A git repo with the new .jig/ layout already initialized."""
    (tmp_path / ".git").mkdir()
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir()
    for subdir in ("agent_types", "workflows", "worktrees", "store"):
        (jig_dir / subdir).mkdir()
    return tmp_path


class TestInitProject:
    def test_creates_jig_directory(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert jig_dir.is_dir()
        assert (jig_dir / "agent_types").is_dir()
        assert (jig_dir / "workflows").is_dir()
        assert (jig_dir / "worktrees").is_dir()
        assert (jig_dir / "store").is_dir()

    def test_does_not_create_legacy_dirs(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert not (jig_dir / "issues").exists()
        assert not (jig_dir / "config.yaml").exists()
        assert not (jig_dir / "agents").exists()

    def test_raises_if_already_initialized(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileExistsError):
            init_project(tmp_new_jig_project)

    def test_raises_if_not_git_repo(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a git repository"):
            init_project(tmp_path)


class TestAgentTypePersistence:
    def test_save_and_load(self, tmp_new_jig_project: Path) -> None:
        config = AgentTypeConfig(
            role="dev",
            phase_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        save_agent_type(tmp_new_jig_project, config)
        loaded = load_agent_type(tmp_new_jig_project, "dev")
        assert loaded.role == "dev"
        assert loaded.phase_prompt == "You are a dev agent."
        assert loaded.allowed_tools == ["Read", "Edit"]

    def test_saves_to_correct_path(self, tmp_new_jig_project: Path) -> None:
        config = AgentTypeConfig(role="test", phase_prompt="Test agent.")
        save_agent_type(tmp_new_jig_project, config)
        yaml_path = tmp_new_jig_project / ".jig" / "agent_types" / "test.yaml"
        assert yaml_path.is_file()

    def test_list_empty(self, tmp_new_jig_project: Path) -> None:
        types = list_agent_types(tmp_new_jig_project)
        assert types == []

    def test_list_multiple(self, tmp_new_jig_project: Path) -> None:
        save_agent_type(
            tmp_new_jig_project, AgentTypeConfig(role="dev", phase_prompt="Dev.")
        )
        save_agent_type(
            tmp_new_jig_project, AgentTypeConfig(role="test", phase_prompt="Test.")
        )
        types = list_agent_types(tmp_new_jig_project)
        roles = {t.role for t in types}
        assert roles == {"dev", "test"}

    def test_load_nonexistent_raises(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_agent_type(tmp_new_jig_project, "nope")


class TestDefaultAgentTypes:
    def test_creates_all_types(self, tmp_new_jig_project: Path) -> None:
        save_default_agent_types(tmp_new_jig_project)
        types = list_agent_types(tmp_new_jig_project)
        roles = {t.role for t in types}
        assert roles == {"spec", "test", "dev", "review", "validate", "document"}

    def test_each_has_phase_prompt(self, tmp_new_jig_project: Path) -> None:
        save_default_agent_types(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review", "validate", "document"):
            config = load_agent_type(tmp_new_jig_project, name)
            assert len(config.phase_prompt) > 0

    def test_each_has_allowed_tools(self, tmp_new_jig_project: Path) -> None:
        save_default_agent_types(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review", "validate", "document"):
            config = load_agent_type(tmp_new_jig_project, name)
            assert len(config.allowed_tools) > 0

    def test_each_has_default_context(self, tmp_new_jig_project: Path) -> None:
        save_default_agent_types(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_new_jig_project, name)
            assert len(config.default_context) > 0


class TestWorkflowPersistence:
    def test_save_and_load(self, tmp_new_jig_project: Path) -> None:
        workflow = WorkflowConfig(
            name="custom",
            phases=[
                PhaseConfig(name="spec", role="spec"),
                PhaseConfig(name="test", role="test"),
            ],
        )
        save_workflow(tmp_new_jig_project, workflow)
        loaded = load_workflow(tmp_new_jig_project, "custom")
        assert loaded.name == "custom"
        assert len(loaded.phases) == 2

    def test_saves_to_correct_path(self, tmp_new_jig_project: Path) -> None:
        workflow = WorkflowConfig(
            name="custom",
            phases=[PhaseConfig(name="spec", role="spec")],
        )
        save_workflow(tmp_new_jig_project, workflow)
        path = tmp_new_jig_project / ".jig" / "workflows" / "custom.yaml"
        assert path.is_file()

    def test_load_nonexistent_raises(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_workflow(tmp_new_jig_project, "nope")


class TestDefaultWorkflow:
    def test_creates_default(self, tmp_new_jig_project: Path) -> None:
        save_default_workflow(tmp_new_jig_project)
        workflow = load_workflow(tmp_new_jig_project, "default")
        assert workflow.name == "default"
        phase_names = [p.name for p in workflow.phases]
        assert phase_names == ["spec", "test", "implement", "review", "validate", "document"]

    def test_roles_correct(self, tmp_new_jig_project: Path) -> None:
        save_default_workflow(tmp_new_jig_project)
        workflow = load_workflow(tmp_new_jig_project, "default")
        roles = {p.name: p.role for p in workflow.phases}
        assert roles == {
            "spec": "spec",
            "test": "test",
            "implement": "dev",
            "review": "review",
            "validate": "validate",
            "document": "document",
        }
