"""File I/O for .jig/ directory structure."""

from pathlib import Path

import yaml

from jig.models import ProjectConfig, Issue, Message, AgentTypeConfig


def _jig_dir(project_path: Path) -> Path:
    return project_path / ".jig"


def init_project(project_path: Path, default_branch: str = "main") -> None:
    """Initialize .jig/ directory in a project."""
    if not (project_path / ".git").is_dir():
        raise ValueError(f"{project_path} is not a git repository")

    jig_dir = _jig_dir(project_path)
    if jig_dir.exists():
        raise FileExistsError(f"{jig_dir} already exists")

    jig_dir.mkdir()
    for subdir in ("issues", "agent_types", "workflows", "worktrees"):
        (jig_dir / subdir).mkdir()

    config = ProjectConfig(
        repo_path=str(project_path),
        default_branch=default_branch,
    )
    (jig_dir / "config.yaml").write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def load_project(project_path: Path) -> ProjectConfig:
    """Load project config from .jig/config.yaml."""
    config_path = _jig_dir(project_path) / "config.yaml"
    data = yaml.safe_load(config_path.read_text())
    return ProjectConfig.model_validate(data)


def save_issue(project_path: Path, issue: Issue) -> None:
    """Save an issue to .jig/issues/<id>/issue.yaml."""
    issue_dir = _jig_dir(project_path) / "issues" / issue.id
    issue_dir.mkdir(exist_ok=True)
    (issue_dir / "tasks").mkdir(exist_ok=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.dump(issue.model_dump(mode="json"), default_flow_style=False)
    )


def load_issue(project_path: Path, issue_id: str) -> Issue:
    """Load an issue from .jig/issues/<id>/issue.yaml."""
    issue_path = _jig_dir(project_path) / "issues" / issue_id / "issue.yaml"
    if not issue_path.is_file():
        raise FileNotFoundError(f"Issue {issue_id} not found")
    data = yaml.safe_load(issue_path.read_text())
    return Issue.model_validate(data)


def list_issues(project_path: Path) -> list[Issue]:
    """List all issues in .jig/issues/."""
    issues_dir = _jig_dir(project_path) / "issues"
    issues = []
    for issue_dir in sorted(issues_dir.iterdir()):
        issue_file = issue_dir / "issue.yaml"
        if issue_file.is_file():
            data = yaml.safe_load(issue_file.read_text())
            issues.append(Issue.model_validate(data))
    return issues


def append_message(project_path: Path, issue_id: str, message: Message) -> None:
    """Append a message to .jig/issues/<id>/messages.jsonl."""
    messages_path = _jig_dir(project_path) / "issues" / issue_id / "messages.jsonl"
    with messages_path.open("a") as f:
        f.write(message.model_dump_json() + "\n")


def load_messages(project_path: Path, issue_id: str) -> list[Message]:
    """Load all messages from .jig/issues/<id>/messages.jsonl."""
    messages_path = _jig_dir(project_path) / "issues" / issue_id / "messages.jsonl"
    if not messages_path.is_file():
        return []
    messages = []
    for line in messages_path.read_text().splitlines():
        if line.strip():
            messages.append(Message.model_validate_json(line))
    return messages


def save_agent_type(project_path: Path, config: AgentTypeConfig) -> None:
    """Save an agent type config to .jig/agent_types/<name>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{config.name}.yaml"
    type_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def load_agent_type(project_path: Path, name: str) -> AgentTypeConfig:
    """Load an agent type config from .jig/agent_types/<name>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{name}.yaml"
    if not type_path.is_file():
        raise FileNotFoundError(f"Agent type '{name}' not found")
    data = yaml.safe_load(type_path.read_text())
    return AgentTypeConfig.model_validate(data)


def list_agent_types(project_path: Path) -> list[AgentTypeConfig]:
    """List all agent type configs in .jig/agent_types/."""
    types_dir = _jig_dir(project_path) / "agent_types"
    configs = []
    for yaml_file in sorted(types_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        configs.append(AgentTypeConfig.model_validate(data))
    return configs
