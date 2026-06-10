"""Tests for ``jig.profile_loader``.

Covers ``load_profile``, ``apply_profile``, ``copy_profile_templates``,
and ``list_profiles``. The two shipped profiles (``small``, ``medium``)
are used as fixtures; tests run against ``tmp_path`` for filesystem
isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.config import Config
from jig.profile_loader import (
    apply_profile,
    copy_profile_templates,
    list_profiles,
    load_profile,
)
from jig.project import Project


def _bare_config(tmp_path: Path) -> Config:
    return Config(
        project=Project(id="t", name="t", path=str(tmp_path)),
    )


class TestLoadProfile:
    def test_loads_shipped_small(self) -> None:
        p = load_profile("small")
        assert p.name == "small"
        assert p.sa_role == "sa"
        # xs now maps to feature-s (feature-xs deleted; every size in the
        # small profile goes through the test-included workflow).
        assert p.workflows.default_by_size["xs"] == "feature-s"

    def test_loads_shipped_medium(self) -> None:
        p = load_profile("medium")
        assert p.name == "medium"
        # medium drives the L0–L3 PO pipeline + module-producing SA.
        assert p.sa_role == "sa_mvp"
        assert p.workflows.default_by_size["s"] == "feature-s-full"

    def test_project_local_wins_over_shipped(self, tmp_path: Path) -> None:
        # Project-local override with a different sa_role; the loader
        # must prefer it over the shipped default.
        (tmp_path / ".jig" / "profiles").mkdir(parents=True)
        (tmp_path / ".jig" / "profiles" / "small.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "small",
                    "description": "Customized small",
                    "sa_role": "custom-sa",
                    "workflows": {
                        "default_by_size": {"s": "feature-s"},
                        "available": ["feature-s"],
                    },
                }
            )
        )
        p = load_profile("small", project_path=tmp_path)
        assert p.sa_role == "custom-sa"
        assert p.description == "Customized small"

    def test_unknown_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="large"):
            load_profile("large", project_path=tmp_path)


class TestApplyProfile:
    def test_writes_profile_section(self, tmp_path: Path) -> None:
        cfg = _bare_config(tmp_path)
        assert cfg.profile.name == ""  # default
        applied = apply_profile(cfg, load_profile("medium"))
        assert applied.profile.name == "medium"
        assert applied.profile.sa_role == "sa_mvp"

    def test_merges_workflow_routing(self, tmp_path: Path) -> None:
        cfg = _bare_config(tmp_path)
        applied = apply_profile(cfg, load_profile("small"))
        assert applied.workflows.default_by_size["xs"] == "feature-s"
        assert applied.workflows.default_by_size["s"] == "feature-s"
        # Profile's allowlist replaces the workflow's available list.
        assert "feature-s" in applied.workflows.available
        # feature-xs no longer in the allowlist — workflow was deleted.
        assert "feature-xs" not in applied.workflows.available

    def test_does_not_mutate_input(self, tmp_path: Path) -> None:
        cfg = _bare_config(tmp_path)
        before = cfg.model_dump_json()
        apply_profile(cfg, load_profile("medium"))
        assert cfg.model_dump_json() == before  # input unchanged


class TestCopyProfileTemplates:
    def test_copies_profile_and_referenced_workflows(self, tmp_path: Path) -> None:
        copy_profile_templates(load_profile("medium"), tmp_path)
        assert (tmp_path / ".jig" / "profiles" / "medium.yaml").is_file()
        # medium references feature-s, feature-s-full, default, etc.
        # — each must land in .jig/workflows/. (feature-xs was deleted;
        # xs now maps to feature-s.)
        assert (tmp_path / ".jig" / "workflows" / "feature-s.yaml").is_file()
        assert (tmp_path / ".jig" / "workflows" / "feature-s-full.yaml").is_file()
        assert (tmp_path / ".jig" / "workflows" / "default.yaml").is_file()

    def test_idempotent_when_files_exist(self, tmp_path: Path) -> None:
        # First copy.
        copy_profile_templates(load_profile("small"), tmp_path)
        feature_s = tmp_path / ".jig" / "workflows" / "feature-s.yaml"
        # Operator edit — must be preserved on re-copy.
        feature_s.write_text("name: feature-s\nphases: []\n# operator-edited\n")
        copy_profile_templates(load_profile("small"), tmp_path)
        assert "operator-edited" in feature_s.read_text()

    def test_copies_check_catalog(self, tmp_path: Path) -> None:
        """Profile apply seeds ``.jig/checks.yaml`` from the shipped
        catalog so operators have an editable file alongside their
        profile and workflow YAMLs."""
        copy_profile_templates(load_profile("small"), tmp_path)
        checks_file = tmp_path / ".jig" / "checks.yaml"
        assert checks_file.is_file()
        body = checks_file.read_text()
        # All five shipped catalog entries land in the project copy.
        for name in (
            "pytest-all",
            "ruff-check",
            "mypy-strict",
            "pytest-diff-tests",
            "pytest-new-tests-fail",
        ):
            assert name in body

    def test_check_catalog_preserves_operator_edits(self, tmp_path: Path) -> None:
        """Operator edits to ``.jig/checks.yaml`` survive a subsequent
        profile-apply (the copy is one-way and skips when dest
        exists)."""
        copy_profile_templates(load_profile("small"), tmp_path)
        checks_file = tmp_path / ".jig" / "checks.yaml"
        checks_file.write_text(
            "checks:\n  custom-check:\n    type: scripted\n    command: 'echo ok'\n"
        )
        copy_profile_templates(load_profile("small"), tmp_path)
        body = checks_file.read_text()
        assert "custom-check" in body
        # Shipped names should NOT have been re-copied over the edit.
        assert "pytest-all" not in body


class TestListProfiles:
    def test_lists_both_shipped(self) -> None:
        names = [p.name for p in list_profiles()]
        assert names == ["medium", "small"]  # sorted

    def test_project_local_wins(self, tmp_path: Path) -> None:
        (tmp_path / ".jig" / "profiles").mkdir(parents=True)
        (tmp_path / ".jig" / "profiles" / "small.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "small",
                    "description": "Local override",
                    "sa_role": "custom-sa",
                    "workflows": {"default_by_size": {}, "available": []},
                }
            )
        )
        profiles_by_name = {p.name: p for p in list_profiles(tmp_path)}
        assert profiles_by_name["small"].sa_role == "custom-sa"
        # medium still comes from shipped.
        assert profiles_by_name["medium"].sa_role == "sa_mvp"
