import json
from pathlib import Path

from jig.project import Project, load_project, save_project


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

    assert (tmp_path / ".jig" / "project.json").is_file()
    loaded = load_project(tmp_path)
    assert loaded == project


def test_load_project_missing_raises(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(FileNotFoundError):
        load_project(tmp_path)


def test_project_json_is_pretty(tmp_path: Path) -> None:
    project = Project(id="p1", name="p1", path=str(tmp_path))
    save_project(tmp_path, project)
    raw = (tmp_path / ".jig" / "project.json").read_text()
    data = json.loads(raw)
    assert data["id"] == "p1"
    assert "\n" in raw  # pretty-printed, not single-line
