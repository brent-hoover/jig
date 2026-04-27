"""Resolve context URIs into text blocks for agent prompts.

Schemes per doc 07:
- ``project://<path>``    — curated project-level artifact under
                              ``.jig/context/project/<path>``.
- ``role://<role>/<path>`` — role-level artifact under
                              ``.jig/context/roles/<role>/<path>``.
- ``ticket://<artifact>`` — per-ticket state: ``description``, ``design``,
                              ``plan``, ``thread``.
- ``decision://<id>``     — a decision record at
                              ``.jig/decisions/<id>.md``.
- ``repo://<path>``       — raw file in the worktree; escape hatch.
- ``issue://<artifact>``  — deprecated alias for ``ticket://``; kept for
                              one release while shipped defaults migrate.

Unknown schemes and unresolved references log a warning and return the
empty string; callers decide whether that's fatal (see
``required_context`` in Phase 2 Task B).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from jig.spec_schema import StructuredSpec
from jig.spec_uri import SpecUriError, resolve_spec_uri
from jig.store.threads import ThreadStore
from jig.thread import entry_content
from jig.ticket import Ticket

_logger = logging.getLogger(__name__)


class MissingContextError(RuntimeError):
    """Raised when a required context URI fails to resolve at spawn."""

    def __init__(self, uri: str, reason: str = "not found") -> None:
        super().__init__(f"required context URI {uri!r} did not resolve: {reason}")
        self.uri = uri
        self.reason = reason


# Glob patterns to search for design/plan docs in the worktree.
_DESIGN_GLOBS = ["docs/design/**/*.md", "docs/design*.md", "design*.md", "spec*.md"]
_PLAN_GLOBS = ["docs/plan/**/*.md", "docs/plan*.md", "plan*.md"]

# Track whether we've already warned about the issue:// alias in this
# process so we don't spam the log for every URI in every role template.
_issue_alias_warned = False


async def resolve_context_uris(
    uris: list[str],
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
    strict: bool = False,
) -> str:
    """Resolve a list of context URIs and return a combined text block.

    Returns ``""`` when ``uris`` is empty or every URI resolves to no
    content.

    If ``strict`` is True, any URI that fails to resolve raises
    :class:`MissingContextError` — use this for ``required_context``.
    With ``strict=False`` (default) unresolved URIs are logged and
    skipped, matching the optional-context semantics in doc 07.
    """
    if not uris:
        return ""
    sections: list[str] = []
    for uri in uris:
        text = await _resolve_one(
            uri,
            ticket=ticket,
            parent=parent,
            threads=threads,
            worktree_path=worktree_path,
            project_path=project_path,
        )
        if text is None:
            if strict:
                raise MissingContextError(uri)
            continue
        if text:
            sections.append(text)
    if not sections:
        return ""
    return "## Context\n\n" + "\n\n---\n\n".join(sections) + "\n\n"


async def resolve_context_uri(
    uri: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str | None:
    """Resolve a single URI. Thin public wrapper for validation callers.

    Returns ``None`` if the URI could not be resolved (missing file,
    unknown scheme, etc.) so callers can distinguish "not there" from
    "there but intentionally empty".
    """
    return await _resolve_one(
        uri,
        ticket=ticket,
        parent=parent,
        threads=threads,
        worktree_path=worktree_path,
        project_path=project_path,
    )


async def _resolve_one(
    uri: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str | None:
    """Dispatch a single URI through its scheme handler.

    Returns ``None`` if the URI can't produce useful content (bad scheme,
    missing file, empty thread, …); the caller decides whether that's a
    warning or a hard error via ``strict`` in ``resolve_context_uris``.
    """
    if "://" not in uri:
        _logger.warning("context URI missing scheme: %s", uri)
        return None
    scheme, _, body = uri.partition("://")

    if scheme == "issue":
        _warn_issue_alias()
        scheme = "ticket"

    handler = _HANDLERS.get(scheme)
    if handler is None:
        _logger.warning("unknown context URI scheme: %s", uri)
        return None

    text = await handler(
        body,
        ticket=ticket,
        parent=parent,
        threads=threads,
        worktree_path=worktree_path,
        project_path=project_path,
    )
    if not text:
        return None
    return text


def _warn_issue_alias() -> None:
    global _issue_alias_warned
    if not _issue_alias_warned:
        _logger.warning(
            "issue:// URI scheme is deprecated; use ticket:// "
            "(see docs/07-context-bundles.md). This warning is logged once "
            "per process."
        )
        _issue_alias_warned = True


# ----- ticket:// -----------------------------------------------------------


async def _resolve_ticket(
    body: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str:
    if body == "description":
        return _ticket_description(ticket, parent)
    if body == "design":
        return await _ticket_design(ticket, parent, threads, worktree_path)
    if body == "plan":
        return await _ticket_plan(ticket, parent, threads, worktree_path)
    if body == "thread":
        return await _ticket_thread(ticket, parent, threads)
    # Phase 3D: ticket://spec  or  ticket://spec.<field>
    if body == "spec" or body.startswith("spec."):
        section = body[len("spec.") :] if body.startswith("spec.") else None
        return _ticket_spec(ticket, project_path, section=section)
    _logger.warning("unknown ticket:// artifact: %s", body)
    return ""


def _ticket_description(ticket: Ticket, parent: Ticket | None) -> str:
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


async def _ticket_design(
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
) -> str:
    """Gather design artifacts: decision entries + design doc files."""
    parts: list[str] = []

    entries = await threads.for_ticket(ticket.id)
    if parent:
        entries = entries + await threads.for_ticket(parent.id)
    for d in entries:
        if d.kind != "decision":
            continue
        body = d.decision
        if d.rationale:
            body = f"{body}\n\n{d.rationale}"
        parts.append(f"**Decision** ({d.author}):\n{body}")

    for pattern in _DESIGN_GLOBS:
        for path in sorted(worktree_path.glob(pattern)):
            if path.is_file():
                try:
                    content = path.read_text()
                    rel = path.relative_to(worktree_path)
                    parts.append(f"### {rel}\n\n{content.rstrip()}")
                except (OSError, UnicodeDecodeError):
                    _logger.warning("failed to read %s", path, exc_info=True)

    if not parts:
        return ""
    return "### Design Context\n\n" + "\n\n".join(parts)


async def _ticket_plan(
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
) -> str:
    """Gather plan artifacts: plan-related decisions + plan doc files."""
    parts: list[str] = []

    entries = await threads.for_ticket(ticket.id)
    if parent:
        entries = entries + await threads.for_ticket(parent.id)
    for e in entries:
        if e.kind != "decision":
            continue
        if "plan" in e.decision.lower():
            body = e.decision
            if e.rationale:
                body = f"{body}\n\n{e.rationale}"
            parts.append(f"**Plan** ({e.author}):\n{body}")

    for pattern in _PLAN_GLOBS:
        for path in sorted(worktree_path.glob(pattern)):
            if path.is_file():
                try:
                    content = path.read_text()
                    rel = path.relative_to(worktree_path)
                    parts.append(f"### {rel}\n\n{content.rstrip()}")
                except (OSError, UnicodeDecodeError):
                    _logger.warning("failed to read %s", path, exc_info=True)

    if not parts:
        return ""
    return "### Implementation Plan\n\n" + "\n\n".join(parts)


def _ticket_spec(
    ticket: Ticket,
    project_path: Path,
    *,
    section: str | None,
) -> str:
    """Render the ticket's structured spec (or a single field).

    * ``ticket://spec`` → the full spec (each field as its own block).
    * ``ticket://spec.<field>`` → just that field, or empty-with-warning
      if the spec has no such key.

    Missing spec file returns ``""`` — callers using ``strict=True``
    (``required_context``) will raise ``MissingContextError`` upstream.
    Per doc 03 the section is rendered as a YAML block so agents see the
    structured content directly rather than a prose paraphrase.
    """
    from jig.specs import load_ticket_spec

    spec = load_ticket_spec(project_path, ticket.id)
    if spec is None:
        _logger.warning(
            "ticket://spec%s: no spec found for ticket %s",
            f".{section}" if section else "",
            ticket.id,
        )
        return ""

    if section is None:
        # Full spec: one block per populated field.
        if not spec.fields:
            return ""
        blocks: list[str] = [f"## Ticket Spec ({spec.work_type.value})"]
        for field_name, value in spec.fields.items():
            blocks.append(_render_spec_field(field_name, value))
        return "\n\n".join(blocks)

    if section not in spec.fields:
        _logger.warning(
            "ticket://spec.%s: field not present on ticket %s",
            section,
            ticket.id,
        )
        return ""
    return _render_spec_field(section, spec.fields[section])


def _render_spec_field(name: str, value: object) -> str:
    """Render a single spec field as a markdown-wrapped YAML block.

    Scalars (including multi-line strings) render naked under the
    header; structured content goes in a fenced YAML block so the
    agent reads the shape directly.
    """
    import yaml as _yaml

    header = f"### {name.replace('_', ' ').title()}"
    if isinstance(value, str):
        return f"{header}\n\n{value.rstrip()}"
    body = _yaml.safe_dump(value, default_flow_style=False, sort_keys=False).rstrip()
    return f"{header}\n\n```yaml\n{body}\n```"


async def _ticket_thread(
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
) -> str:
    """Concatenate typed thread entries for the ticket (and parent) in order.

    Chronological markdown dump so reviewer and resumption agents see
    what has been said. Each entry's body comes from its kind-specific
    field via ``entry_content`` (``text`` on Note, ``question`` on
    Question, etc.).
    """
    entries = await threads.for_ticket(ticket.id)
    if parent:
        entries = await threads.for_ticket(parent.id) + entries
    if not entries:
        return ""
    lines = ["### Thread"]
    for e in entries:
        ts = e.created_at.isoformat() if e.created_at else ""
        lines.append(f"**[{e.kind}] {e.author} — {ts}**")
        lines.append(entry_content(e).rstrip())
        lines.append("")
    return "\n".join(lines).rstrip()


# ----- project:// / role:// ------------------------------------------------


async def _resolve_project(
    body: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str:
    # NEW: spec/... routes through the structured-spec resolver.
    if body == "spec" or body.startswith("spec/") or body.startswith("spec#"):
        spec_file = project_path / ".jig" / "spec" / "project.structured.yaml"
        if not spec_file.is_file():
            return f"# project://{body}\n\n(no project.structured.yaml exists yet)\n"
        data = yaml.safe_load(spec_file.read_text()) or {}
        spec = StructuredSpec.model_validate(data)
        try:
            out = resolve_spec_uri(f"project://{body}", spec)
        except SpecUriError as e:
            return f"# project://{body}\n\n[unresolved: {e}]\n"
        # Render the structured payload as YAML text for context-bundle injection.
        return yaml.safe_dump(out["data"], sort_keys=False)

    # EXISTING: file-based project context (unchanged below)
    base = project_path / ".jig" / "context" / "project"
    return _read_context_file(base, body, f"project://{body}")


async def _resolve_role(
    body: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str:
    # role://<role>/<path...> — strip the role segment, read the rest
    # relative to that role's context directory.
    role_name, _, rel = body.partition("/")
    if not role_name or not rel:
        _logger.warning(
            "role:// URI must include both role and path (role://<role>/<path>); got role://%s",
            body,
        )
        return ""
    base = project_path / ".jig" / "context" / "roles" / role_name
    return _read_context_file(base, rel, f"role://{body}")


# ----- decision:// ---------------------------------------------------------


async def _resolve_decision(
    body: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str:
    if not body:
        _logger.warning("decision:// URI missing id")
        return ""
    # `decision://DR-0001` → `.jig/decisions/DR-0001.md`. Accept explicit
    # extension if the caller already supplied one.
    rel = body if Path(body).suffix else f"{body}.md"
    path = project_path / ".jig" / "decisions" / rel
    if not path.is_file():
        _logger.warning("decision:// not found: %s (looked in %s)", body, path)
        return ""
    try:
        content = path.read_text().rstrip()
    except (OSError, UnicodeDecodeError):
        _logger.warning("failed to read decision %s", path, exc_info=True)
        return ""
    return f"### Decision {body}\n\n{content}"


# ----- repo:// -------------------------------------------------------------


async def _resolve_repo(
    body: str,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str:
    if not body:
        _logger.warning("repo:// URI missing path")
        return ""
    path = worktree_path / body
    if not path.is_file():
        _logger.warning("repo:// not found: %s (looked in %s)", body, path)
        return ""
    try:
        content = path.read_text().rstrip()
    except (OSError, UnicodeDecodeError):
        _logger.warning("failed to read repo file %s", path, exc_info=True)
        return ""
    return f"### {body}\n\n{content}"


# ----- shared helpers ------------------------------------------------------


def _read_context_file(base: Path, rel: str, uri_for_log: str) -> str:
    """Read ``base/rel``; if no extension, try ``.md`` first."""
    if not rel:
        _logger.warning("%s missing path component", uri_for_log)
        return ""
    candidates: list[Path] = []
    as_given = base / rel
    candidates.append(as_given)
    if not as_given.suffix:
        candidates.append(as_given.with_suffix(".md"))
    for candidate in candidates:
        if candidate.is_file():
            try:
                content = candidate.read_text().rstrip()
            except (OSError, UnicodeDecodeError):
                _logger.warning("failed to read %s", candidate, exc_info=True)
                return ""
            header = candidate.stem.replace("-", " ").replace("_", " ").title()
            return f"### {header}\n\n{content}"
    _logger.warning(
        "%s not found (looked in %s)",
        uri_for_log,
        ", ".join(str(c) for c in candidates),
    )
    return ""


# ----- dispatcher ----------------------------------------------------------


_Handler = Callable[..., Awaitable[str]]

_HANDLERS: dict[str, _Handler] = {
    "ticket": _resolve_ticket,
    "project": _resolve_project,
    "role": _resolve_role,
    "decision": _resolve_decision,
    "repo": _resolve_repo,
}


__all__ = [
    "MissingContextError",
    "resolve_context_uris",
    "resolve_context_uri",
]
