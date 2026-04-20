"""Tests for jig.work_types — work-type schema loader (Phase 3 Tasks A + B).

Task A covers the loader + models (envelope + required_by_size + ownership).
Task B covers the shipped defaults — every shipped work type loads and the
size-scaled required lists match doc 03 intent.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jig.ticket import Size, WorkType
from jig.work_types import (
    WorkTypeSchema,
    list_work_type_names,
    list_work_type_schemas,
    load_work_type_schema,
)


def _write_schema(project_path: Path, name: str, data: dict) -> None:
    work_types_dir = project_path / ".jig" / "work_types"
    work_types_dir.mkdir(parents=True, exist_ok=True)
    (work_types_dir / f"{name}.yaml").write_text(yaml.safe_dump(data))


class TestShippedDefaults:
    def test_all_seven_shipped(self, tmp_path: Path) -> None:
        names = list_work_type_names(tmp_path)
        assert set(names) == {
            "feature", "bugfix", "refactor", "spike",
            "perf", "migration", "docs",
        }

    def test_each_shipped_loads(self, tmp_path: Path) -> None:
        for name in list_work_type_names(tmp_path):
            schema = load_work_type_schema(tmp_path, name)
            assert isinstance(schema, WorkTypeSchema)
            # Every shipped schema must declare at least `summary`.
            assert "summary" in schema.required

    def test_feature_schema_shape(self, tmp_path: Path) -> None:
        s = load_work_type_schema(tmp_path, "feature")
        assert s.work_type == WorkType.FEATURE
        assert set(s.required) >= {
            "summary", "behaviors", "acceptance_criteria", "out_of_scope",
        }
        # xs must shrink the required set.
        assert s.required_fields_for_size(Size.XS) == ["summary"]
        # xl must expand past required.
        xl = s.required_fields_for_size(Size.XL)
        assert "design" in xl
        assert "technical_risks" in xl

    def test_spike_has_no_behaviors(self, tmp_path: Path) -> None:
        """Doc 03 §Work types: spike 'may have no behaviors section'."""
        s = load_work_type_schema(tmp_path, "spike")
        assert "behaviors" not in s.allowed_fields()
        assert "question" in s.required
        assert "time_box" in s.required

    def test_docs_is_minimal(self, tmp_path: Path) -> None:
        s = load_work_type_schema(tmp_path, "docs")
        # Doc 03: docs skips design / technical_risks / behaviors.
        assert "design" not in s.allowed_fields()
        assert "technical_risks" not in s.allowed_fields()
        assert "behaviors" not in s.allowed_fields()

    def test_ownership_references_po_or_sa(self, tmp_path: Path) -> None:
        """Shipped defaults only lean on the two built-in roles per doc 04."""
        for name in list_work_type_names(tmp_path):
            s = load_work_type_schema(tmp_path, name)
            for field, owner in s.ownership.items():
                assert owner in {"po", "sa"}, (
                    f"{name}.{field} owner={owner!r} outside built-in set"
                )


class TestLoaderResolution:
    def test_project_override_wins(self, tmp_path: Path) -> None:
        _write_schema(
            tmp_path,
            "feature",
            {
                "work_type": "feature",
                "required": ["summary", "only_me"],
                "optional": [],
                "ownership": {"summary": "po", "only_me": "po"},
            },
        )
        s = load_work_type_schema(tmp_path, "feature")
        assert s.required == ["summary", "only_me"]

    def test_shipped_fallback_used(self, tmp_path: Path) -> None:
        s = load_work_type_schema(tmp_path, "bugfix")
        assert "regression_test" in s.required

    def test_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            load_work_type_schema(tmp_path, "nonexistent")

    def test_list_merges_layers(self, tmp_path: Path) -> None:
        _write_schema(
            tmp_path,
            "feature",
            {
                "work_type": "feature",
                "required": ["summary"],
            },
        )
        _write_schema(
            tmp_path,
            "custom",
            {
                "work_type": "feature",  # alias is OK for project-only
                "required": ["summary", "custom_field"],
            },
        )
        names = list_work_type_names(tmp_path)
        # custom (project-only) + 7 shipped, but 'feature' is shared.
        assert "custom" in names
        assert "feature" in names
        # 'feature' from project layer wins — check via list_work_type_schemas.
        schemas = {s.work_type.value: s for s in list_work_type_schemas(tmp_path)}
        assert "summary" in schemas["feature"].required
        # Project override replaced the shipped feature's required list.
        assert "behaviors" not in schemas["feature"].required


class TestRequiredBySize:
    def test_size_override_wins_when_declared(self, tmp_path: Path) -> None:
        _write_schema(
            tmp_path,
            "feature",
            {
                "work_type": "feature",
                "required": ["summary", "behaviors"],
                "required_by_size": {"xs": ["summary"]},
            },
        )
        s = load_work_type_schema(tmp_path, "feature")
        assert s.required_fields_for_size(Size.XS) == ["summary"]

    def test_unlisted_size_falls_back_to_required(self, tmp_path: Path) -> None:
        _write_schema(
            tmp_path,
            "feature",
            {
                "work_type": "feature",
                "required": ["summary", "behaviors"],
                "required_by_size": {"xs": ["summary"]},
            },
        )
        s = load_work_type_schema(tmp_path, "feature")
        assert s.required_fields_for_size(Size.M) == ["summary", "behaviors"]


class TestValidation:
    def test_unknown_work_type_rejected(self, tmp_path: Path) -> None:
        _write_schema(
            tmp_path,
            "widget",
            {"work_type": "widget", "required": ["summary"]},
        )
        with pytest.raises(ValidationError):
            load_work_type_schema(tmp_path, "widget")

    def test_empty_yaml_normalizes(self, tmp_path: Path) -> None:
        (tmp_path / ".jig" / "work_types").mkdir(parents=True)
        (tmp_path / ".jig" / "work_types" / "feature.yaml").write_text("")
        # Empty YAML → missing required work_type field.
        with pytest.raises(ValidationError):
            load_work_type_schema(tmp_path, "feature")

    def test_bad_size_in_required_by_size(self, tmp_path: Path) -> None:
        _write_schema(
            tmp_path,
            "feature",
            {
                "work_type": "feature",
                "required_by_size": {"huge": ["summary"]},
            },
        )
        with pytest.raises(ValidationError):
            load_work_type_schema(tmp_path, "feature")


class TestInitCreatesDir:
    def test_init_creates_work_types_dir(self, tmp_path: Path) -> None:
        from jig.persistence import init_project

        (tmp_path / ".git").mkdir()
        init_project(tmp_path)
        assert (tmp_path / ".jig" / "work_types").is_dir()
