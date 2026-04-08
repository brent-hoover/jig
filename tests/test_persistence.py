import pytest
from pathlib import Path

from jig.models import ProjectConfig, Issue, IssueStatus
from jig.persistence import init_project, load_project, save_issue, load_issue, list_issues


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


class TestIssuePersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        loaded = load_issue(tmp_jig_project, "issue-1")
        assert loaded.id == "issue-1"
        assert loaded.title == "Add auth"
        assert loaded.status == IssueStatus.PENDING

    def test_creates_issue_directory(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        issue_dir = tmp_jig_project / ".jig" / "issues" / "issue-1"
        assert issue_dir.is_dir()
        assert (issue_dir / "issue.yaml").is_file()
        assert (issue_dir / "tasks").is_dir()

    def test_list_empty(self, tmp_jig_project: Path):
        issues = list_issues(tmp_jig_project)
        assert issues == []

    def test_list_multiple(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="a", title="First"))
        save_issue(tmp_jig_project, Issue(id="b", title="Second"))
        issues = list_issues(tmp_jig_project)
        ids = {i.id for i in issues}
        assert ids == {"a", "b"}

    def test_update_existing(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        issue.status = IssueStatus.IN_PROGRESS
        issue.current_phase = "spec"
        save_issue(tmp_jig_project, issue)
        loaded = load_issue(tmp_jig_project, "issue-1")
        assert loaded.status == IssueStatus.IN_PROGRESS
        assert loaded.current_phase == "spec"

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_issue(tmp_jig_project, "nope")
