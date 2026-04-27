from __future__ import annotations

from typing import Any

from jig.models import PhaseConfig, RoleConfig
from jig.project import Project
from jig.runtime import SpawnReason
from jig.skill_loader import Skill
from jig.thread import ThreadEntry, entry_content
from jig.ticket import Ticket

__all__ = ["SpawnReason", "build_initial_prompt"]


# Maximum characters of check / objection / note body to quote inline in
# the evaluator prompt. The full record stays in the store; the prompt
# just carries enough for the evaluator to make a call without an extra
# read_comments round-trip on the happy path. Tuned by eye — long enough
# for stack traces and objection prose, short enough that a handoff with
# a dozen failures doesn't blow the context budget.
_EVAL_EXCERPT_MAX_CHARS = 400


class _SafeFormatDict(dict):
    """Dict subclass that echoes unknown keys back as ``{key}`` rather than raising.

    Used so a mistyped or forward-looking placeholder in a template
    degrades to visible-in-prompt text instead of crashing the spawn.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _role_section(cfg: RoleConfig, reason: SpawnReason) -> str:
    if reason == SpawnReason.QA_RESPONDER:
        if cfg.response_prompt:
            return cfg.response_prompt + "\n\n"
        return (
            "You are answering a question from another role. Below is your "
            f"normal role prompt for context.\n\n{cfg.phase_prompt}\n\n"
        )
    if reason == SpawnReason.EVALUATOR:
        # Evaluator framing sits above the role's own phase_prompt so a
        # role that doubles as actor + evaluator (e.g. a dev reviewing
        # another dev's handoff) reads the evaluator instructions first
        # and understands which hat it's wearing on this spawn.
        return (
            "You are evaluating a handoff. Review the handoff record, "
            "the check results, and the thread. Either accept or reject. "
            "The orchestrator enforces evaluator-vs-completing-actor "
            "asymmetry — attempting to accept your own handoff will "
            "escalate, not self-approve.\n\n"
            "Your role context, for reference:\n\n"
            f"{cfg.phase_prompt}\n\n"
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
    lines.append(
        "These are the exact role names in this project. "
        "Use these names when assigning tickets or addressing messages — "
        "do NOT invent role names.\n"
    )
    for cfg in all_roles:
        marker = " ← you" if cfg.role == current_role else ""
        # Extract first sentence of phase_prompt as a brief description
        brief = cfg.phase_prompt.split(".")[0].strip() if cfg.phase_prompt else cfg.role
        lines.append(f"- **{cfg.role}**: {brief}{marker}")
    return "\n".join(lines) + "\n\n"


def _phase_section(phase: PhaseConfig | None, ticket: Ticket) -> str:
    """Render the current workflow phase — task template + acceptance criteria.

    `task_template` is interpolated with ticket fields. The current
    canonical placeholders are ``{ticket_title}`` and ``{ticket_id}``;
    legacy ``{issue_title}`` / ``{issue_id}`` are accepted during the
    Phase 1 rename to keep existing workflow yamls working.
    """
    if phase is None:
        return ""
    parts: list[str] = [f"## Phase: {phase.name}\n"]
    if phase.task_template:
        context = _SafeFormatDict(
            ticket_title=ticket.title,
            issue_title=ticket.title,  # legacy alias
            ticket_id=ticket.id,
            issue_id=ticket.id,  # legacy alias
        )
        task = phase.task_template.format_map(context)
        parts.append(f"### Task\n\n{task}\n")
    if phase.acceptance_criteria:
        parts.append(f"### Acceptance criteria\n\n{phase.acceptance_criteria}\n")
    return "\n".join(parts) + "\n"


def _ticket_section(
    ticket: Ticket, parent: Ticket | None, entries: list[ThreadEntry]
) -> str:
    parts = [f"## Ticket: {ticket.title}\n"]
    if ticket.description:
        parts.append(ticket.description)
    if parent is not None:
        parts.append(f"\n### Parent: {parent.title}\n")
        if parent.description:
            parts.append(parent.description)
    if entries:
        parts.append("\n### Relevant thread\n")
        for e in entries:
            body = entry_content(e)
            if body:
                parts.append(f"- [{e.kind}] [{e.author}] {body}")
    return "\n".join(parts) + "\n\n"


# Roles whose handoff is via a role-specific MCP tool (po_finish_brief,
# spec_publish/spec_report_gaps, sa_propose_scaffold) rather than the
# generic commit_progress/update_ticket dance. Their phase_prompt owns
# all the instructions; rendering the generic block on top would tell
# them to call commit_progress and update_ticket — which is exactly the
# thrashing observed in jig init before this fix.
_INIT_ROLES_WITH_OWN_INSTRUCTIONS: frozenset[str] = frozenset(
    {"po", "sa", "spec-generator"}
)


def _instructions_section(
    ticket: Ticket,
    reason: SpawnReason,
    evaluator_bundle: dict[str, Any] | None = None,
    role: str | None = None,
) -> str:
    if role in _INIT_ROLES_WITH_OWN_INSTRUCTIONS and reason not in (
        SpawnReason.QA_RESPONDER,
        SpawnReason.EVALUATOR,
    ):
        # The role's phase_prompt enumerates tools, files, and the
        # situational next step. Adding the generic block would
        # instruct the agent to call commit_progress / update_ticket,
        # neither of which applies to the init flow.
        return ""
    if reason == SpawnReason.QA_RESPONDER:
        return (
            "## Instructions\n\n"
            f"Respond to the most recent message on ticket {ticket.id}. "
            "When you've answered, call "
            f'`update_ticket(ticket_id="{ticket.id}", status="resolved")`.\n'
        )
    if reason == SpawnReason.EVALUATOR:
        # Instructions reference the pinned handoff id so the agent has
        # one unambiguous target — even if multiple handoffs exist on
        # the ticket, the orchestrator told us which one to evaluate.
        handoff_id = (evaluator_bundle or {}).get("handoff_id") or ""
        hid_literal = f'"{handoff_id}"' if handoff_id else '"<handoff-id>"'
        return (
            "## Instructions\n\n"
            f"Evaluate handoff {hid_literal} on ticket `{ticket.id}`. "
            "Use `read_comments` for additional context as needed. "
            "When you've decided:\n\n"
            f"* Accept: `thread_accept_handoff(handoff_id={hid_literal})`\n"
            f"* Reject: `thread_reject_handoff(handoff_id={hid_literal}, "
            'rejection_reason="...")`\n\n'
            "Do NOT advance the ticket status manually — "
            "`thread_accept_handoff` wires the phase advance for you.\n"
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


def _truncate(text: str, limit: int = _EVAL_EXCERPT_MAX_CHARS) -> str:
    """Trim a body to `limit` chars, appending an ellipsis if cut.

    Used for thread bodies quoted into the evaluator prompt — the full
    record stays in the store, the prompt just needs a recognizable
    excerpt. Keeps one-line records intact and clips long ones.
    """
    t = text.strip()
    if len(t) <= limit:
        return t
    return t[:limit].rstrip() + "…"


def _evaluator_section(
    entries: list[ThreadEntry],
    bundle: dict[str, Any] | None,
) -> str:
    """Render the evaluator-facing bundle: handoff + checks + audit +
    waivers + promoted items + helper drafts.

    Data flow per Phase 5 Task C/E/J/helper-label: orchestrator's
    ``_spawn_evaluator`` pre-fetches the check-results batch (stores
    aren't reachable from the pure prompt builder) and hands it in
    via ``evaluator_bundle``. Everything else lives on the thread
    already, so we just filter ``entries`` by kind + relationship.

    Returns empty string when ``bundle`` is ``None`` — graceful
    degradation on store-read failure keeps the spawn from cascading
    into a prompt-builder crash, at the cost of losing thread-derived
    context (waivers, helper drafts) for that one spawn. A future
    refactor could render the thread-only portion without the bundle;
    today the two are rendered together and fall together.
    """
    if bundle is None:
        return ""

    handoff_id = bundle.get("handoff_id") or ""
    entries_by_id = {e.id: e for e in entries}
    handoff = entries_by_id.get(handoff_id) if handoff_id else None
    # Only pin on a thread entry whose kind is actually a handoff — a
    # stale id pointing at some other entry kind shouldn't accidentally
    # render as one. Same defensive posture as ``_spawn_evaluator``
    # calling ``resolve_evaluator`` without trusting the caller.
    if handoff is not None and handoff.kind != "handoff":
        handoff = None

    parts: list[str] = ["## Evaluator bundle\n"]

    # ---- Handoff record ---------------------------------------------------
    if handoff is not None:
        parts.append(f"### Handoff: {handoff.phase}\n")
        parts.append(f"- **Completed by**: {handoff.author}")
        if handoff.summary:
            parts.append(f"- **Summary**: {_truncate(handoff.summary)}")
        if handoff.outputs:
            parts.append("- **Outputs**:")
            for o in handoff.outputs:
                parts.append(f"  - {o}")
        open_items = [d for d in handoff.deferred_items if d.status == "open"]
        promoted = [
            d
            for d in handoff.deferred_items
            if d.status == "promoted" and d.promoted_ticket_id
        ]
        if open_items:
            parts.append("- **Deferred (open)**:")
            for d in open_items:
                reason_suffix = f" — {d.reason}" if d.reason else ""
                parts.append(f"  - `{d.id}` {d.item}{reason_suffix}")
        if promoted:
            parts.append("- **Promoted to child tickets**:")
            for d in promoted:
                parts.append(f"  - {d.item} → `{d.promoted_ticket_id}`")
        parts.append("")

    # ---- Check results (structured, from bundle) -------------------------
    check_results = bundle.get("check_results") or []
    if check_results:
        parts.append("### Check results\n")
        for r in check_results:
            name = r.get("check_name") or "?"
            verdict = r.get("verdict") or "?"
            # ``r["severity"]`` is whatever the bundle passes — the
            # orchestrator hands in a ``CheckSeverity`` enum (not its
            # ``.value``). In Python 3.11+ ``str(CheckSeverity.REQUIRED)``
            # returns ``"CheckSeverity.REQUIRED"`` rather than
            # ``"required"``, which would leak the class name into the
            # evaluator prompt. Pull ``.value`` when present and fall
            # back to ``str`` so plain-string severities (tests, future
            # callers) still render.
            raw_severity = r.get("severity")
            if raw_severity is None:
                severity = "?"
            else:
                severity = str(getattr(raw_severity, "value", raw_severity))
            parts.append(f"- **{name}** [{severity}]: `{verdict}`")
            # Quote the excerpt only on non-pass — a green dump of every
            # check's stdout would drown the evaluator in noise.
            if verdict != "pass":
                excerpt = (r.get("output") or "").strip()
                if excerpt:
                    parts.append("  ```")
                    parts.append("  " + _truncate(excerpt).replace("\n", "\n  "))
                    parts.append("  ```")
        parts.append("")

    # ---- Check-failure audit events (historical, including waived) -------
    cf_events = [
        e
        for e in entries
        if e.kind == "system_event" and e.event_type == "check_failure"
    ]
    if cf_events:
        parts.append("### Check-failure audit\n")
        parts.append(
            "*Historical failures for this ticket, including currently waived ones.*\n"
        )
        for e in cf_events:
            flag = " **(WAIVED)**" if e.waived else ""
            parts.append(
                f"- `{e.check_name}` [{e.check_severity}] → `{e.check_verdict}`{flag}"
            )
            if e.excerpt:
                parts.append(f"  - {_truncate(e.excerpt)}")
        parts.append("")

    # ---- Active waivers --------------------------------------------------
    check_waivers = [e for e in entries if e.kind == "waiver" and e.check_failure_id]
    obj_waivers = [e for e in entries if e.kind == "waiver" and e.objection_id]
    if check_waivers or obj_waivers:
        parts.append("### Active waivers\n")
        for w in check_waivers:
            target = entries_by_id.get(w.check_failure_id or "")
            name = (
                target.check_name
                if target is not None and target.kind == "system_event"
                else "?"
            )
            parts.append(
                f"- **check_failure** `{name}` waived by **{w.author}**: "
                f"{_truncate(w.justification)}"
            )
        for w in obj_waivers:
            target = entries_by_id.get(w.objection_id or "")
            snippet = (
                _truncate(target.text, 80)
                if target is not None and target.kind == "objection"
                else "?"
            )
            parts.append(
                f'- **objection** "{snippet}" waived by **{w.author}**: '
                f"{_truncate(w.justification)}"
            )
        parts.append("")

    # ---- Helper-agent drafts ---------------------------------------------
    # Notes whose `responds_to` points at a Proposal entry on this thread.
    # Task N posts these with ``author = <helper_role_name>`` — we label
    # them distinctly so the evaluator reads them as context, not as an
    # authoritative human decision. Plain Notes (responds_to=None or
    # pointing at a non-proposal) stay in the normal thread rendering.
    proposal_ids = {e.id for e in entries if e.kind == "proposal"}
    helper_notes = [
        e
        for e in entries
        if e.kind == "note"
        and e.responds_to is not None
        and e.responds_to in proposal_ids
    ]
    if helper_notes:
        parts.append("### Helper-agent drafts\n")
        parts.append(
            "*Drafts from helper agents attached to proposals — context "
            "only, not decisions. Produced by `human_with_helper` routing.*\n"
        )
        for n in helper_notes:
            parts.append(f"- **{n.author}** on proposal `{n.responds_to}`:")
            parts.append(f"  > {_truncate(n.text)}")
        parts.append("")

    # Header-only bundles (no handoff matched, no results, no audit,
    # no waivers, no helper drafts) add no signal — suppress to keep
    # the prompt tight.
    if len(parts) == 1:
        return ""
    return "\n".join(parts) + "\n"


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
    entries: list[ThreadEntry],
    memories: list[str],
    project: Project,
    skills: list[Skill],
    environment_md: str,
    resolved_context: str = "",
    all_roles: list[RoleConfig] | None = None,
    worktree_path: str | None = None,
    phase: PhaseConfig | None = None,
    evaluator_bundle: dict[str, Any] | None = None,
) -> str:
    # Evaluator spawns carry a pre-assembled bundle (handoff id +
    # structured check results) from the orchestrator; Phase 5 Task C/E/J.
    # For non-evaluator spawns the bundle is ignored, keeping the call
    # site uniform.
    eval_section = (
        _evaluator_section(entries, evaluator_bundle)
        if spawn_reason == SpawnReason.EVALUATOR
        else ""
    )
    parts = [
        _role_section(role_cfg, spawn_reason),
        _worktree_section(worktree_path),
        _project_section(project),
        _team_roles_section(role_cfg.role, all_roles or []),
        _skills_section(skills),
        _environment_section(environment_md),
        _memories_section(memories),
        resolved_context,
        _ticket_section(ticket, parent, entries),
        _phase_section(phase, ticket),
        eval_section,
        _instructions_section(
            ticket,
            spawn_reason,
            evaluator_bundle=evaluator_bundle,
            role=role_cfg.role,
        ),
    ]
    return "".join(parts)
