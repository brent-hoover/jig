import pytest
from pathlib import Path


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary directory simulating a git repo."""
    (tmp_path / ".git").mkdir()
    return tmp_path
