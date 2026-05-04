"""Tests for jig.persistence — init_project, agent types, workflows."""

from pathlib import Path

import pytest

from jig.models import RoleConfig, PhaseConfig, WorkflowConfig
from jig.persistence import (
    init_project,
    list_roles,
    load_role,
    load_workflow,
    save_role,
    save_default_roles,
    save_default_workflow,
    save_workflow,
)


@pytest.fixture
def tmp_new_jig_project(tmp_path: Path) -> Path:
    """A git repo with the new .jig/ layout already initialized."""
    (tmp_path / ".git").mkdir()
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir()
    for subdir in ("roles", "workflows", "worktrees", "store"):
        (jig_dir / subdir).mkdir()
    return tmp_path


class TestInitProject:
    def test_creates_jig_directory(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert jig_dir.is_dir()
        assert (jig_dir / "roles").is_dir()
        assert (jig_dir / "workflows").is_dir()
        assert (jig_dir / "worktrees").is_dir()
        assert (jig_dir / "store").is_dir()

    def test_creates_doc17_placeholders(self, tmp_project: Path) -> None:
        """Per doc 17 layout — populated in later phases, laid down now."""
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert (jig_dir / "spec").is_dir()
        assert (jig_dir / "context" / "project").is_dir()
        assert (jig_dir / "context" / "roles").is_dir()
        assert (jig_dir / "decisions").is_dir()
        assert (jig_dir / "archive").is_dir()

    def test_creates_project_spec_stub(self, tmp_project: Path) -> None:
        """`.jig/spec/project.md` ships as a PO-facing template.

        Per docs/02-project-spec.md §"Human format example" the PO's
        brief has state-category level-2 headers. The stub mirrors that
        shape so the PO has somewhere concrete to start writing.
        """
        init_project(tmp_project)
        spec_path = tmp_project / ".jig" / "spec" / "project.md"
        assert spec_path.is_file()
        body = spec_path.read_text()
        # Every state-category header from doc 02 must be present.
        for header in (
            "## Built",
            "## Planned (committed)",
            "## Planned (not yet committed)",
            "## Backlog",
            "## Non-goals",
        ):
            assert header in body, f"missing {header!r} in project.md stub"
        # Top-level heading is derived from the project's directory name
        # so a git clone doesn't come with a title that claims to be a
        # different project.
        assert f"# {tmp_project.name}" in body

    def test_creates_empty_checks_catalog(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        checks_path = tmp_project / ".jig" / "checks.yaml"
        assert checks_path.is_file()
        import yaml

        data = yaml.safe_load(checks_path.read_text())
        assert data == {"checks": {}}

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
        config = RoleConfig(
            role="dev",
            phase_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        save_role(tmp_new_jig_project, config)
        loaded = load_role(tmp_new_jig_project, "dev")
        assert loaded.role == "dev"
        assert loaded.phase_prompt == "You are a dev agent."
        assert loaded.allowed_tools == ["Read", "Edit"]

    def test_saves_to_correct_path(self, tmp_new_jig_project: Path) -> None:
        config = RoleConfig(role="test", phase_prompt="Test agent.")
        save_role(tmp_new_jig_project, config)
        yaml_path = tmp_new_jig_project / ".jig" / "roles" / "test.yaml"
        assert yaml_path.is_file()

    def test_list_empty_falls_back_to_shipped_defaults(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Phase 2 Task C: project override layer empty → serve shipped defaults."""
        types = list_roles(tmp_new_jig_project)
        roles = {t.role for t in types}
        # The exact set of shipped defaults is asserted in TestDefaultRoles.
        # Here we just need to know the fallback layer is in play.
        assert roles, "list_roles should fall back to shipped defaults"
        assert "dev" in roles

    def test_list_merges_project_override_with_shipped(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_role(
            tmp_new_jig_project,
            RoleConfig(role="dev", phase_prompt="Project dev override."),
        )
        save_role(
            tmp_new_jig_project,
            RoleConfig(role="custom-role", phase_prompt="Project only."),
        )
        types = {t.role: t for t in list_roles(tmp_new_jig_project)}
        # Shipped defaults show up.
        assert "pm" in types
        assert "spec" in types
        # Project-only role is present.
        assert "custom-role" in types
        # Project override wins for shared names.
        assert types["dev"].phase_prompt == "Project dev override."

    def test_load_role_prefers_project_override(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_role(
            tmp_new_jig_project,
            RoleConfig(role="dev", phase_prompt="Custom project dev."),
        )
        loaded = load_role(tmp_new_jig_project, "dev")
        assert loaded.phase_prompt == "Custom project dev."

    def test_load_role_falls_back_to_shipped_default(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Project override missing → serve shipped default transparently."""
        loaded = load_role(tmp_new_jig_project, "dev")
        # dev.yaml ships in jig/defaults/roles/ — should resolve.
        assert loaded.role == "dev"

    def test_load_nonexistent_raises(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_role(tmp_new_jig_project, "nope")


class TestDefaultRoles:
    def test_creates_all_types(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        types = list_roles(tmp_new_jig_project)
        roles = {t.role for t in types}
        # ``user`` is a shipped pseudo-role (no phase_prompt, no allowed_tools)
        # — never dispatched to Claude Code; carries default waiver capability
        # for future user-driven waive flows. It surfaces via the shipped
        # fallthrough in ``list_roles`` even when not explicitly copied.
        assert roles == {
            "spec",
            "test",
            "dev",
            "review",
            "validate",
            "document",
            "pm",
            "user",
            "po",
            "po-l0",
            "po-l1",
            "po-l3",
            "sa",
            "sa-v2",
            "planner-pm",
            "spec-generator",
            "concierge",
            "quartermaster",
        }

    def test_each_has_phase_prompt(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review", "validate", "document", "pm"):
            config = load_role(tmp_new_jig_project, name)
            assert len(config.phase_prompt) > 0

    def test_each_has_allowed_tools(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review", "validate", "document", "pm"):
            config = load_role(tmp_new_jig_project, name)
            assert len(config.allowed_tools) > 0

    def test_each_has_default_context(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_role(tmp_new_jig_project, name)
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

    def test_load_workflow_falls_back_to_shipped_default(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Project override missing → serve shipped default."""
        workflow = load_workflow(tmp_new_jig_project, "default")
        assert workflow.name == "default"

    def test_load_workflow_prefers_project_override(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_workflow(
            tmp_new_jig_project,
            WorkflowConfig(
                name="default",
                phases=[PhaseConfig(name="only", role="dev")],
            ),
        )
        loaded = load_workflow(tmp_new_jig_project, "default")
        assert [p.name for p in loaded.phases] == ["only"]


class TestDefaultWorkflow:
    def test_creates_default(self, tmp_new_jig_project: Path) -> None:
        save_default_workflow(tmp_new_jig_project)
        workflow = load_workflow(tmp_new_jig_project, "default")
        assert workflow.name == "default"
        phase_names = [p.name for p in workflow.phases]
        assert phase_names == [
            "spec",
            "test",
            "implement",
            "review",
            "validate",
            "document",
        ]

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
