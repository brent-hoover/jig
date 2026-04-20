"""Checkpoint MCP handlers per doc 09 / Phase 4 Task G.

Three agent-facing tools:

* ``checkpoint_milestone(description, position, plan, completed=[],
  ruled_out=[])`` — the workhorse "here's where I am" snapshot.
* ``checkpoint_decision(decision_id, rationale)`` — references a
  thread ``Decision`` entry; the checkpoint mirrors its context so
  retries/resumptions see the decision alongside position notes.
* ``checkpoint_deferred(item, reason)`` — single-item append; the
  item surfaces on the next Handoff's ``deferred_items`` for the
  evaluator to review.

Plus three harness hooks invoked from the orchestration layer
(``ticket_mcp``/``thread_mcp``) — **not** from agent MCP tools:

* ``record_auto_commit_checkpoint`` — after ``commit_worktree``
  succeeds.
* ``record_auto_test_checkpoint`` — after lint/test runs; status
  encoded in ``description`` / ``open_questions``.
* ``record_auto_pre_handoff_checkpoint`` — from
  ``handle_thread_handoff`` right before the Handoff entry lands.

The handlers raise ``CheckpointError`` for semantic failures
(unknown ticket, empty item, etc.). The MCP wrapper catches and
translates into tool output; callers inside the codebase propagate.
"""

from __future__ import annotations

from typing import Any

from jig.checkpoints import Checkpoint, RuledOut
from jig.store.checkpoints import CheckpointStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import DeferredItem, Decision


class CheckpointError(RuntimeError):
    """Raised for semantic validation failures in checkpoint handlers."""


# ---- helpers --------------------------------------------------------------


def _coerce_ruled_out(raw: Any) -> list[RuledOut]:
    """Accept ``RuledOut`` models, ``{"approach", "reason"}`` dicts, or
    bare strings (wrapped as ``approach`` with no reason) so callers
    can be loose at the MCP boundary."""
    if not raw:
        return []
    out: list[RuledOut] = []
    for entry in raw:
        if isinstance(entry, RuledOut):
            out.append(entry)
        elif isinstance(entry, dict):
            out.append(RuledOut.model_validate(entry))
        elif isinstance(entry, str):
            out.append(RuledOut(approach=entry, reason=""))
        else:
            raise CheckpointError(
                f"ruled_out entry must be RuledOut, dict, or str — got "
                f"{type(entry).__name__}"
            )
    return out


def _coerce_deferred(raw: Any) -> list[DeferredItem]:
    if not raw:
        return []
    out: list[DeferredItem] = []
    for entry in raw:
        if isinstance(entry, DeferredItem):
            out.append(entry)
        elif isinstance(entry, dict):
            out.append(DeferredItem.model_validate(entry))
        elif isinstance(entry, str):
            out.append(DeferredItem(item=entry))
        else:
            raise CheckpointError(
                f"deferred entry must be DeferredItem, dict, or str — got "
                f"{type(entry).__name__}"
            )
    return out


async def _require_ticket(tickets: TicketStore, ticket_id: str) -> None:
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise CheckpointError(f"ticket {ticket_id!r} not found")


# ---- agent-facing handlers -----------------------------------------------


async def handle_checkpoint_milestone(
    *,
    tickets: TicketStore,
    checkpoints: CheckpointStore,
    sender: str,
    phase_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Write an ``agent_milestone`` checkpoint.

    ``phase_name`` is the current workflow phase the agent is running
    (passed by the MCP server from the spawn context — the agent
    doesn't supply it). Empty phase_name falls back to ``"<unknown>"``
    so the record is still queryable but flagged.
    """
    ticket_id = args["ticket_id"]
    await _require_ticket(tickets, ticket_id)

    cp = Checkpoint(
        ticket_id=ticket_id,
        phase=phase_name or "<unknown>",
        author=sender,
        description=args.get("description", ""),
        position=args.get("position", ""),
        plan=args.get("plan", ""),
        completed=list(args.get("completed") or []),
        ruled_out=_coerce_ruled_out(args.get("ruled_out")),
        open_questions=list(args.get("open_questions") or []),
        trigger="agent_milestone",
    )
    cid = await checkpoints.post(cp)
    return {"checkpoint_id": cid}


async def handle_checkpoint_decision(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    checkpoints: CheckpointStore,
    sender: str,
    phase_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Mirror a thread ``Decision`` into a checkpoint.

    The thread Decision is canonical; this checkpoint captures the
    agent's position at decision time so resumptions see it alongside
    other state notes. Fails loud if the referenced entry isn't a
    ``Decision`` — we don't silently promote a different entry type.
    """
    decision_id = args["decision_id"]
    entry = await threads.get(decision_id)
    if entry is None:
        raise CheckpointError(f"decision {decision_id!r} not found")
    if not isinstance(entry, Decision):
        raise CheckpointError(
            f"thread entry {decision_id!r} is a "
            f"{entry.kind!r}, not a decision"
        )

    await _require_ticket(tickets, entry.ticket_id)

    description = (
        f"decision: {entry.decision}"
        if entry.decision
        else "decision (no summary)"
    )
    cp = Checkpoint(
        ticket_id=entry.ticket_id,
        phase=phase_name or "<unknown>",
        author=sender,
        description=description,
        plan=args.get("rationale", entry.rationale),
        trigger="agent_decision",
    )
    cid = await checkpoints.post(cp)
    return {
        "checkpoint_id": cid,
        "decision_id": decision_id,
    }


async def handle_checkpoint_deferred(
    *,
    tickets: TicketStore,
    checkpoints: CheckpointStore,
    sender: str,
    phase_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Append a single deferred item.

    Arrives at the next Handoff as ``deferred_items`` for the
    evaluator. Bare ``item`` strings are accepted.
    """
    ticket_id = args["ticket_id"]
    await _require_ticket(tickets, ticket_id)
    item = args.get("item", "").strip()
    if not item:
        raise CheckpointError("deferred item must be non-empty")

    deferred = DeferredItem(item=item, reason=args.get("reason", ""))
    cp = Checkpoint(
        ticket_id=ticket_id,
        phase=phase_name or "<unknown>",
        author=sender,
        description=f"deferred: {item}",
        deferred=[deferred],
        trigger="agent_deferred",
    )
    cid = await checkpoints.post(cp)
    return {"checkpoint_id": cid, "item": item}


# ---- harness hooks --------------------------------------------------------


async def record_auto_commit_checkpoint(
    *,
    checkpoints: CheckpointStore,
    ticket_id: str,
    phase_name: str,
    author: str,
    commit_sha: str,
    message: str,
) -> str:
    """Harness-triggered: write after a successful ``commit_worktree``.

    ``phase_name`` may be empty for non-phase spawns (QA responders,
    etc.); it's accepted and recorded as ``"<unknown>"`` so the
    checkpoint still survives replay.
    """
    cp = Checkpoint(
        ticket_id=ticket_id,
        phase=phase_name or "<unknown>",
        author=author,
        description=f"commit {commit_sha[:7]}: {message}",
        completed=[message],
        position=f"committed {commit_sha[:7]}",
        trigger="auto_commit",
    )
    return await checkpoints.post(cp)


async def record_auto_test_checkpoint(
    *,
    checkpoints: CheckpointStore,
    ticket_id: str,
    phase_name: str,
    author: str,
    passed: bool,
    summary: str,
    open_questions: list[str] | None = None,
) -> str:
    """Harness-triggered: write after lint/test, regardless of outcome.

    ``summary`` is a terse human-readable blurb (e.g., "ruff clean" or
    "3 unfixable lint errors"). ``passed`` stamps the description
    prefix; failures populate ``open_questions`` so the next
    checkpoint read surfaces what to address.
    """
    prefix = "test pass" if passed else "test fail"
    cp = Checkpoint(
        ticket_id=ticket_id,
        phase=phase_name or "<unknown>",
        author=author,
        description=f"{prefix}: {summary}",
        position="tests green" if passed else "tests red",
        open_questions=list(open_questions or []),
        trigger="auto_test",
    )
    return await checkpoints.post(cp)


async def record_auto_pre_handoff_checkpoint(
    *,
    checkpoints: CheckpointStore,
    ticket_id: str,
    phase_name: str,
    author: str,
    outputs: list[str],
    summary: str,
    deferred: list[DeferredItem] | None = None,
) -> str:
    """Harness-triggered: written by ``handle_thread_handoff`` before
    the Handoff entry itself.

    Keeps a last-known-state snapshot at phase-closing time that's
    queryable independently of the thread (useful for rejection
    retries that want to pick up from exactly where the Handoff was
    posted).
    """
    cp = Checkpoint(
        ticket_id=ticket_id,
        phase=phase_name,
        author=author,
        description=f"pre-handoff: {summary}" if summary else "pre-handoff",
        completed=list(outputs or []),
        position="handoff posted",
        deferred=list(deferred or []),
        trigger="auto_pre_handoff",
    )
    return await checkpoints.post(cp)


__all__ = [
    "CheckpointError",
    "handle_checkpoint_decision",
    "handle_checkpoint_deferred",
    "handle_checkpoint_milestone",
    "record_auto_commit_checkpoint",
    "record_auto_pre_handoff_checkpoint",
    "record_auto_test_checkpoint",
]
