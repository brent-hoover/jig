"""Resolve default_context URIs (e.g. issue://design) into text for agent prompts."""

import logging
from pathlib import Path

from jig.store.comments import CommentStore
from jig.ticket import Ticket

_logger = logging.getLogger(__name__)

# Glob patterns to search for design/plan docs in the worktree.
_DESIGN_GLOBS = ["docs/design/**/*.md", "docs/design*.md", "design*.md", "spec*.md"]
_PLAN_GLOBS = ["docs/plan/**/*.md", "docs/plan*.md", "plan*.md"]


async def resolve_context_uris(
    uris: list[str],
    *,
    ticket: Ticket,
    parent: Ticket | None,
    comments: CommentStore,
    worktree_path: Path,
) -> str:
    """Resolve a list of context URIs and return combined text."""
    if not uris:
        return ""
    sections: list[str] = []
    for uri in uris:
        text = await _resolve_one(
            uri,
            ticket=ticket,
            parent=parent,
            comments=comments,
            worktree_path=worktree_path,
        )
        if text:
            sections.append(text)
    if not sections:
        return ""
    return "## Context\n\n" + "\n\n---\n\n".join(sections) + "\n\n"


async def _resolve_one(
    uri: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    comments: CommentStore,
    worktree_path: Path,
) -> str:
    if not uri.startswith("issue://"):
        _logger.warning("unknown context URI scheme: %s", uri)
        return ""

    key = uri.removeprefix("issue://")

    if key == "description":
        return _resolve_description(ticket, parent)
    if key == "design":
        return await _resolve_design(ticket, parent, comments, worktree_path)
    if key == "plan":
        return await _resolve_plan(ticket, parent, comments, worktree_path)

    _logger.warning("unknown context URI key: %s", key)
    return ""


def _resolve_description(ticket: Ticket, parent: Ticket | None) -> str:
    """Return the ticket (or parent) description."""
    desc = ""
    if parent and parent.description:
        desc = f"### Parent Ticket: {parent.title}\n\n{parent.description}"
    if ticket.description:
        if desc:
            desc += f"\n\n### Task: {ticket.title}\n\n{ticket.description}"
        else:
            desc = f"### {ticket.title}\n\n{ticket.description}"
    return desc


async def _resolve_design(
    ticket: Ticket,
    parent: Ticket | None,
    comments: CommentStore,
    worktree_path: Path,
) -> str:
    """Gather design artifacts: decision comments + design doc files."""
    parts: list[str] = []

    all_comments = await comments.for_ticket(ticket.id)
    if parent:
        all_comments.extend(await comments.for_ticket(parent.id))
    decisions = [c for c in all_comments if c.kind == "decision"]
    for d in decisions:
        parts.append(f"**Decision** ({d.author}):\n{d.content}")

    # Design doc files in worktree
    for pattern in _DESIGN_GLOBS:
        for path in sorted(worktree_path.glob(pattern)):
            if path.is_file():
                try:
                    content = path.read_text()
                    rel = path.relative_to(worktree_path)
                    parts.append(f"### {rel}\n\n{content.rstrip()}")
                except Exception:
                    _logger.warning("failed to read %s", path, exc_info=True)

    if not parts:
        return ""
    return "### Design Context\n\n" + "\n\n".join(parts)


async def _resolve_plan(
    ticket: Ticket,
    parent: Ticket | None,
    comments: CommentStore,
    worktree_path: Path,
) -> str:
    """Gather plan artifacts: plan-related comments + plan doc files."""
    parts: list[str] = []

    all_comments = await comments.for_ticket(ticket.id)
    if parent:
        all_comments.extend(await comments.for_ticket(parent.id))
    for c in all_comments:
        if c.kind == "decision" and "plan" in c.content.lower():
            parts.append(f"**Plan** ({c.author}):\n{c.content}")

    # Plan doc files in worktree
    for pattern in _PLAN_GLOBS:
        for path in sorted(worktree_path.glob(pattern)):
            if path.is_file():
                try:
                    content = path.read_text()
                    rel = path.relative_to(worktree_path)
                    parts.append(f"### {rel}\n\n{content.rstrip()}")
                except Exception:
                    _logger.warning("failed to read %s", path, exc_info=True)

    if not parts:
        return ""
    return "### Implementation Plan\n\n" + "\n\n".join(parts)
