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
        assert p.workflows.default_by_size["xs"] == "feature-xs"

    def test_loads_shipped_medium(self) -> None:
        p = load_profile("medium")
        assert p.name == "medium"
        assert p.sa_role == "sa"  # medium currently routes to basic sa (sa_mvp deferred)
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
        assert applied.profile.sa_role == "sa"  # medium currently routes to basic sa (sa_mvp deferred)

    def test_merges_workflow_routing(self, tmp_path: Path) -> None:
        cfg = _bare_config(tmp_path)
        applied = apply_profile(cfg, load_profile("small"))
        assert applied.workflows.default_by_size["xs"] == "feature-xs"
        assert applied.workflows.default_by_size["s"] == "feature-s"
        # Profile's allowlist replaces the workflow's available list.
        assert "feature-xs" in applied.workflows.available
        assert "feature-s" in applied.workflows.available

    def test_does_not_mutate_input(self, tmp_path: Path) -> None:
        cfg = _bare_config(tmp_path)
        before = cfg.model_dump_json()
        apply_profile(cfg, load_profile("medium"))
        assert cfg.model_dump_json() == before  # input unchanged


class TestCopyProfileTemplates:
    def test_copies_profile_and_referenced_workflows(self, tmp_path: Path) -> None:
        copy_profile_templates(load_profile("medium"), tmp_path)
        assert (tmp_path / ".jig" / "profiles" / "medium.yaml").is_file()
        # medium references feature-xs, feature-s-full, default, etc.
        # — each must land in .jig/workflows/.
        assert (tmp_path / ".jig" / "workflows" / "feature-s-full.yaml").is_file()
        assert (tmp_path / ".jig" / "workflows" / "feature-xs.yaml").is_file()
        assert (tmp_path / ".jig" / "workflows" / "default.yaml").is_file()

    def test_idempotent_when_files_exist(self, tmp_path: Path) -> None:
        # First copy.
        copy_profile_templates(load_profile("small"), tmp_path)
        feature_s = tmp_path / ".jig" / "workflows" / "feature-s.yaml"
        # Operator edit — must be preserved on re-copy.
        feature_s.write_text("name: feature-s\nphases: []\n# operator-edited\n")
        copy_profile_templates(load_profile("small"), tmp_path)
        assert "operator-edited" in feature_s.read_text()


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
        assert profiles_by_name["medium"].sa_role == "sa"  # medium currently routes to basic sa (sa_mvp deferred)
