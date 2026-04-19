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
