"""Tests for jig.catalog.validate_catalog (Phase 2 Task F).

Covers YAML shape errors, unknown references (roles / workflows / checks)
and bad ``required_context`` URIs. Also exercises both modes: default
fail-fast (raises ``CatalogError``) and ``collect=True`` (returns all
errors without raising).
"""

from pathlib import Path

import pytest
import yaml

from jig.catalog import CatalogError, validate_catalog
from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.persistence import init_project, save_role, save_workflow


@pytest.fixture
def initialized_project(tmp_path: Path) -> Path:
    """``jig init`` equivalent: git repo + the .jig layout."""
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    return tmp_path


def _save_config(project_path: Path, workflows: dict) -> None:
    (project_path / ".jig" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "project": {
                    "id": "p",
                    "name": "p",
                    "path": str(project_path),
                    "default_branch": "main",
                },
                "workflows": workflows,
            }
        )
    )


class TestHappyPath:
    def test_shipped_defaults_validate(self, initialized_project: Path) -> None:
        """Fresh project with shipped defaults only — must pass fail-loud."""
        validate_catalog(initialized_project)  # raises on failure

    def test_collect_returns_empty_list(self, initialized_project: Path) -> None:
        errors = validate_catalog(initialized_project, collect=True)
        assert errors == []


class TestRoleReferences:
    def test_unknown_role_fails_loud(self, initialized_project: Path) -> None:
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="bad",
                phases=[PhaseConfig(name="x", role="nonexistent-role")],
            ),
        )
        with pytest.raises(CatalogError, match="nonexistent-role"):
            validate_catalog(initialized_project)

    def test_unknown_role_in_collect(self, initialized_project: Path) -> None:
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="bad",
                phases=[PhaseConfig(name="x", role="nonexistent-role")],
            ),
        )
        errors = validate_catalog(initialized_project, collect=True) or []
        assert any("nonexistent-role" in e for e in errors)

    def test_project_override_role_counts(self, initialized_project: Path) -> None:
        """A project-only role satisfies a phase's role reference."""
        save_role(
            initialized_project,
            RoleConfig(role="speciale", phase_prompt="do special things"),
        )
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="s",
                phases=[PhaseConfig(name="p", role="speciale")],
            ),
        )
        validate_catalog(initialized_project)


class TestCheckReferences:
    def test_unknown_check_fails(self, initialized_project: Path) -> None:
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="w",
                phases=[
                    PhaseConfig(
                        name="p",
                        role="dev",
                        automated_checks=["no-such-check"],
                    )
                ],
            ),
        )
        with pytest.raises(CatalogError, match="no-such-check"):
            validate_catalog(initialized_project)

    def test_known_check_passes(self, initialized_project: Path) -> None:
        (initialized_project / ".jig" / "checks.yaml").write_text(
            yaml.safe_dump(
                {
                    "checks": {
                        "lint": {"type": "scripted", "command": "ruff check ."}
                    }
                }
            )
        )
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="w",
                phases=[
                    PhaseConfig(
                        name="p", role="dev", automated_checks=["lint"]
                    )
                ],
            ),
        )
        validate_catalog(initialized_project)


class TestConfigWorkflowReferences:
    def test_unknown_default_by_size_fails(self, initialized_project: Path) -> None:
        _save_config(
            initialized_project,
            {"default_by_size": {"m": "phantom"}},
        )
        with pytest.raises(CatalogError, match="phantom"):
            validate_catalog(initialized_project)

    def test_unknown_available_fails(self, initialized_project: Path) -> None:
        _save_config(
            initialized_project,
            {"available": ["phantom"]},
        )
        with pytest.raises(CatalogError, match="phantom"):
            validate_catalog(initialized_project)

    def test_unknown_by_type_fails(self, initialized_project: Path) -> None:
        _save_config(
            initialized_project,
            {
                "by_type": {
                    "feature": {
                        "default_by_size": {"m": "ghost"},
                        "available": ["ghost"],
                    }
                }
            },
        )
        errors = validate_catalog(initialized_project, collect=True) or []
        # Both ``by_type.feature.default_by_size.m`` and
        # ``by_type.feature.available`` should flag "ghost".
        assert sum("ghost" in e for e in errors) >= 2


class TestRequiredContextURIs:
    def test_missing_project_uri_fails(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="custom",
                phase_prompt="",
                required_context=["project://missing.md"],
            ),
        )
        with pytest.raises(CatalogError, match="missing.md"):
            validate_catalog(initialized_project)

    def test_present_project_uri_passes(
        self, initialized_project: Path
    ) -> None:
        (
            initialized_project
            / ".jig"
            / "context"
            / "project"
            / "principles.md"
        ).write_text("k")
        save_role(
            initialized_project,
            RoleConfig(
                role="custom",
                phase_prompt="",
                required_context=["project://principles.md"],
            ),
        )
        validate_catalog(initialized_project)

    def test_missing_role_uri_fails(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="custom",
                phase_prompt="",
                required_context=["role://custom/missing.md"],
            ),
        )
        with pytest.raises(CatalogError, match="missing.md"):
            validate_catalog(initialized_project)

    def test_ticket_and_repo_uris_skipped(self, initialized_project: Path) -> None:
        """Per-spawn schemes aren't checkable at load — they just pass."""
        save_role(
            initialized_project,
            RoleConfig(
                role="custom",
                phase_prompt="",
                required_context=[
                    "ticket://description",
                    "repo://README.md",
                    "decision://DR-0001",
                ],
            ),
        )
        validate_catalog(initialized_project)

    def test_uri_missing_scheme_fails(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="custom",
                phase_prompt="",
                required_context=["just/a/path.md"],
            ),
        )
        with pytest.raises(CatalogError, match="missing scheme"):
            validate_catalog(initialized_project)

    def test_malformed_role_uri_fails(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="custom",
                phase_prompt="",
                required_context=["role://just-a-name"],
            ),
        )
        with pytest.raises(CatalogError, match="role://"):
            validate_catalog(initialized_project)


class TestYAMLShape:
    def test_bad_yaml_in_role_fails(self, initialized_project: Path) -> None:
        (initialized_project / ".jig" / "roles" / "broken.yaml").write_text(
            "this: is: not: valid: yaml\n: : :\n"
        )
        with pytest.raises(CatalogError):
            validate_catalog(initialized_project)

    def test_bad_yaml_collected(self, initialized_project: Path) -> None:
        (initialized_project / ".jig" / "roles" / "broken.yaml").write_text(
            "this: is: not: valid: yaml\n: : :\n"
        )
        errors = validate_catalog(initialized_project, collect=True) or []
        assert any("broken" in e for e in errors)


class TestCollectMode:
    def test_collect_gathers_multiple(self, initialized_project: Path) -> None:
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="bad1",
                phases=[PhaseConfig(name="p", role="ghost1")],
            ),
        )
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="bad2",
                phases=[PhaseConfig(name="p", role="ghost2")],
            ),
        )
        errors = validate_catalog(initialized_project, collect=True) or []
        joined = " | ".join(errors)
        assert "ghost1" in joined
        assert "ghost2" in joined


# ---- Phase 3H: work-type schema + ownership + helper_template refs --------


def _write_config(project_path: Path, extra: dict) -> None:
    base = {
        "project": {
            "id": "p",
            "name": "p",
            "path": str(project_path),
            "default_branch": "main",
        }
    }
    base.update(extra)
    (project_path / ".jig" / "config.yaml").write_text(
        yaml.safe_dump(base)
    )


class TestWorkTypeSchemaValidation:
    def test_bad_yaml_in_work_type_schema_fails(
        self, initialized_project: Path
    ) -> None:
        """A malformed project work-type schema surfaces at load."""
        (
            initialized_project
            / ".jig"
            / "work_types"
            / "broken.yaml"
        ).write_text("this: is: not: valid: yaml\n: : :\n")
        with pytest.raises(CatalogError, match="broken"):
            validate_catalog(initialized_project)

    def test_missing_required_work_type_field_fails(
        self, initialized_project: Path
    ) -> None:
        """``work_type`` key is required by the WorkTypeSchema model."""
        (
            initialized_project
            / ".jig"
            / "work_types"
            / "weird.yaml"
        ).write_text(yaml.safe_dump({"required": ["summary"]}))
        with pytest.raises(CatalogError, match="weird"):
            validate_catalog(initialized_project)


class TestOwnershipSpecFieldReferences:
    def test_unknown_spec_field_fails(
        self, initialized_project: Path
    ) -> None:
        """config.ownership.spec.<bogus> flags a field unknown to every schema."""
        _write_config(
            initialized_project,
            {"ownership": {"spec": {"not_a_real_field": "po"}}},
        )
        with pytest.raises(CatalogError, match="not_a_real_field"):
            validate_catalog(initialized_project)

    def test_known_spec_field_passes(
        self, initialized_project: Path
    ) -> None:
        """A field declared by any shipped schema is accepted."""
        _write_config(
            initialized_project,
            {"ownership": {"spec": {"summary": "po"}}},
        )
        validate_catalog(initialized_project)

    def test_empty_owner_is_ignored(
        self, initialized_project: Path
    ) -> None:
        """Empty-string owner for a field isn't treated as a claim."""
        _write_config(
            initialized_project,
            {"ownership": {"spec": {"not_a_real_field": ""}}},
        )
        validate_catalog(initialized_project)


class TestRoleHelperTemplateReferences:
    def test_unknown_helper_template_fails(
        self, initialized_project: Path
    ) -> None:
        _write_config(
            initialized_project,
            {
                "roles": {
                    "po": {
                        "assignment": "human_with_helper",
                        "human": "alice",
                        "helper_template": "ghost-role",
                    }
                }
            },
        )
        with pytest.raises(CatalogError, match="ghost-role"):
            validate_catalog(initialized_project)

    def test_agent_assignment_helper_also_checked(
        self, initialized_project: Path
    ) -> None:
        """``agent`` mode still names a role — same validation."""
        _write_config(
            initialized_project,
            {
                "roles": {
                    "sa": {
                        "assignment": "agent",
                        "helper_template": "nope-role",
                    }
                }
            },
        )
        with pytest.raises(CatalogError, match="nope-role"):
            validate_catalog(initialized_project)

    def test_human_assignment_skips_helper_check(
        self, initialized_project: Path
    ) -> None:
        """``human`` mode doesn't need helper_template — blank is fine."""
        _write_config(
            initialized_project,
            {
                "roles": {
                    "po": {
                        "assignment": "human",
                        "human": "alice",
                    }
                }
            },
        )
        validate_catalog(initialized_project)

    def test_known_helper_template_passes(
        self, initialized_project: Path
    ) -> None:
        save_role(
            initialized_project,
            RoleConfig(role="helper-bot", phase_prompt="assist"),
        )
        _write_config(
            initialized_project,
            {
                "roles": {
                    "po": {
                        "assignment": "human_with_helper",
                        "human": "alice",
                        "helper_template": "helper-bot",
                    }
                }
            },
        )
        validate_catalog(initialized_project)
