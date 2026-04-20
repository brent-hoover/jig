"""MCP tool handlers for the typed thread-entry Q&A flow (Phase 4 C).

Three agent-facing tools per doc 08 §Agent tools:

* ``thread_ask`` — post a targeted Question. Non-blocking by default;
  callers opt in to gating via ``blocking=True``.
* ``thread_answer`` — post an Answer against a Question. Posting an
  answer does *not* close the Question — doc 08 §Gating semantics
  makes the asker the only actor who can resolve.
* ``thread_resolve_question`` — the asker's close. Refuses if the
  sender isn't the question's author (resolution asymmetry rule).

This module is deliberately parallel to ``ticket_mcp.handle_ask_question``
/ ``handle_answer_questions`` rather than replacing them. The legacy
pair implements the *operator-pause* UX (status → ``needs_info`` →
human answers → status restored) and is wired into the WebSocket
server and the TUI. Retiring them is Task H work once doc-08 threads
are the canonical conversation surface.

Target validation is intentionally loose here. The plan's Task C
note pins enforcement to Phase 5 — workflow phases will start
declaring ``questions_to`` / ``escalation_targets`` alongside the
policy layer. For now we only reject obvious garbage (empty string).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jig.config import load_config
from jig.store import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import (
    Answer,
    Decision,
    Escalation,
    Note,
    Objection,
    Question,
    Resolution,
    Uncertain,
    Waiver,
)

_logger = logging.getLogger(__name__)


class ThreadError(ValueError):
    """Raised for thread-flow violations the caller should fix
    (e.g. answering a resolved question, non-asker trying to close).
    """


# ---- thread_ask -----------------------------------------------------------


async def handle_thread_ask(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Post a typed Question on a ticket.

    Required args: ``ticket_id``, ``target``, ``question``.
    Optional: ``blocking`` (default False).

    Returns ``{"question_id": ..., "blocking": ..., "target": ...}``.
    """
    ticket_id = args["ticket_id"]
    target = args["target"]
    question_text = args["question"]
    blocking = bool(args.get("blocking", False))

    if not target.strip():
        raise ValueError("target is required")
    if not question_text.strip():
        raise ValueError("question is required")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    q = Question(
        ticket_id=ticket_id,
        author=sender,
        target=target,
        question=question_text,
        blocking=blocking,
    )
    qid = await threads.post(q)

    await bus.publish(
        Message(
            sender=sender,
            to=target,
            type=MessageType.QUESTION,
            payload={
                "kind": "thread_question_posted",
                "ticket_id": ticket_id,
                "question_id": qid,
                "author": sender,
                "target": target,
                "question": question_text,
                "blocking": blocking,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {"question_id": qid, "blocking": blocking, "target": target}


# ---- thread_answer --------------------------------------------------------


async def handle_thread_answer(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Post an Answer pointing at an existing Question.

    Does not close the Question — the asker retains that right.
    Fails loud if the Question is already resolved so the caller
    doesn't bury an answer on a closed entry.

    Required args: ``question_id``, ``text``.
    """
    question_id = args["question_id"]
    text = args["text"]

    if not text.strip():
        raise ValueError("text is required")

    q = await threads.get(question_id)
    if q is None:
        raise KeyError(f"question {question_id!r} not found")
    if not isinstance(q, Question):
        raise ThreadError(
            f"entry {question_id!r} is a {q.kind!r}, not a question"
        )
    if q.is_resolved():
        raise ThreadError(
            f"question {question_id!r} is already resolved "
            f"(resolved_by={q.resolved_by!r})"
        )

    answer = Answer(
        ticket_id=q.ticket_id,
        author=sender,
        question_id=question_id,
        text=text,
    )
    aid = await threads.post(answer)

    await bus.publish(
        Message(
            sender=sender,
            to=q.author,
            type=MessageType.ANSWER,
            payload={
                "kind": "thread_answer_posted",
                "ticket_id": q.ticket_id,
                "question_id": question_id,
                "answer_id": aid,
                "author": sender,
                "text": text,
            },
            topic=f"tickets.{q.ticket_id}",
        )
    )
    return {"answer_id": aid, "question_id": question_id}


# ---- thread_resolve_question ----------------------------------------------


async def handle_thread_resolve_question(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Close a Question — asker-only, per doc 08 §Gating semantics.

    Required args: ``question_id``.
    Optional: ``accepted_answer_id``, ``reason`` (free-text note that
    lands alongside the close event on the bus but isn't a Decision
    record; use ``thread_decision`` in Task E for that).

    Returns ``{"question_id": ..., "resolved_by": sender,
    "accepted_answer_id": ...}``.
    """
    question_id = args["question_id"]
    accepted_answer_id = args.get("accepted_answer_id")
    reason = args.get("reason")

    q = await threads.get(question_id)
    if q is None:
        raise KeyError(f"question {question_id!r} not found")
    if not isinstance(q, Question):
        raise ThreadError(
            f"entry {question_id!r} is a {q.kind!r}, not a question"
        )
    if q.is_resolved():
        raise ThreadError(
            f"question {question_id!r} is already resolved "
            f"(resolved_by={q.resolved_by!r})"
        )
    if sender != q.author:
        raise ThreadError(
            f"only the asker can resolve a question "
            f"(question.author={q.author!r}, sender={sender!r})"
        )

    changes: dict[str, Any] = {"resolved_by": sender}
    if accepted_answer_id is not None:
        # Validate the answer exists and points back at this question.
        a = await threads.get(accepted_answer_id)
        if a is None:
            raise KeyError(
                f"accepted_answer_id {accepted_answer_id!r} not found"
            )
        if not isinstance(a, Answer):
            raise ThreadError(
                f"entry {accepted_answer_id!r} is a {a.kind!r}, not an answer"
            )
        if a.question_id != question_id:
            raise ThreadError(
                f"answer {accepted_answer_id!r} is not against "
                f"question {question_id!r}"
            )
        changes["accepted_answer_id"] = accepted_answer_id
    await threads.update(question_id, changes)

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_question_resolved",
                "ticket_id": q.ticket_id,
                "question_id": question_id,
                "resolved_by": sender,
                "accepted_answer_id": accepted_answer_id,
                "reason": reason,
            },
            topic=f"tickets.{q.ticket_id}",
        )
    )
    return {
        "question_id": question_id,
        "resolved_by": sender,
        "accepted_answer_id": accepted_answer_id,
    }


# ---- thread_object --------------------------------------------------------


async def handle_thread_object(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Post an Objection against a specific artifact.

    Objections are *always* blocking — ``is_blocking`` is True until
    the objector accepts a Resolution or an authorized actor waives.

    Required args: ``ticket_id``, ``target_artifact``, ``text``.
    """
    ticket_id = args["ticket_id"]
    target_artifact = args["target_artifact"]
    text = args["text"]

    if not target_artifact.strip():
        raise ValueError("target_artifact is required")
    if not text.strip():
        raise ValueError("text is required")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    o = Objection(
        ticket_id=ticket_id,
        author=sender,
        target_artifact=target_artifact,
        text=text,
    )
    oid = await threads.post(o)

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_objection_posted",
                "ticket_id": ticket_id,
                "objection_id": oid,
                "author": sender,
                "target_artifact": target_artifact,
                "text": text,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {"objection_id": oid, "blocking": True}


# ---- thread_resolve_objection ---------------------------------------------


async def handle_thread_resolve_objection(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Post a Resolution entry pointing at an Objection.

    Does NOT close the Objection — per doc 08 the objector must
    accept via ``thread_accept_resolution`` before the block lifts.
    Fails loud if the Objection is already resolved or waived so
    the caller doesn't stack resolutions on a closed entry.

    Required args: ``objection_id``, ``text``.
    """
    objection_id = args["objection_id"]
    text = args["text"]

    if not text.strip():
        raise ValueError("text is required")

    o = await threads.get(objection_id)
    if o is None:
        raise KeyError(f"objection {objection_id!r} not found")
    if not isinstance(o, Objection):
        raise ThreadError(
            f"entry {objection_id!r} is a {o.kind!r}, not an objection"
        )
    if o.is_resolved():
        raise ThreadError(
            f"objection {objection_id!r} is already resolved "
            f"(resolved_by={o.resolved_by!r}, waived_by={o.waived_by!r})"
        )

    r = Resolution(
        ticket_id=o.ticket_id,
        author=sender,
        objection_id=objection_id,
        text=text,
    )
    rid = await threads.post(r)

    await bus.publish(
        Message(
            sender=sender,
            to=o.author,
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_resolution_posted",
                "ticket_id": o.ticket_id,
                "objection_id": objection_id,
                "resolution_id": rid,
                "author": sender,
                "text": text,
            },
            topic=f"tickets.{o.ticket_id}",
        )
    )
    return {"resolution_id": rid, "objection_id": objection_id}


# ---- thread_accept_resolution ---------------------------------------------


async def handle_thread_accept_resolution(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Close an Objection — objector-only, per doc 08 §Gating semantics.

    The objector is the only actor who can accept a Resolution. Non-
    objector callers get a clear error rather than a silent no-op.

    Required args: ``objection_id``.
    """
    objection_id = args["objection_id"]

    o = await threads.get(objection_id)
    if o is None:
        raise KeyError(f"objection {objection_id!r} not found")
    if not isinstance(o, Objection):
        raise ThreadError(
            f"entry {objection_id!r} is a {o.kind!r}, not an objection"
        )
    if o.is_resolved():
        raise ThreadError(
            f"objection {objection_id!r} is already resolved "
            f"(resolved_by={o.resolved_by!r}, waived_by={o.waived_by!r})"
        )
    if sender != o.author:
        raise ThreadError(
            f"only the objector can accept a resolution "
            f"(objection.author={o.author!r}, sender={sender!r})"
        )

    await threads.update(objection_id, {"resolved_by": sender})

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_objection_resolved",
                "ticket_id": o.ticket_id,
                "objection_id": objection_id,
                "resolved_by": sender,
            },
            topic=f"tickets.{o.ticket_id}",
        )
    )
    return {"objection_id": objection_id, "resolved_by": sender}


# ---- thread_waive ---------------------------------------------------------


async def handle_thread_waive(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
) -> dict[str, Any]:
    """Override an Objection via authorized Waiver.

    The Waiver and the original Objection both remain in the thread
    — the audit trail is the point per doc 08 §Waivers. The Objection
    flips to waived-with-reason (``waived_by=sender``), which causes
    ``is_blocking()`` to return False.

    Authorization: ``sender`` must be in ``config.waiver_authority``.
    Phase 5's capability-policy layer (doc 16) supersedes this with
    proper capability tokens; the flat list is a bridge until then.

    Required args: ``objection_id``, ``justification``.
    """
    objection_id = args["objection_id"]
    justification = args["justification"]

    if not justification.strip():
        raise ValueError("justification is required")

    cfg = load_config(project_path)
    if sender not in cfg.waiver_authority:
        raise ThreadError(
            f"{sender!r} is not authorized to waive objections "
            f"(config.waiver_authority={cfg.waiver_authority!r})"
        )

    o = await threads.get(objection_id)
    if o is None:
        raise KeyError(f"objection {objection_id!r} not found")
    if not isinstance(o, Objection):
        raise ThreadError(
            f"entry {objection_id!r} is a {o.kind!r}, not an objection"
        )
    if o.is_resolved():
        raise ThreadError(
            f"objection {objection_id!r} is already resolved "
            f"(resolved_by={o.resolved_by!r}, waived_by={o.waived_by!r})"
        )

    w = Waiver(
        ticket_id=o.ticket_id,
        author=sender,
        objection_id=objection_id,
        justification=justification,
    )
    wid = await threads.post(w)
    await threads.update(objection_id, {"waived_by": sender})

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_objection_waived",
                "ticket_id": o.ticket_id,
                "objection_id": objection_id,
                "waiver_id": wid,
                "waived_by": sender,
                "justification": justification,
            },
            topic=f"tickets.{o.ticket_id}",
        )
    )
    return {
        "waiver_id": wid,
        "objection_id": objection_id,
        "waived_by": sender,
    }


# ---- thread_decide --------------------------------------------------------


def _next_decision_seq(project_path: Path, ticket_id: str) -> int:
    """Scan ``.jig/decisions/`` for the next decision-record sequence
    for ``ticket_id``. Sequences start at 1 and are per-ticket.

    Collisions across racing callers are resolved in the caller: we
    write with the derived seq, and if two writers pick the same slot
    the later write wins (the earlier record is overwritten).
    doc 17 §Decision records says the sequence is advisory, not a key
    — the canonical record is the thread `Decision` entry.
    """
    root = project_path / ".jig" / "decisions"
    if not root.is_dir():
        return 1
    prefix = f"{ticket_id}-"
    highest = 0
    for entry in root.iterdir():
        name = entry.name
        if not name.endswith(".md") or not name.startswith(prefix):
            continue
        tail = name[len(prefix) : -len(".md")]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


def _write_decision_record(
    project_path: Path,
    *,
    ticket_id: str,
    seq: int,
    decision: str,
    rationale: str,
    author: str,
    entry_id: str,
) -> Path:
    """Write the standalone `.jig/decisions/<ticket-id>-<seq>.md`
    mirror per doc 17.
    """
    root = project_path / ".jig" / "decisions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{ticket_id}-{seq}.md"
    body = (
        f"# Decision — {ticket_id}-{seq}\n\n"
        f"- **Author:** {author}\n"
        f"- **Ticket:** {ticket_id}\n"
        f"- **Thread entry:** {entry_id}\n\n"
        f"## Decision\n\n{decision}\n\n"
        f"## Rationale\n\n{rationale}\n"
    )
    path.write_text(body)
    return path


async def handle_thread_decide(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
) -> dict[str, Any]:
    """Record a Decision in the thread and mirror to a decision record.

    Decision entries are auto-resolved (no gating lifecycle). The
    standalone file under ``.jig/decisions/`` is the doc-17 artifact.

    Required args: ``ticket_id``, ``decision``, ``rationale``.
    """
    ticket_id = args["ticket_id"]
    decision_text = args["decision"]
    rationale = args["rationale"]

    if not decision_text.strip():
        raise ValueError("decision is required")
    if not rationale.strip():
        raise ValueError("rationale is required")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    d = Decision(
        ticket_id=ticket_id,
        author=sender,
        decision=decision_text,
        rationale=rationale,
    )
    did = await threads.post(d)

    seq = _next_decision_seq(project_path, ticket_id)
    record_path = _write_decision_record(
        project_path,
        ticket_id=ticket_id,
        seq=seq,
        decision=decision_text,
        rationale=rationale,
        author=sender,
        entry_id=did,
    )

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_decision_posted",
                "ticket_id": ticket_id,
                "decision_id": did,
                "author": sender,
                "decision": decision_text,
                "rationale": rationale,
                "record_path": str(record_path.relative_to(project_path)),
                "seq": seq,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {
        "decision_id": did,
        "record_path": str(record_path.relative_to(project_path)),
        "seq": seq,
    }


# ---- thread_note ----------------------------------------------------------


async def handle_thread_note(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Post a freeform Note. Auto-resolved, never blocking.

    Required args: ``ticket_id``, ``text``.
    """
    ticket_id = args["ticket_id"]
    text = args["text"]

    if not text.strip():
        raise ValueError("text is required")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    n = Note(ticket_id=ticket_id, author=sender, text=text)
    nid = await threads.post(n)

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_note_posted",
                "ticket_id": ticket_id,
                "note_id": nid,
                "author": sender,
                "text": text,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {"note_id": nid}


# ---- thread_escalate ------------------------------------------------------


async def handle_thread_escalate(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    valid_roles: frozenset[str] = frozenset(),
    phase_escalation_targets: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Post an Escalation. Always blocking until a resolver acts.

    Target validation is best-effort in Phase 4: if
    ``phase_escalation_targets`` is supplied and ``target`` isn't in
    it, log a warning but don't refuse. Phase 5 flips this to a hard
    check once workflow phases declare their targets.

    Required args: ``ticket_id``, ``reason``, ``details``.
    Optional: ``target`` (default ``"human"``).
    """
    ticket_id = args["ticket_id"]
    reason = args["reason"]
    details = args["details"]
    target = args.get("target", "human")

    if not reason.strip():
        raise ValueError("reason is required")
    if not details.strip():
        raise ValueError("details is required")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    if (
        phase_escalation_targets is not None
        and target not in phase_escalation_targets
    ):
        _logger.warning(
            "escalation target %r not in phase escalation_targets %s "
            "(ticket=%s, sender=%s); allowing in Phase 4",
            target,
            sorted(phase_escalation_targets),
            ticket_id,
            sender,
        )
    elif (
        valid_roles
        and target != "human"
        and target not in valid_roles
    ):
        _logger.warning(
            "escalation target %r is not a known role or 'human' "
            "(ticket=%s, sender=%s); allowing in Phase 4",
            target,
            ticket_id,
            sender,
        )

    e = Escalation(
        ticket_id=ticket_id,
        author=sender,
        reason=reason,
        details=details,
        target=target,
    )
    eid = await threads.post(e)

    await bus.publish(
        Message(
            sender=sender,
            to=target,
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_escalation_posted",
                "ticket_id": ticket_id,
                "escalation_id": eid,
                "author": sender,
                "reason": reason,
                "details": details,
                "target": target,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {
        "escalation_id": eid,
        "target": target,
        "blocking": True,
    }


# ---- thread_uncertain -----------------------------------------------------


def _route_uncertain(
    details: str, valid_roles: frozenset[str]
) -> str | None:
    """Phase 4 routing heuristic: if ``details`` mentions a known role
    name as a whole word, return that role. Otherwise return None and
    the caller falls back to an Escalation.

    Matching is case-insensitive and word-bounded so ``"ask the SA to
    review"`` routes to ``sa`` without ``"saffron"`` colliding. Ties
    are broken by first-mention order.
    """
    if not valid_roles:
        return None
    import re

    lowered = details.lower()
    best: tuple[int, str] | None = None
    for role in valid_roles:
        if not role:
            continue
        for m in re.finditer(
            rf"\b{re.escape(role.lower())}\b", lowered
        ):
            idx = m.start()
            if best is None or idx < best[0]:
                best = (idx, role)
            break
    return best[1] if best else None


async def handle_thread_uncertain(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    valid_roles: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Record an Uncertain and route it per the Phase 4 heuristic.

    The Uncertain entry itself is not blocking — it's a routing
    request. The derived Question (non-blocking by default) or
    Escalation (blocking) carries the gating.

    Required args: ``ticket_id``, ``details``.
    """
    ticket_id = args["ticket_id"]
    details = args["details"]

    if not details.strip():
        raise ValueError("details is required")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    u = Uncertain(ticket_id=ticket_id, author=sender, details=details)
    uid = await threads.post(u)

    routed_role = _route_uncertain(details, valid_roles)
    if routed_role is not None:
        q = Question(
            ticket_id=ticket_id,
            author=sender,
            target=routed_role,
            question=details,
            blocking=False,
        )
        qid = await threads.post(q)
        await bus.publish(
            Message(
                sender=sender,
                to=routed_role,
                type=MessageType.QUESTION,
                payload={
                    "kind": "thread_uncertain_routed_question",
                    "ticket_id": ticket_id,
                    "uncertain_id": uid,
                    "question_id": qid,
                    "target": routed_role,
                    "details": details,
                },
                topic=f"tickets.{ticket_id}",
            )
        )
        return {
            "uncertain_id": uid,
            "routed": "question",
            "question_id": qid,
            "target": routed_role,
        }

    esc = Escalation(
        ticket_id=ticket_id,
        author=sender,
        reason="uncertain_unroutable",
        details=details,
        target="human",
    )
    eid = await threads.post(esc)
    await bus.publish(
        Message(
            sender=sender,
            to="human",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_uncertain_escalated",
                "ticket_id": ticket_id,
                "uncertain_id": uid,
                "escalation_id": eid,
                "details": details,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {
        "uncertain_id": uid,
        "routed": "escalation",
        "escalation_id": eid,
        "target": "human",
    }


__all__ = [
    "ThreadError",
    "handle_thread_accept_resolution",
    "handle_thread_answer",
    "handle_thread_ask",
    "handle_thread_decide",
    "handle_thread_escalate",
    "handle_thread_note",
    "handle_thread_object",
    "handle_thread_resolve_objection",
    "handle_thread_resolve_question",
    "handle_thread_uncertain",
    "handle_thread_waive",
]
