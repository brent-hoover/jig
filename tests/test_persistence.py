import pytest
from pathlib import Path

from jig.models import ProjectConfig
from jig.persistence import init_project, load_project


class TestInitProject:
    def test_creates_jig_directory(self, tmp_project: Path):
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert jig_dir.is_dir()
        assert (jig_dir / "config.yaml").is_file()
        assert (jig_dir / "issues").is_dir()
        assert (jig_dir / "agent_types").is_dir()
        assert (jig_dir / "workflows").is_dir()
        assert (jig_dir / "worktrees").is_dir()

    def test_writes_config(self, tmp_project: Path):
        init_project(tmp_project, default_branch="develop")
        config = load_project(tmp_project)
        assert config.repo_path == str(tmp_project)
        assert config.default_branch == "develop"

    def test_default_branch(self, tmp_project: Path):
        init_project(tmp_project)
        config = load_project(tmp_project)
        assert config.default_branch == "main"

    def test_raises_if_already_initialized(self, tmp_jig_project: Path):
        with pytest.raises(FileExistsError):
            init_project(tmp_jig_project)

    def test_raises_if_not_git_repo(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not a git repository"):
            init_project(tmp_path)
