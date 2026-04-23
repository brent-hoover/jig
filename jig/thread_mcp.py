"""MCP tool handlers for the typed thread-entry Q&A flow (Phase 4 C).

Three agent-facing tools per doc 08 §Agent tools:

* ``thread_ask`` — post a targeted Question. Non-blocking by default;
  callers opt in to gating via ``blocking=True``.
* ``thread_answer`` — post an Answer against a Question. Posting an
  answer does *not* close the Question — doc 08 §Gating semantics
  makes the asker the only actor who can resolve.
* ``thread_resolve_question`` — the asker's close. Refuses if the
  sender isn't the question's author (resolution asymmetry rule).

These tools are the typed, in-band Q&A surface for agent-to-agent
conversation. The operator-pause UX (``ask_question`` MCP tool and
``answer_questions`` WebSocket command — flip ticket to
``needs_info`` / restore to ``in_progress``) lives directly at its
surfaces (``mcp_server.py`` and ``ws_server.py``) now that the doc-08
thread types are the canonical conversation store.

Target validation: Phase 5 Task K flips the post-time checks from
warn-only to hard refusal. ``handle_thread_ask`` and
``handle_thread_escalate`` both take an optional phase allow-list
kwarg (``phase_questions_to`` / ``phase_escalation_targets``). When
the kwarg is ``None`` the handler stays permissive — phases that
don't declare the field on disk inherit today's "anything goes"
behavior. When the kwarg is a frozenset, the caller must either
hit a listed target or the always-allowed escape hatch
``{"human", "any_human"}``; anything else raises
:class:`ThreadError`. Catalog-load validation (``jig validate``)
still rejects phases that name unknown roles, so post-time checks
are purely additive.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jig.checkpoint_mcp import record_auto_pre_handoff_checkpoint
from jig.evaluator_resolver import (
    ResolvedEvaluator,
    natural_next_role,
    resolve_evaluator,
)
from jig.store import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.models import WorkflowConfig
from jig.persistence import load_workflow

if TYPE_CHECKING:
    from jig.store.checkpoints import CheckpointStore
from jig.thread import (
    Answer,
    Decision,
    DeferredItem,
    Escalation,
    Handoff,
    Note,
    Objection,
    Question,
    Resolution,
    SystemEvent,
    Uncertain,
    Waiver,
)

_logger = logging.getLogger(__name__)


class ThreadError(ValueError):
    """Raised for thread-flow violations the caller should fix
    (e.g. answering a resolved question, non-asker trying to close).
    """


# Phase 5 Task K: targets that always pass the phase allow-list,
# regardless of what the phase declares. These are the human-operator
# escape hatches — a phase that limits ``questions_to: [reviewer]`` still
# needs a way for a confused agent to page a human, and the operator
# pause UX depends on ``any_human`` Questions landing.
_HUMAN_TARGET_ESCAPE_HATCH: frozenset[str] = frozenset({"human", "any_human"})


def _require_target_in_phase_allowlist(
    target: str,
    *,
    allowlist: frozenset[str] | None,
    field_name: str,
) -> None:
    """Enforce a phase's ``questions_to`` / ``escalation_targets`` list.

    ``allowlist=None`` means the phase didn't declare the field — stay
    permissive. An empty frozenset is treated the same (nothing
    declared, nothing to enforce) so callers don't need to normalize.
    """
    if not allowlist:
        return
    if target in _HUMAN_TARGET_ESCAPE_HATCH:
        return
    if target in allowlist:
        return
    raise ThreadError(
        f"target {target!r} is not permitted by this phase's {field_name} "
        f"(allowed: {sorted(allowlist)} plus human escape hatch "
        f"{sorted(_HUMAN_TARGET_ESCAPE_HATCH)})"
    )


def _require_waive_token(
    token: str,
    *,
    role: str,
    can_waive: frozenset[str],
    subject: str,
) -> None:
    """Raise :class:`ThreadError` if ``token`` is not in ``can_waive``.

    Error names the exact missing token so the operator knows what to
    add to ``capabilities.waivers.can_waive`` in the role YAML. The
    current list is sorted for deterministic output."""

    if token not in can_waive:
        raise ThreadError(
            f"role {role!r} cannot waive {subject} — "
            f"capabilities.waivers.can_waive must include {token!r} "
            f"(current: {sorted(can_waive)})"
        )


# ---- thread_ask -----------------------------------------------------------


async def handle_thread_ask(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    phase_questions_to: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Post a typed Question on a ticket.

    Required args: ``ticket_id``, ``target``, ``question``.
    Optional: ``blocking`` (default False).

    ``phase_questions_to`` is the phase's declared allow-list of
    legal Question targets (Phase 5 Task K). ``None`` (or empty)
    keeps today's permissive behavior; a populated frozenset
    refuses targets that aren't in it, except for the always-
    allowed human escape hatch (``human`` / ``any_human``).

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

    _require_target_in_phase_allowlist(
        target, allowlist=phase_questions_to, field_name="questions_to"
    )

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
        raise ThreadError(f"entry {question_id!r} is a {q.kind!r}, not a question")
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
        raise ThreadError(f"entry {question_id!r} is a {q.kind!r}, not a question")
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
            raise KeyError(f"accepted_answer_id {accepted_answer_id!r} not found")
        if not isinstance(a, Answer):
            raise ThreadError(
                f"entry {accepted_answer_id!r} is a {a.kind!r}, not an answer"
            )
        if a.question_id != question_id:
            raise ThreadError(
                f"answer {accepted_answer_id!r} is not against question {question_id!r}"
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
        raise ThreadError(f"entry {objection_id!r} is a {o.kind!r}, not an objection")
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
        raise ThreadError(f"entry {objection_id!r} is a {o.kind!r}, not an objection")
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
    can_waive: frozenset[str],
    args: dict[str, Any],
) -> dict[str, Any]:
    """Override an Objection via authorized Waiver.

    The Waiver and the original Objection both remain in the thread
    — the audit trail is the point per doc 08 §Waivers. The Objection
    flips to waived-with-reason (``waived_by=sender``), which causes
    ``is_blocking()`` to return False.

    Authorization: ``sender``'s compiled ``can_waive`` set must include
    ``"objection"``. The set is materialised at agent spawn time from
    ``RoleConfig.capabilities.waivers.can_waive`` unioned with any
    phase-level ``capability_overrides`` (doc 16 §Capability policy).

    Required args: ``objection_id``, ``justification``.
    """
    objection_id = args["objection_id"]
    justification = args["justification"]

    if not justification.strip():
        raise ValueError("justification is required")

    _require_waive_token(
        "objection",
        role=sender,
        can_waive=can_waive,
        subject="objections",
    )

    o = await threads.get(objection_id)
    if o is None:
        raise KeyError(f"objection {objection_id!r} not found")
    if not isinstance(o, Objection):
        raise ThreadError(f"entry {objection_id!r} is a {o.kind!r}, not an objection")
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


# ---- thread_waive_check ---------------------------------------------------


async def _latest_check_failure_for_name(
    *,
    threads: ThreadStore,
    ticket_id: str,
    check_name: str,
) -> SystemEvent | None:
    """Return the most recent unwaived ``check_failure`` SystemEvent
    for ``ticket_id`` matching ``check_name``, or ``None``.

    We walk the thread newest-first and pick the first match. Already-
    waived entries are skipped so waiving a name twice (after a re-run)
    targets the new failure rather than re-flipping the old one.
    """
    entries = await threads.for_ticket(ticket_id)
    for entry in reversed(entries):
        if not isinstance(entry, SystemEvent):
            continue
        if entry.event_type != "check_failure":
            continue
        if entry.check_name != check_name:
            continue
        if entry.waived:
            continue
        return entry
    return None


async def handle_thread_waive_check(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    can_waive: frozenset[str],
    args: dict[str, Any],
) -> dict[str, Any]:
    """Waive a failing check_failure with justification.

    Scoped to a specific ``check_failure`` SystemEvent — either by the
    entry id (``check_failure_id``) or by (``ticket_id``, ``check_name``)
    which resolves to the most recent unwaived failure for that name.

    Posts a ``Waiver`` entry with ``check_failure_id`` and flips the
    target SystemEvent's ``waived`` flag to True so the check gate
    (``jig.check_gate.evaluate_handoff_gate``) stops treating the
    failure as blocking.

    Authorization: ``sender``'s compiled ``can_waive`` set must include
    the severity-qualified token ``"check_failure:<severity>"``. The
    event must be looked up before the auth check because severity
    drives the token — an unauthorized caller passing a bogus id gets
    a KeyError ("not found") rather than a ThreadError. This is
    acceptable because any caller with thread-read access can confirm
    existence via other tools. Malformed events with
    ``check_severity is None`` raise a distinct ThreadError so the
    operator sees "malformed event" rather than a misleading
    "missing capability 'check_failure:None'" message.

    Required args: ``justification`` plus one of
    (``check_failure_id``) or (``ticket_id``, ``check_name``).
    """
    justification = args["justification"]
    if not justification.strip():
        raise ValueError("justification is required")

    check_failure_id = args.get("check_failure_id")
    ticket_id = args.get("ticket_id")
    check_name = args.get("check_name")

    if check_failure_id:
        ev = await threads.get(check_failure_id)
        if ev is None:
            raise KeyError(f"check_failure {check_failure_id!r} not found")
        if not isinstance(ev, SystemEvent) or ev.event_type != "check_failure":
            raise ThreadError(
                f"entry {check_failure_id!r} is not a check_failure event"
            )
    elif ticket_id and check_name:
        if await tickets.get(ticket_id) is None:
            raise KeyError(f"ticket {ticket_id} not found")
        ev = await _latest_check_failure_for_name(
            threads=threads,
            ticket_id=ticket_id,
            check_name=check_name,
        )
        if ev is None:
            raise ThreadError(
                f"no unwaived check_failure for check {check_name!r} "
                f"on ticket {ticket_id!r}"
            )
        check_failure_id = ev.id
    else:
        raise ValueError("supply either check_failure_id or (ticket_id, check_name)")

    if ev.waived:
        raise ThreadError(f"check_failure {check_failure_id!r} is already waived")

    # Severity drives the token. ``check_severity`` is Optional on the
    # model though the producers (check_gate, check_runner) always set
    # it. If a malformed event slips through, surface it explicitly —
    # a blind ``f"check_failure:{None}"`` would otherwise report the
    # role as lacking ``'check_failure:None'``, which reads as a
    # capability-list problem instead of the data-quality problem it
    # actually is.
    if ev.check_severity is None:
        raise ThreadError(
            f"check_failure {check_failure_id!r} is missing check_severity "
            f"(malformed event) — cannot route to a waive token"
        )
    token = f"check_failure:{ev.check_severity}"
    _require_waive_token(
        token,
        role=sender,
        can_waive=can_waive,
        subject=f"check failures of severity {ev.check_severity!r}",
    )

    w = Waiver(
        ticket_id=ev.ticket_id,
        author=sender,
        check_failure_id=check_failure_id,
        justification=justification,
    )
    wid = await threads.post(w)
    await threads.update(check_failure_id, {"waived": True})

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_check_failure_waived",
                "ticket_id": ev.ticket_id,
                "check_failure_id": check_failure_id,
                "check_name": ev.check_name,
                "waiver_id": wid,
                "waived_by": sender,
                "justification": justification,
            },
            topic=f"tickets.{ev.ticket_id}",
        )
    )
    return {
        "waiver_id": wid,
        "check_failure_id": check_failure_id,
        "check_name": ev.check_name,
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

    Phase 5 Task K enforcement: when ``phase_escalation_targets`` is a
    populated frozenset, ``target`` must be in it or be one of the
    always-allowed human escape hatches (``human`` / ``any_human``).
    ``None``/empty keeps the old permissive behavior so phases that
    don't declare the field still post cleanly. The separate
    ``valid_roles`` check below is still warn-only — it's a sanity
    hint for operators running without phase declarations, not a
    gate (``jig validate`` catches unknown roles at catalog-load).

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

    _require_target_in_phase_allowlist(
        target,
        allowlist=phase_escalation_targets,
        field_name="escalation_targets",
    )

    if (
        not phase_escalation_targets
        and valid_roles
        and target not in _HUMAN_TARGET_ESCAPE_HATCH
        and target not in valid_roles
    ):
        _logger.warning(
            "escalation target %r is not a known role or human escape hatch "
            "(ticket=%s, sender=%s); allowing because phase_escalation_targets "
            "isn't declared",
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


def _route_uncertain(details: str, valid_roles: frozenset[str]) -> str | None:
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
        for m in re.finditer(rf"\b{re.escape(role.lower())}\b", lowered):
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


# ---- thread_handoff -------------------------------------------------------


async def _resolve_phase_evaluator(
    *,
    workflow: WorkflowConfig,
    phase_name: str,
    threads: ThreadStore,
    ticket_id: str,
) -> ResolvedEvaluator | None:
    """Return the evaluator(s) authorized to accept/reject the Handoff
    for ``phase_name``.

    Resolution order:

    1. The phase's explicit ``evaluator:`` field, resolved via
       ``jig.evaluator_resolver.resolve_evaluator``. The five spec
       types from doc 10 §Evaluators are supported.
    2. The next phase's ``role`` — the natural-sequence default,
       wrapped as a ``kind='role'`` ``ResolvedEvaluator``.
    3. ``None`` — no evaluator can be determined (terminal phase,
       unresolvable ``previous_phase_role``). Caller warns and
       allows; Phase 5 Task D tightens this for required-checks
       phases.
    """
    for idx, phase in enumerate(workflow.phases):
        if phase.name != phase_name:
            continue
        if phase.evaluator is not None:
            history = [
                e for e in await threads.for_ticket(ticket_id) if isinstance(e, Handoff)
            ]
            resolved = resolve_evaluator(
                spec=phase.evaluator,
                workflow=workflow,
                phase_name=phase_name,
                handoff_history=history,
            )
            if resolved is not None:
                return resolved
        nxt = natural_next_role(workflow, phase_name)
        if nxt is not None:
            return ResolvedEvaluator(kind="role", actors=[nxt])
        return None
    return None


async def handle_thread_handoff(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    checkpoints: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Create a Handoff entry for a phase closing.

    Always blocking until accepted or rejected. ``deferred_items`` are
    merged from two sources:

    * **Explicit** — values in ``args["deferred_items"]`` (str, dict,
      or ``DeferredItem``). Useful when the caller wants to override
      or supplement the checkpoint-derived list.
    * **Checkpoint-derived** — when ``checkpoints`` is provided, open
      ``DeferredItem`` entries from the phase's checkpoint history are
      appended (Phase 4 Task G). Explicit entries come first so the
      caller's order wins.

    A pre-handoff checkpoint (``trigger="auto_pre_handoff"``) is also
    written when ``checkpoints`` is provided, snapshotting the phase's
    final state for replay/audit.

    Required args: ``ticket_id``, ``phase``, ``outputs``.
    Optional: ``summary`` (default ``""``), ``deferred_items``
    (default ``[]``).
    """
    ticket_id = args["ticket_id"]
    phase = args["phase"]
    outputs = args.get("outputs", [])
    summary = args.get("summary", "")
    raw_deferred = args.get("deferred_items", [])

    if not phase.strip():
        raise ValueError("phase is required")
    if not isinstance(outputs, list):
        raise ValueError("outputs must be a list")

    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    deferred: list[DeferredItem] = []
    for item in raw_deferred:
        if isinstance(item, DeferredItem):
            deferred.append(item)
        elif isinstance(item, str):
            if item.strip():
                deferred.append(DeferredItem(item=item))
        elif isinstance(item, dict):
            deferred.append(DeferredItem.model_validate(item))
        else:
            raise ValueError(
                f"deferred_items entry must be str, dict, or DeferredItem; "
                f"got {type(item).__name__}"
            )

    if checkpoints is not None:
        from_checkpoints = await checkpoints.deferred_items_open(ticket_id, phase)
        # Dedupe: skip items whose (item, reason) pair is already in the
        # explicit list. Evaluator sees each issue once.
        seen = {(d.item, d.reason) for d in deferred}
        for cd in from_checkpoints:
            key = (cd.item, cd.reason)
            if key in seen:
                continue
            deferred.append(cd)
            seen.add(key)

        await record_auto_pre_handoff_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name=phase,
            author=sender,
            outputs=list(outputs),
            summary=summary,
            deferred=deferred,
        )

    h = Handoff(
        ticket_id=ticket_id,
        author=sender,
        phase=phase,
        outputs=list(outputs),
        summary=summary,
        deferred_items=deferred,
    )
    hid = await threads.post(h)

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_handoff_posted",
                "ticket_id": ticket_id,
                "handoff_id": hid,
                "author": sender,
                "phase": phase,
                "outputs": list(outputs),
                "summary": summary,
                "deferred_items": [d.model_dump() for d in deferred],
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {
        "handoff_id": hid,
        "phase": phase,
        "acceptance_state": "pending",
        "blocking": True,
    }


async def _close_handoff(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    project_path: Path,
    handoff_id: str,
    accepted: bool,
    rejection_reason: str | None,
    checkpoints: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Shared guard for accept/reject — evaluator check + state write."""
    h = await threads.get(handoff_id)
    if h is None:
        raise KeyError(f"handoff {handoff_id!r} not found")
    if not isinstance(h, Handoff):
        raise ThreadError(f"entry {handoff_id!r} is a {h.kind!r}, not a handoff")
    if h.is_resolved():
        raise ThreadError(f"handoff {handoff_id!r} is already {h.acceptance_state!r}")

    ticket = await tickets.get(h.ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {h.ticket_id} not found")

    # The evaluator guard is load-bearing (doc 10 §Evaluators): if we
    # can't resolve an evaluator, we can't verify the caller has
    # authority to close this handoff. Fail loud in both branches
    # rather than allow permissively — a missing workflow is config
    # corruption, not an excuse to let any agent flip a handoff.
    try:
        workflow = load_workflow(project_path, ticket.workflow)
    except FileNotFoundError as exc:
        raise ThreadError(
            f"cannot determine evaluator for handoff {handoff_id!r}: "
            f"workflow {ticket.workflow!r} not found for ticket "
            f"{h.ticket_id!r}; handoff cannot be "
            f"{'accepted' if accepted else 'rejected'}"
        ) from exc

    resolved: ResolvedEvaluator | None = await _resolve_phase_evaluator(
        workflow=workflow,
        phase_name=h.phase,
        threads=threads,
        ticket_id=h.ticket_id,
    )

    if resolved is None:
        raise ThreadError(
            f"cannot determine evaluator for phase {h.phase!r} in "
            f"workflow {ticket.workflow!r}; handoff {handoff_id!r} "
            f"cannot be {'accepted' if accepted else 'rejected'}"
        )
    if resolved.kind == "automated":
        # ``automated_only`` phases accept via the orchestrator's
        # check-gating path (Task D); a manual accept/reject from a
        # named sender is disallowed so agents can't side-step the
        # automated gate by poking this tool.
        raise ThreadError(
            f"phase {h.phase!r} has evaluator=automated_only; "
            f"manual {'accept' if accepted else 'reject'} by "
            f"{sender!r} not permitted (check results gate)"
        )
    elif sender not in resolved.actors:
        raise ThreadError(
            f"only the phase evaluator ({resolved.actors!r}) can "
            f"{'accept' if accepted else 'reject'} this handoff "
            f"(sender={sender!r}, phase={h.phase!r})"
        )
    # Structural identity guard per doc 10 §Evaluators: the actor who
    # authored the Handoff cannot also evaluate it. Catches the case
    # where ``specific_role`` names the completing phase's role, or
    # ``previous_phase_role`` happens to resolve to the same identity.
    if sender == h.author:
        raise ThreadError(
            f"evaluator cannot be the completing actor "
            f"(sender={sender!r}, handoff_author={h.author!r}, "
            f"phase={h.phase!r})"
        )

    if accepted:
        changes: dict[str, Any] = {
            "acceptance_state": "accepted",
            "accepted_by": sender,
        }
        bus_kind = "thread_handoff_accepted"
    else:
        changes = {
            "acceptance_state": "rejected",
            "rejection_reason": rejection_reason or "",
        }
        bus_kind = "thread_handoff_rejected"
    await threads.update(handoff_id, changes)

    if accepted and checkpoints is not None:
        # Phase-boundary pruning per doc 09 §Phase boundaries. The
        # just-accepted phase's checkpoints flip to historical so
        # default queries for the next phase ignore them; the records
        # remain on disk for audit.
        await checkpoints.mark_phase_historical(h.ticket_id, h.phase)

    payload: dict[str, Any] = {
        "kind": bus_kind,
        "ticket_id": h.ticket_id,
        "handoff_id": handoff_id,
        "phase": h.phase,
    }
    if accepted:
        payload["accepted_by"] = sender
    else:
        payload["rejection_reason"] = rejection_reason or ""
        payload["rejected_by"] = sender

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload=payload,
            topic=f"tickets.{h.ticket_id}",
        )
    )
    return {
        "handoff_id": handoff_id,
        "phase": h.phase,
        "acceptance_state": changes["acceptance_state"],
    }


async def handle_thread_accept_handoff(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
    checkpoints: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Evaluator-only accept. Publishes ``thread_handoff_accepted``
    on the ticket topic so the orchestrator can advance the workflow.
    When ``checkpoints`` is provided, the accepted phase's checkpoints
    are marked historical (doc 09 §Phase boundaries).

    Required args: ``handoff_id``.
    """
    return await _close_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender=sender,
        project_path=project_path,
        handoff_id=args["handoff_id"],
        accepted=True,
        rejection_reason=None,
        checkpoints=checkpoints,
    )


async def handle_thread_reject_handoff(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
    checkpoints: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Evaluator-only reject. Publishes ``thread_handoff_rejected``
    on the ticket topic so the orchestrator can follow the phase's
    on-failure edge. Rejection does not prune checkpoints — the retry
    attempt resumes from the same history.

    Required args: ``handoff_id``, ``reason``.
    """
    reason = args.get("reason", "")
    if not reason.strip():
        raise ValueError("reason is required")
    return await _close_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender=sender,
        project_path=project_path,
        handoff_id=args["handoff_id"],
        accepted=False,
        rejection_reason=reason,
        checkpoints=checkpoints,
    )


__all__ = [
    "ThreadError",
    "handle_thread_accept_handoff",
    "handle_thread_accept_resolution",
    "handle_thread_answer",
    "handle_thread_ask",
    "handle_thread_decide",
    "handle_thread_escalate",
    "handle_thread_handoff",
    "handle_thread_note",
    "handle_thread_object",
    "handle_thread_reject_handoff",
    "handle_thread_resolve_objection",
    "handle_thread_resolve_question",
    "handle_thread_uncertain",
    "handle_thread_waive",
    "handle_thread_waive_check",
]
