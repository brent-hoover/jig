"""File I/O for .jig/ directory structure."""

from pathlib import Path

import yaml

from jig.models import ProjectConfig, Issue, Message, AgentTypeConfig, Task, WorkflowConfig, PhaseConfig, WorkflowPhase


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


def save_default_agent_types(project_path: Path) -> None:
    """Create the default v1 agent type configs."""
    defaults = [
        AgentTypeConfig(
            name="spec",
            system_prompt=(
                "You are a specification agent. Your job is to draft a design document "
                "from the issue description. Analyze requirements, identify components, "
                "and produce a clear, actionable design doc in markdown format. "
                "Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Glob", "Grep"],
            default_context=["issue://description"],
        ),
        AgentTypeConfig(
            name="test",
            system_prompt=(
                "You are a test agent. Your job is to write tests based on the design doc "
                "and implementation plan. Write comprehensive tests that cover the specified "
                "behavior and edge cases. Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Write", "Glob", "Grep", "Bash"],
            default_context=["issue://design", "issue://plan"],
        ),
        AgentTypeConfig(
            name="dev",
            system_prompt=(
                "You are a development agent. Your job is to implement code changes based "
                "on the design doc and implementation plan. Write clean, well-structured code "
                "that passes the existing tests. Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            default_context=["issue://design", "issue://plan"],
        ),
        AgentTypeConfig(
            name="review",
            system_prompt=(
                "You are a review agent. Your job is to review the implementation for "
                "correctness, quality, and adherence to the design doc. Run tests and linters. "
                "Report issues found. Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            default_context=["issue://design", "issue://plan"],
        ),
    ]
    for config in defaults:
        save_agent_type(project_path, config)


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
    """Create the default v1 linear workflow."""
    workflow = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
            PhaseConfig(name=WorkflowPhase.TEST, agent_type="test"),
            PhaseConfig(name=WorkflowPhase.IMPLEMENT, agent_type="dev"),
            PhaseConfig(name=WorkflowPhase.REVIEW, agent_type="review"),
        ],
    )
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
