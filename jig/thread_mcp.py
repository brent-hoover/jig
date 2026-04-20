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
from typing import Any

from jig.store import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Answer, Question

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


__all__ = [
    "ThreadError",
    "handle_thread_answer",
    "handle_thread_ask",
    "handle_thread_resolve_question",
]
