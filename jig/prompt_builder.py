from enum import Enum

from jig.models import AgentTypeConfig
from jig.project import Project
from jig.skill_loader import Skill
from jig.ticket import Comment, Ticket


class SpawnReason(str, Enum):
    PHASE_PRIMARY = "phase_primary"
    QA_RESPONDER = "qa_responder"


def _role_section(cfg: AgentTypeConfig, reason: SpawnReason) -> str:
    if reason == SpawnReason.QA_RESPONDER:
        if cfg.response_prompt:
            return cfg.response_prompt + "\n\n"
        return (
            "You are answering a question from another role. Below is your "
            f"normal role prompt for context.\n\n{cfg.phase_prompt}\n\n"
        )
    return cfg.phase_prompt + "\n\n"


def _project_section(p: Project) -> str:
    lines = ["## Project Context\n"]
    if p.name:
        lines.append(f"- **Project**: {p.name}")
    if p.description:
        lines.append(f"- **Description**: {p.description}")
    if p.language:
        lines.append(f"- **Language**: {p.language}")
    if p.framework:
        lines.append(f"- **Framework**: {p.framework}")
    if p.package_manager:
        lines.append(f"- **Package manager**: {p.package_manager}")
    if p.test_command:
        lines.append(f"- **Test**: `{p.test_command}`")
    if p.build_command:
        lines.append(f"- **Build**: `{p.build_command}`")
    lines.append(f"- **Default branch**: {p.default_branch}")
    return "\n".join(lines) + "\n\n"


def _skills_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    parts = ["## Skills\n"]
    for s in skills:
        parts.append(s.content.rstrip() + "\n")
    return "\n".join(parts) + "\n"


def _environment_section(env_md: str) -> str:
    if not env_md.strip():
        return ""
    return f"## Environment\n\n{env_md.rstrip()}\n\n"


def _memories_section(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = ["## Your Memories\n"]
    lines.extend(f"- {m}" for m in memories)
    return "\n".join(lines) + "\n\n"


def _ticket_section(
    ticket: Ticket, parent: Ticket | None, comments: list[Comment]
) -> str:
    parts = [f"## Ticket: {ticket.title}\n"]
    if ticket.description:
        parts.append(ticket.description)
    if parent is not None:
        parts.append(f"\n### Parent: {parent.title}\n")
        if parent.description:
            parts.append(parent.description)
    if comments:
        parts.append("\n### Relevant comments\n")
        for c in comments:
            parts.append(f"- [{c.author}] {c.content}")
    return "\n".join(parts) + "\n\n"


def _instructions_section(ticket: Ticket, reason: SpawnReason) -> str:
    if reason == SpawnReason.QA_RESPONDER:
        return (
            "## Instructions\n\n"
            f"Respond to the most recent message on ticket {ticket.id}. "
            "When you've answered, call "
            f'`update_ticket(ticket_id="{ticket.id}", status="resolved")`.\n'
        )
    return (
        "## Instructions\n\n"
        f"Work on ticket {ticket.id}. Call `commit_progress` after each "
        "meaningful chunk of work. When the task is complete, call "
        f'`update_ticket(ticket_id="{ticket.id}", status="resolved")`. '
        'If blocked or in need of clarification, set status="blocked" or '
        'status="needs_info" and explain via `comment_on_ticket`.\n'
    )


def build_initial_prompt(
    *,
    role_cfg: AgentTypeConfig,
    spawn_reason: SpawnReason,
    ticket: Ticket,
    parent: Ticket | None,
    comments: list[Comment],
    memories: list[str],
    project: Project,
    skills: list[Skill],
    environment_md: str,
) -> str:
    parts = [
        _role_section(role_cfg, spawn_reason),
        _project_section(project),
        _skills_section(skills),
        _environment_section(environment_md),
        _memories_section(memories),
        _ticket_section(ticket, parent, comments),
        _instructions_section(ticket, spawn_reason),
    ]
    return "".join(parts)
