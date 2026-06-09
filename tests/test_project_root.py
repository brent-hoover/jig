"""Git-style project-root discovery for the issue front doors.

``find_project_root`` walks up from a starting directory to the nearest
ancestor containing a ``.jig/`` directory, so the CLI / standalone MCP work
from any subdirectory of a project. It fails loudly outside any project.
"""

from pathlib import Path

import pytest

from jig.issues.discovery import find_project_root


def test_finds_root_from_project_root(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    assert find_project_root(tmp_path) == tmp_path


def test_finds_root_from_nested_subdirectory(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_raises_outside_any_project(tmp_path: Path) -> None:
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="(?i)jig project"):
        find_project_root(nested)
