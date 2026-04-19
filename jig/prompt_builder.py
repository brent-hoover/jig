from __future__ import annotations

from jig.models import RoleConfig
from jig.project import Project
from jig.runtime import SpawnReason
from jig.skill_loader import Skill
from jig.ticket import Comment, Ticket

__all__ = ["SpawnReason", "build_initial_prompt"]


def _role_section(cfg: RoleConfig, reason: SpawnReason) -> str:
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


def _team_roles_section(current_role: str, all_roles: list["RoleConfig"]) -> str:
    """List available roles so agents know exact names for messaging/assignment."""
    if not all_roles:
        return ""
    lines = ["## Team Roles\n"]
    lines.append("These are the exact role names in this project. "
                 "Use these names when assigning tickets or addressing messages — "
                 "do NOT invent role names.\n")
    for cfg in all_roles:
        marker = " ← you" if cfg.role == current_role else ""
        # Extract first sentence of phase_prompt as a brief description
        brief = cfg.phase_prompt.split(".")[0].strip() if cfg.phase_prompt else cfg.role
        lines.append(f"- **{cfg.role}**: {brief}{marker}")
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
        f'`update_ticket(ticket_id="{ticket.id}", status="resolved")`.\n\n'
        "### Asking questions\n\n"
        "If you need clarification from the operator, call "
        f'`ask_question(ticket_id="{ticket.id}", '
        'question="<your question>")`. '
        "For multiple questions at once, pass "
        '`questions=["q1", "q2", ...]` instead. '
        "This posts the question(s) and pauses the ticket automatically. "
        "The orchestrator will resume you once the operator answers. "
        "Do NOT create question tickets or manually set needs_info.\n"
    )


def _worktree_section(worktree_path: str | None) -> str:
    if not worktree_path:
        return ""
    return (
        "## Working Directory\n\n"
        "**IMPORTANT**: You are running inside a git worktree. Your current "
        f"working directory is `{worktree_path}`. All file operations (Read, "
        "Edit, Write, Glob, Grep, Bash) default to this directory.\n\n"
        "- Use **relative paths** or your CWD for all file operations.\n"
        "- Do NOT `cd` to the main project repo or use absolute paths "
        "outside your worktree.\n"
        "- The worktree contains a full copy of the project source — "
        "work with the files here, not in the original repo.\n"
        "- Use `commit_progress` (not raw git commands) to commit your work.\n\n"
    )


def build_initial_prompt(
    *,
    role_cfg: RoleConfig,
    spawn_reason: SpawnReason,
    ticket: Ticket,
    parent: Ticket | None,
    comments: list[Comment],
    memories: list[str],
    project: Project,
    skills: list[Skill],
    environment_md: str,
    resolved_context: str = "",
    all_roles: list[RoleConfig] | None = None,
    worktree_path: str | None = None,
) -> str:
    parts = [
        _role_section(role_cfg, spawn_reason),
        _worktree_section(worktree_path),
        _project_section(project),
        _team_roles_section(role_cfg.role, all_roles or []),
        _skills_section(skills),
        _environment_section(environment_md),
        _memories_section(memories),
        resolved_context,
        _ticket_section(ticket, parent, comments),
        _instructions_section(ticket, spawn_reason),
    ]
    return "".join(parts)
