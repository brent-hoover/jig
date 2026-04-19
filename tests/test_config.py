"""Tests for jig.config — nested `.jig/config.yaml` schema."""

from pathlib import Path

import pytest
import yaml

from jig.config import (
    Config,
    OwnershipSection,
    RoleAssignment,
    RolesSection,
    WorkflowsSection,
    WorkflowTypeEntry,
    load_config,
    save_config,
)
from jig.project import Project


@pytest.fixture
def tmp_jig(tmp_path: Path) -> Path:
    (tmp_path / ".jig").mkdir()
    return tmp_path


class TestConfigModel:
    def test_minimal_roundtrip(self, tmp_jig: Path) -> None:
        config = Config(project=Project(id="p", name="p", path=str(tmp_jig)))
        save_config(tmp_jig, config)
        loaded = load_config(tmp_jig)
        assert loaded == config

    def test_defaults_fill_in_missing_sections(self, tmp_jig: Path) -> None:
        (tmp_jig / ".jig" / "config.yaml").write_text(
            yaml.safe_dump(
                {"project": {"id": "p", "name": "p", "path": str(tmp_jig)}}
            )
        )
        loaded = load_config(tmp_jig)
        assert isinstance(loaded.workflows, WorkflowsSection)
        assert loaded.workflows.available == []
        assert loaded.ownership.spec.behaviors == "po"
        assert loaded.escalation.default_human == ""

    def test_load_missing_raises(self, tmp_jig: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_jig)


class TestWorkflowsByType:
    """Phase 1C only parses — phase 2 consumes."""

    def test_by_type_accepted(self, tmp_jig: Path) -> None:
        yaml_text = """
project: {id: p, name: p, path: "%s"}
workflows:
  default_by_size: {xs: hotfix, s: small-change, m: standard, l: large-feature, xl: epic}
  available: [hotfix, small-change, standard, large-feature, epic]
  by_type:
    feature:
      default_by_size: {m: standard, l: large-feature}
      available: [standard, large-feature]
    bugfix:
      default_by_size: {s: hotfix, m: small-change}
      available: [hotfix, small-change]
""" % tmp_jig
        (tmp_jig / ".jig" / "config.yaml").write_text(yaml_text)
        cfg = load_config(tmp_jig)

        assert cfg.workflows.default_by_size["m"] == "standard"
        assert isinstance(cfg.workflows.by_type["feature"], WorkflowTypeEntry)
        assert cfg.workflows.by_type["feature"].available == ["standard", "large-feature"]
        assert cfg.workflows.by_type["bugfix"].default_by_size["s"] == "hotfix"


class TestOwnershipAccepted:
    """Phase 1C parses; phase 3 enforces."""

    def test_round_trip(self, tmp_jig: Path) -> None:
        config = Config(
            project=Project(id="p", name="p", path=str(tmp_jig)),
            ownership=OwnershipSection(architecture="sa", roadmap="po"),
            roles=RolesSection(
                po=RoleAssignment(
                    assignment="human_with_helper",
                    human="alice@example.com",
                    helper_template="po-helper",
                ),
                sa=RoleAssignment(
                    assignment="human_with_helper",
                    human="bob@example.com",
                    helper_template="sa-helper",
                ),
            ),
        )
        save_config(tmp_jig, config)
        loaded = load_config(tmp_jig)
        assert loaded.roles.po is not None
        assert loaded.roles.po.human == "alice@example.com"
        assert loaded.ownership.architecture == "sa"
