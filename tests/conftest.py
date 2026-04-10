import pytest
from pathlib import Path


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary directory simulating a git repo."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def tmp_jig_project(tmp_project: Path) -> Path:
    """Create a temporary project with .jig/ already initialized."""
    jig_dir = tmp_project / ".jig"
    jig_dir.mkdir()
    (jig_dir / "config.yaml").write_text("repo_path: .\ndefault_branch: main\n")
    (jig_dir / "issues").mkdir()
    (jig_dir / "agent_types").mkdir()
    (jig_dir / "workflows").mkdir()
    (jig_dir / "worktrees").mkdir()
    (jig_dir / "agents").mkdir()
    return tmp_project
