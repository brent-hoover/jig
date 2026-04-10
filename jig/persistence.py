"""File I/O for .jig/ directory structure."""

from pathlib import Path

import yaml

from jig.models import (
    AgentTypeConfig,
    Issue,
    Message,
    PhaseConfig,
    PhaseHistoryEntry,
    ProjectConfig,
    ProjectContext,
    Task,
    WorkflowConfig,
)


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


def save_project_context(project_path: Path, context: ProjectContext) -> None:
    """Save project context to .jig/project_context.yaml."""
    context_path = _jig_dir(project_path) / "project_context.yaml"
    context_path.write_text(
        yaml.dump(context.model_dump(mode="json"), default_flow_style=False)
    )


def load_project_context(project_path: Path) -> ProjectContext:
    """Load project context from .jig/project_context.yaml."""
    context_path = _jig_dir(project_path) / "project_context.yaml"
    if not context_path.exists():
        return ProjectContext()
    data = yaml.safe_load(context_path.read_text())
    return ProjectContext.model_validate(data or {})


def save_issue(project_path: Path, issue: Issue) -> None:
    """Save an issue to .jig/issues/<id>/issue.yaml."""
    issue_dir = _jig_dir(project_path) / "issues" / issue.id
    issue_dir.mkdir(parents=True, exist_ok=True)
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
    """Save an agent type config to .jig/agent_types/<role>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{config.role}.yaml"
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


def _defaults_dir() -> Path:
    """Return the path to the built-in defaults directory."""
    return Path(__file__).resolve().parent / "defaults"


def save_default_agent_types(project_path: Path) -> None:
    """Copy default agent type configs from jig/defaults/agent_types/ into project."""
    source_dir = _defaults_dir() / "agent_types"
    for yaml_file in sorted(source_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        config = AgentTypeConfig.model_validate(data)
        save_agent_type(project_path, config)


def load_skill(name: str) -> str:
    """Load a skill markdown file by name from the built-in skills library."""
    skill_path = _defaults_dir() / "skills" / f"{name}.md"
    if not skill_path.is_file():
        return ""
    return skill_path.read_text()


def save_workflow(project_path: Path, workflow: WorkflowConfig) -> None:
    """Save a workflow config to .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{workflow.name}.yaml"
    wf_path.write_text(
        yaml.dump(workflow.model_dump(mode="json"), default_flow_style=False)
    )


def load_workflow(project_path: Path, name: str) -> WorkflowConfig:
    """Load a workflow config from .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{name}.yaml"
    if not wf_path.is_file():
        raise FileNotFoundError(f"Workflow '{name}' not found")
    data = yaml.safe_load(wf_path.read_text())
    return WorkflowConfig.model_validate(data)


def save_default_workflow(project_path: Path) -> None:
    """Copy default workflow config from jig/defaults/workflows/ into project."""
    source_dir = _defaults_dir() / "workflows"
    for yaml_file in sorted(source_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        workflow = WorkflowConfig.model_validate(data)
        save_workflow(project_path, workflow)


def save_task(project_path: Path, issue_id: str, task: Task) -> None:
    """Save a task to .jig/issues/<issue_id>/tasks/<task_id>.yaml."""
    tasks_dir = _jig_dir(project_path) / "issues" / issue_id / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    task_path = tasks_dir / f"{task.id}.yaml"
    task_path.write_text(
        yaml.dump(task.model_dump(mode="json"), default_flow_style=False)
    )


def load_task(project_path: Path, issue_id: str, task_id: str) -> Task:
    """Load a task from .jig/issues/<issue_id>/tasks/<task_id>.yaml."""
    task_path = _jig_dir(project_path) / "issues" / issue_id / "tasks" / f"{task_id}.yaml"
    if not task_path.is_file():
        raise FileNotFoundError(f"Task {task_id} not found in issue {issue_id}")
    data = yaml.safe_load(task_path.read_text())
    return Task.model_validate(data)


def _phase_history_path(project_path: Path, issue_id: str) -> Path:
    return _jig_dir(project_path) / "issues" / issue_id / "phase_history.jsonl"


def append_phase_history(project_path: Path, issue_id: str, entry: PhaseHistoryEntry) -> None:
    """Append a phase history entry to .jig/issues/<id>/phase_history.jsonl.

    The file is append-only so each entry is durable on its own line, and the
    history can be replayed in order across orchestrator restarts.
    """
    path = _phase_history_path(project_path, issue_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(entry.model_dump_json() + "\n")


def load_phase_history(project_path: Path, issue_id: str) -> list[PhaseHistoryEntry]:
    """Load all phase history entries for an issue, in append order."""
    path = _phase_history_path(project_path, issue_id)
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if line.strip():
            entries.append(PhaseHistoryEntry.model_validate_json(line))
    return entries
