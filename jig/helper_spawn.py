"""Helper-agent spawning for ``human_with_helper`` proposal routing.

Phase 5 Task N. When a proposal routes to a role whose configured
``assignment == "human_with_helper"``, spawn the declared
``helper_template`` role as a short-lived agent. The helper's draft
response lands as a :class:`jig.thread.Note` on the ticket thread,
linked to the originating proposal via ``responds_to``, so the human
reviewer sees it alongside the proposal when the evaluator prompt is
composed.

The helper is:

* **Short-lived** — bounded by ``timeout_s`` (default
  :data:`HELPER_DEFAULT_TIMEOUT_S`). Mirrors ``AgentCheckRunner``'s
  pattern: one side-effecting MCP tool (``submit_helper_draft``)
  plus read-only context access (``Read`` / ``Grep`` / ``Glob``),
  no persistent thread state beyond the Note it emits. The helper
  cannot ``Write``, ``Bash``, spawn sub-agents, or invoke other
  MCPs even if the helper role template would permit it.
* **Best-effort** — timeouts, crashes, and "agent forgot to submit"
  all log a warning and return ``None`` without posting anything. The
  proposal is still routed normally; the human simply doesn't get a
  draft.
* **Context-only** — the Note carries no authority. Accepting /
  rejecting / refining the proposal remains the human's decision
  (Task N, third bullet).

Surfacing the Note distinctly in the evaluator prompt (``"helper
draft"`` vs plain note) rides with the deferred Task C prompt-
composition work; the Note is already visible to the evaluator via
thread iteration today.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TypedDict

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool

from jig.ownership import OwnerRouting
from jig.persistence import load_role
from jig.store.threads import ThreadStore
from jig.thread import Note, Proposal

_logger = logging.getLogger(__name__)

HELPER_DEFAULT_TIMEOUT_S: float = 120.0
"""Default timeout for a single helper spawn. Mirrors the agent-check
budget — long enough for the helper to read context and compose a
short draft, short enough that a hung spawn never blocks the parent
MCP call indefinitely."""


class CapturedDraft(TypedDict, total=False):
    """Mutable slot the spawner owns; ``submit_helper_draft`` writes
    into it exactly once. Mirrors
    :class:`jig.check_mcp.CapturedVerdict`'s role for agent checks."""

    text: str
    call_count: int


def _fresh_slot() -> CapturedDraft:
    return {"text": "", "call_count": 0}


def build_helper_draft_tool(captured: CapturedDraft):
    """Build the ``submit_helper_draft`` SDK tool bound to ``captured``.

    Exposed so tests can call the handler directly without booting a
    real MCP server; the runtime factory below composes it into the
    scoped server for agent use.
    """

    @tool(
        "submit_helper_draft",
        "Submit your draft response to the proposal. Call exactly "
        "once; a second call is an error. ``text`` is the draft "
        "(1-3 paragraphs) the human reviewer will read alongside "
        "the proposal.",
        {"text": str},
    )
    async def submit_helper_draft(args):
        captured["call_count"] = captured.get("call_count", 0) + 1
        if captured["call_count"] > 1:
            raise RuntimeError(
                "submit_helper_draft already called for this run; "
                "drafts are immutable — post a plain Note if you need "
                "to amend"
            )
        captured["text"] = str(args.get("text") or "")
        return {"content": [{"type": "text", "text": "draft submitted"}]}

    return submit_helper_draft


def create_helper_mcp_server(captured: CapturedDraft):
    """Build a scoped MCP server exposing only ``submit_helper_draft``.

    ``captured`` is the dict the spawner reads after the agent quits.
    Initialize with :func:`_fresh_slot` before passing — the tool
    doesn't reset any fields on its own.
    """
    return create_sdk_mcp_server(
        name="jig_helper", tools=[build_helper_draft_tool(captured)]
    )


def _build_helper_prompt(proposal: Proposal) -> str:
    """Initial prompt the helper sees when spawned.

    Intentionally minimal — the helper's role config carries the
    persona / style guidance via ``phase_prompt``; this message just
    hands over the proposal contents and the one tool it needs to
    call.
    """
    target = proposal.target or "(unspecified)"
    section_suffix = f" (section: {proposal.section})" if proposal.section else ""
    change = proposal.change or "(no change payload)"
    rationale = proposal.rationale or "(no rationale)"
    return (
        f"A proposal has been opened on ticket {proposal.ticket_id} by "
        f"{proposal.author}. Draft a short response (1-3 paragraphs) "
        "that the human reviewer will read alongside the proposal to "
        "help decide accept / reject / refine.\n\n"
        f"Target: {target}{section_suffix}\n"
        f"Rationale: {rationale}\n\n"
        f"Proposed change:\n```\n{change}\n```\n\n"
        "When finished, call `submit_helper_draft` with your draft "
        "text. Do not resolve the proposal yourself — the human does "
        "that."
    )


async def spawn_helper_for_proposal(
    *,
    project_path: Path,
    threads: ThreadStore,
    proposal: Proposal,
    routing: OwnerRouting,
    cwd: Path | None = None,
    timeout_s: float = HELPER_DEFAULT_TIMEOUT_S,
) -> str | None:
    """Spawn the helper named by ``routing.helper_template`` for a
    proposal routed to ``human_with_helper``.

    Returns the posted Note id on success; ``None`` when the spawn is
    skipped or fails. Skip conditions — all return ``None`` without
    side effects:

    * ``routing.assignment`` is anything other than
      ``"human_with_helper"``
    * ``routing.helper_template`` is empty
    * the named helper role can't be loaded
    * the SDK spawn raises or times out
    * the helper exits without calling ``submit_helper_draft``
    * the helper submits an empty / whitespace-only draft

    On success, a :class:`Note` is posted on the ticket thread with
    ``responds_to`` set to the originating proposal id and ``author``
    set to the helper role name so readers can distinguish it from a
    human-authored Note without reading the full prompt-composition
    layer that Task C will eventually wire.
    """
    if routing.assignment != "human_with_helper":
        return None
    helper_name = routing.helper_template
    if not helper_name:
        return None

    try:
        role_cfg = load_role(project_path, helper_name)
    except FileNotFoundError:
        _logger.warning(
            "helper role %r not found; skipping helper spawn for proposal %s",
            helper_name,
            proposal.id,
        )
        return None

    captured = _fresh_slot()
    mcp_server = create_helper_mcp_server(captured)
    prompt = _build_helper_prompt(proposal)

    # Hard-scoped allow-list — deliberately NOT inherited from
    # ``role_cfg.allowed_tools``. The helper contract is:
    #
    # * one side-effecting tool: ``submit_helper_draft`` (scoped MCP)
    # * read-only context access: ``Read``, ``Grep``, ``Glob``
    #
    # This mirrors the implementation-aware branch of
    # ``check_runner``'s allow-list. Helpers must not ``Write``,
    # ``Bash``, spawn sub-agents, or invoke other MCPs even if their
    # normal role template would permit it — the helper is a
    # short-lived context-only process with no authority to change
    # the workspace or the ticket.
    allowed_tools: list[str] = [
        "mcp__jig_helper__submit_helper_draft",
        "Read",
        "Grep",
        "Glob",
    ]
    options = ClaudeAgentOptions(
        cwd=str(cwd or project_path),
        allowed_tools=allowed_tools,
        system_prompt=role_cfg.phase_prompt,
        mcp_servers={"jig_helper": mcp_server},
        permission_mode="bypassPermissions",
    )

    async def _drive() -> None:
        async for _msg in query(prompt=prompt, options=options):
            # Stop as soon as the draft is in — the agent may continue
            # to chat, but the spawner only cares about the first
            # submission and has no reason to consume more turns.
            if captured["call_count"] > 0:
                break

    try:
        await asyncio.wait_for(_drive(), timeout=timeout_s)
    except asyncio.TimeoutError:
        _logger.warning(
            "helper %s timed out (%.1fs) on proposal %s",
            helper_name,
            timeout_s,
            proposal.id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "helper %s crashed on proposal %s: %s",
            helper_name,
            proposal.id,
            exc,
        )
        return None

    text = (captured.get("text") or "").strip()
    if captured.get("call_count", 0) == 0 or not text:
        _logger.warning(
            "helper %s exited without a usable draft for proposal %s",
            helper_name,
            proposal.id,
        )
        return None

    note = Note(
        ticket_id=proposal.ticket_id,
        author=helper_name,
        text=text,
        responds_to=proposal.id,
    )
    return await threads.post(note)


__all__ = [
    "CapturedDraft",
    "HELPER_DEFAULT_TIMEOUT_S",
    "build_helper_draft_tool",
    "create_helper_mcp_server",
    "spawn_helper_for_proposal",
]
