import json
from pathlib import Path

import pytest
import yaml

from jig.project import Project, _json_save_project, load_project, save_project


def test_save_and_load_project(tmp_path: Path) -> None:
    project = Project(
        id="jig-itself",
        name="jig",
        path=str(tmp_path),
        default_branch="develop",
        description="multi-agent dev system",
        language="python",
        framework="",
        package_manager="uv",
        test_command="uv run pytest",
        build_command="",
    )
    save_project(tmp_path, project)

    config_path = tmp_path / ".jig" / "config.yaml"
    assert config_path.is_file()
    assert not (tmp_path / ".jig" / "project.json").exists()
    loaded = load_project(tmp_path)
    assert loaded == project


def test_max_parallel_round_trips(tmp_path: Path) -> None:
    project = Project(
        id="p",
        name="p",
        path=str(tmp_path),
        max_parallel=2,
    )
    save_project(tmp_path, project)
    loaded = load_project(tmp_path)
    assert loaded.max_parallel == 2


def test_max_parallel_defaults_to_none(tmp_path: Path) -> None:
    project = Project(id="p", name="p", path=str(tmp_path))
    save_project(tmp_path, project)
    loaded = load_project(tmp_path)
    assert loaded.max_parallel is None


def test_existing_config_without_max_parallel_loads_as_none(tmp_path: Path) -> None:
    """Projects saved before max_parallel was added deserialize with None."""
    import yaml

    project = Project(id="p", name="p", path=str(tmp_path))
    save_project(tmp_path, project)
    config_path = tmp_path / ".jig" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())
    raw.get("project", {}).pop("max_parallel", None)
    config_path.write_text(yaml.safe_dump(raw))
    loaded = load_project(tmp_path)
    assert loaded.max_parallel is None


def test_load_project_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_project(tmp_path)


def test_config_yaml_has_nested_shape(tmp_path: Path) -> None:
    project = Project(id="p1", name="p1", path=str(tmp_path))
    save_project(tmp_path, project)
    raw = (tmp_path / ".jig" / "config.yaml").read_text()
    data = yaml.safe_load(raw)
    # Top-level sections per doc 17
    assert set(data) >= {"project", "workflows", "ownership", "roles", "escalation"}
    assert data["project"]["id"] == "p1"


def test_load_project_falls_back_to_legacy_json(tmp_path: Path) -> None:
    project = Project(
        id="legacy",
        name="legacy",
        path=str(tmp_path),
        language="python",
    )
    _json_save_project(tmp_path, project)
    assert (tmp_path / ".jig" / "project.json").is_file()
    assert not (tmp_path / ".jig" / "config.yaml").exists()

    with pytest.warns(DeprecationWarning, match="project.json"):
        loaded = load_project(tmp_path)
    assert loaded == project


def test_save_project_preserves_other_sections(tmp_path: Path) -> None:
    """Hand-edited workflows/ownership sections survive a save_project call."""
    from jig.config import Config, WorkflowsSection, WorkflowTypeEntry, save_config

    initial = Config(
        project=Project(id="p", name="p", path=str(tmp_path)),
        workflows=WorkflowsSection(
            default_by_size={"xs": "hotfix", "s": "small-change"},
            available=["hotfix", "small-change"],
            by_type={
                "feature": WorkflowTypeEntry(
                    default_by_size={"m": "standard"},
                    available=["standard"],
                )
            },
        ),
    )
    save_config(tmp_path, initial)

    updated_project = Project(
        id="p", name="renamed", path=str(tmp_path), description="new"
    )
    save_project(tmp_path, updated_project)

    raw = yaml.safe_load((tmp_path / ".jig" / "config.yaml").read_text())
    assert raw["project"]["name"] == "renamed"
    assert raw["workflows"]["default_by_size"] == {"xs": "hotfix", "s": "small-change"}
    assert raw["workflows"]["by_type"]["feature"]["available"] == ["standard"]


def test_legacy_json_writer_still_pretty(tmp_path: Path) -> None:
    """The legacy writer used by migration tests stays pretty-printed."""
    project = Project(id="p1", name="p1", path=str(tmp_path))
    _json_save_project(tmp_path, project)
    raw = (tmp_path / ".jig" / "project.json").read_text()
    data = json.loads(raw)
    assert data["id"] == "p1"
    assert "\n" in raw
