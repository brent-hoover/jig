"""MCP server factory for agent ticket tools."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig import (
    checkpoint_mcp,
    init_mcp,
    po_l0_mcp,
    po_l3_mcp,
    thread_mcp,
    ticket_mcp,
)
from jig.logging_setup import (
    _agent_id_var,
    _phase_var,
    _role_var,
    _ticket_id_var,
)
from jig.models import RoleConfig
from jig.store import Message, MessageBus, MessageType
from jig.store.checkpoints import CheckpointStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Question, SystemEvent
from jig.ticket import TicketStatus


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _wrap_with_context(
    handler: ToolHandler,
    *,
    ticket_id: str | None,
    phase: str | None,
    role: str | None,
    agent_id: str | None,
) -> ToolHandler:
    """Wrap an MCP tool handler so each call runs with the given
    correlation context. The MCP SDK invokes each tool in a fresh
    asyncio task that does NOT inherit our per-ticket contextvars,
    so handlers must set them explicitly at the call boundary.
    """

    async def wrapper(args: dict[str, Any]) -> dict[str, Any]:
        t_tid = _ticket_id_var.set(ticket_id)
        t_phase = _phase_var.set(phase)
        t_role = _role_var.set(role)
        t_agent = _agent_id_var.set(agent_id)
        try:
            return await handler(args)
        finally:
            _ticket_id_var.reset(t_tid)
            _phase_var.reset(t_phase)
            _role_var.reset(t_role)
            _agent_id_var.reset(t_agent)

    return wrapper


def create_agent_mcp_server(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    agent_role: str,
    agent_cfg: RoleConfig,
    worktree_path: Path,
    project_path: Path,
    valid_roles: frozenset[str] = frozenset(),
    package_manager: str = "",
    checkpoints: CheckpointStore | None = None,
    phase_name: str = "",
    can_waive: frozenset[str] = frozenset(),
    phase_questions_to: frozenset[str] = frozenset(),
    phase_escalation_targets: frozenset[str] = frozenset(),
    ticket_id: str = "",
):
    """Create a Jig MCP server for a worker agent.

    ``threads`` is the Phase 4 typed thread-entry store and is the
    sole store used by every tool handler here. ``thread_ask`` /
    ``thread_answer`` / ``thread_resolve_question`` write typed entries
    directly; legacy ticket tools (``ask_question``, ``comment_on_ticket``
    etc.) now translate to ThreadEntry types on the way in.
    """

    # Allowed assignees: known roles + orchestrator + user
    _allowed_assignees = valid_roles | {"orchestrator", "user"}

    def _check_assignee(assignee: str | None) -> None:
        if assignee and _allowed_assignees and assignee not in _allowed_assignees:
            raise ValueError(
                f"Unknown role {assignee!r}. Valid roles: {sorted(valid_roles)}"
            )

    @tool(
        "create_ticket",
        "Create a new ticket. Use depends_on to list ticket IDs that must be resolved before this ticket can start. "
        "Set workflow to 'project' for tickets that need PM planning breakdown.",
        {
            "work_type": str,
            "size": str,
            "title": str,
            "description": str,
            "assignee": str,
            "parent_id": str,
            "depends_on": list,
            "workflow": str,
            "labels": list,
        },
    )
    async def create_ticket(args):
        _check_assignee(args.get("assignee"))
        ticket_id = await ticket_mcp.handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender=agent_role,
            args=args,
            project_path=project_path,
        )
        return {"content": [{"type": "text", "text": ticket_id}]}

    @tool(
        "read_ticket",
        "Read a ticket by ID",
        {"ticket_id": str},
    )
    async def read_ticket(args):
        t = await ticket_mcp.handle_read_ticket(
            tickets=tickets, ticket_id=args["ticket_id"]
        )
        return {"content": [{"type": "text", "text": t.model_dump_json()}]}

    @tool(
        "update_ticket",
        "Update fields on an existing ticket",
        {
            "ticket_id": str,
            "status": str,
            "description": str,
            "assignee": str,
            "labels": list,
        },
    )
    async def update_ticket(args):
        _check_assignee(args.get("assignee"))
        updated = await ticket_mcp.handle_update_ticket(
            tickets=tickets, threads=threads, bus=bus, sender=agent_role, args=args
        )
        return {"content": [{"type": "text", "text": updated.model_dump_json()}]}

    @tool(
        "comment_on_ticket",
        "Post a comment or decision on a ticket",
        {"ticket_id": str, "content": str, "kind": str},
    )
    async def comment_on_ticket(args):
        cid = await ticket_mcp.handle_comment_on_ticket(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            sender_cfg=agent_cfg,
            args=args,
        )
        return {"content": [{"type": "text", "text": cid}]}

    @tool(
        "ask_question",
        "Ask the operator one or more questions. Pass `questions` as a "
        "list of strings (one entry per question). Posts blocking Question "
        "entries on the ticket and flips it to needs_info; the orchestrator "
        "resumes you once the operator answers. Use this instead of "
        "creating question tickets or manually setting needs_info.",
        {"ticket_id": str, "questions": list},
    )
    async def ask_question(args):
        # Operator-pause UX: post blocking Question entries targeted at
        # "any_human", flip the ticket to needs_info, and let the TUI
        # drive the answer flow via the ws_server `answer_questions`
        # command. Typed agent-to-agent Q&A goes through thread_ask.
        #
        # Schema accepts only `questions` (a list). An earlier draft
        # also exposed a singular `question: str`, which Claude
        # consistently filled with junk like "placeholder" alongside
        # the real list. Dropping it from the schema removes the
        # affordance; we still dedupe within the list because models
        # sometimes repeat the same prompt twice.
        ticket_id = args["ticket_id"]
        raw = args.get("questions") or []
        if not isinstance(raw, list):
            raise ValueError("`questions` must be a list of strings")
        seen: set[str] = set()
        questions: list[str] = []
        for q in raw:
            if not isinstance(q, str):
                raise ValueError("each question must be a string")
            key = q.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            questions.append(q)
        if not questions:
            raise ValueError("at least one question is required")

        ticket = await tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"ticket {ticket_id} not found")

        comment_ids: list[str] = []
        for q in questions:
            cid = await threads.post(
                Question(
                    ticket_id=ticket_id,
                    author=agent_role,
                    target="any_human",
                    question=q,
                    blocking=True,
                )
            )
            comment_ids.append(cid)
            await bus.publish(
                Message(
                    sender=agent_role,
                    to=ticket.assignee or "broadcast",
                    type=MessageType.CONTEXT_UPDATE,
                    payload={
                        "kind": "comment_posted",
                        "ticket_id": ticket_id,
                        "comment_id": cid,
                        "author": agent_role,
                        "content": q,
                        "comment_kind": "question",
                    },
                    topic=f"tickets.{ticket_id}",
                )
            )

        before_status = ticket.status
        updated = await tickets.update(ticket_id, status=TicketStatus.NEEDS_INFO)
        if before_status != TicketStatus.NEEDS_INFO:
            await threads.post(
                SystemEvent(
                    ticket_id=ticket_id,
                    author=agent_role,
                    event_type="status_change",
                    content=f"status {before_status.value} -> needs_info",
                )
            )
        await bus.publish(
            Message(
                sender=agent_role,
                to=updated.assignee or "broadcast",
                type=MessageType.CONTEXT_UPDATE,
                payload={
                    "kind": "ticket_updated",
                    "ticket_id": ticket_id,
                    "status": "needs_info",
                },
                topic=f"tickets.{ticket_id}",
            )
        )

        result = {"comment_ids": comment_ids, "status": "needs_info"}
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_ask",
        "Post a targeted question on a ticket (typed doc-08 thread entry). "
        "'target' is a role name, actor name, or 'any_human'. Set blocking=true "
        "to gate the current phase on a reply — default non-blocking. Unlike "
        "ask_question, this does NOT pause the ticket via status changes; "
        "use it for in-band agent-to-agent Q&A.",
        {"ticket_id": str, "target": str, "question": str, "blocking": bool},
    )
    async def thread_ask(args):
        result = await thread_mcp.handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            phase_questions_to=phase_questions_to or None,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_answer",
        "Post an answer to a thread question. Does NOT close the question — the "
        "asker retains the right to resolve (doc 08 resolution asymmetry). "
        "Fails if the question is already resolved.",
        {"question_id": str, "text": str},
    )
    async def thread_answer(args):
        result = await thread_mcp.handle_thread_answer(
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_resolve_question",
        "Close one of your own thread questions. Only the asker can close "
        "(refuses if sender != question.author). Optional accepted_answer_id "
        "points at the answer that satisfied the question.",
        {
            "question_id": str,
            "accepted_answer_id": str,
            "reason": str,
        },
    )
    async def thread_resolve_question(args):
        result = await thread_mcp.handle_thread_resolve_question(
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_object",
        "Raise an objection against an artifact (file path, PR link, prior "
        "thread entry id, etc). Objections are always blocking until you "
        "accept a resolution (thread_accept_resolution) or an authorized "
        "actor waives them (thread_waive).",
        {"ticket_id": str, "target_artifact": str, "text": str},
    )
    async def thread_object(args):
        result = await thread_mcp.handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_resolve_objection",
        "Post a resolution addressing an objection ('here's how I fixed it'). "
        "Does NOT close the objection — only the objector can accept via "
        "thread_accept_resolution. Fails if the objection is already resolved.",
        {"objection_id": str, "text": str},
    )
    async def thread_resolve_objection(args):
        result = await thread_mcp.handle_thread_resolve_objection(
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_accept_resolution",
        "Close your own objection after reviewing a resolution. Only the "
        "original objector can accept (refuses if sender != objection.author).",
        {"objection_id": str},
    )
    async def thread_accept_resolution(args):
        result = await thread_mcp.handle_thread_accept_resolution(
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_waive",
        "Override an objection with explicit justification. Authorization "
        "is enforced against your role's compiled "
        "capabilities.waivers.can_waive — if the token 'objection' is not "
        "present, this fails. The waiver and the original objection both "
        "stay in the thread as audit trail.",
        {"objection_id": str, "justification": str},
    )
    async def thread_waive(args):
        result = await thread_mcp.handle_thread_waive(
            threads=threads,
            bus=bus,
            sender=agent_role,
            can_waive=can_waive,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_waive_check",
        "Waive a failing check with justification. Pass either "
        "check_failure_id (targets a specific check_failure SystemEvent) "
        "or ticket_id+check_name (resolves to the most recent unwaived "
        "failure for that check). Authorization is enforced against "
        "your role's compiled capabilities.waivers.can_waive — the "
        "required token is 'check_failure:<severity>' where severity "
        "is 'required' or 'warning' — taken from the failure event's "
        "severity field. The waiver and the underlying check_failure "
        "event both remain in the thread; the gate stops treating the "
        "failure as blocking.",
        {
            "justification": str,
            "check_failure_id": str,
            "ticket_id": str,
            "check_name": str,
        },
    )
    async def thread_waive_check(args):
        # Only forward the keys the caller actually set — the tool
        # schema lists all four for discoverability, but the handler's
        # branch logic requires either check_failure_id OR both
        # ticket_id and check_name.
        forwarded = {
            k: v
            for k, v in args.items()
            if k
            in {
                "justification",
                "check_failure_id",
                "ticket_id",
                "check_name",
            }
            and v
        }
        result = await thread_mcp.handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            can_waive=can_waive,
            args=forwarded,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_decide",
        "Record a non-obvious Decision with rationale. Auto-resolved. "
        "Also writes a standalone decision record under .jig/decisions/ "
        "per doc 17 so architectural choices survive outside the thread.",
        {"ticket_id": str, "decision": str, "rationale": str},
    )
    async def thread_decide(args):
        result = await thread_mcp.handle_thread_decide(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            project_path=project_path,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_note",
        "Post a freeform observation on a ticket. Auto-resolved; never "
        "blocking. Use for context drops that don't fit a Question, "
        "Objection, or Decision.",
        {"ticket_id": str, "text": str},
    )
    async def thread_note(args):
        result = await thread_mcp.handle_thread_note(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_escalate",
        "Raise a 'beyond my scope' Escalation. Always blocking until a "
        "resolver acts. Target defaults to 'human'; pass a role name to "
        "route to a specific actor. Reason is a short structured code "
        "(e.g. 'needs_human_judgment'), details is prose.",
        {
            "ticket_id": str,
            "reason": str,
            "details": str,
            "target": str,
        },
    )
    async def thread_escalate(args):
        result = await thread_mcp.handle_thread_escalate(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            valid_roles=valid_roles,
            phase_escalation_targets=phase_escalation_targets or None,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_uncertain",
        "Signal 'I don't know who should handle this' — the orchestrator "
        "routes. Phase 4 routing is a simple rule: if details mention a "
        "known role name, the system reshapes this as a Question to that "
        "role; otherwise it escalates to human. Prefer thread_ask when you "
        "already know the target.",
        {"ticket_id": str, "details": str},
    )
    async def thread_uncertain(args):
        result = await thread_mcp.handle_thread_uncertain(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            valid_roles=valid_roles,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_handoff",
        "Close the current phase by posting a Handoff entry. Always "
        "blocking until the phase evaluator accepts or rejects. outputs "
        "is a list of artifact refs; deferred_items carries checkpoint-"
        "captured work to review at handoff time.",
        {
            "ticket_id": str,
            "phase": str,
            "outputs": list,
            "summary": str,
            "deferred_items": list,
        },
    )
    async def thread_handoff(args):
        result = await thread_mcp.handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            checkpoints=checkpoints,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_accept_handoff",
        "Evaluator-only accept. Advances the workflow by publishing "
        "thread_handoff_accepted on the ticket topic. Fails if the "
        "sender isn't the phase's evaluator.",
        {"handoff_id": str},
    )
    async def thread_accept_handoff(args):
        result = await thread_mcp.handle_thread_accept_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            project_path=project_path,
            checkpoints=checkpoints,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "thread_reject_handoff",
        "Evaluator-only reject. Publishes thread_handoff_rejected so the "
        "orchestrator follows the phase's on-failure edge. Reason is "
        "required and surfaces in the thread + on the bus payload.",
        {"handoff_id": str, "reason": str},
    )
    async def thread_reject_handoff(args):
        result = await thread_mcp.handle_thread_reject_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            project_path=project_path,
            checkpoints=checkpoints,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "checkpoint_milestone",
        "Record a progress checkpoint: where you are, what's done, what's "
        "next, and anything ruled out. Use at natural milestones (chunk "
        "complete, approach chosen, about to context-switch) so retries "
        "and resumptions have position notes. Not blocking.",
        {
            "ticket_id": str,
            "description": str,
            "position": str,
            "plan": str,
            "completed": list,
            "ruled_out": list,
            "open_questions": list,
        },
    )
    async def checkpoint_milestone(args):
        if checkpoints is None:
            raise RuntimeError(
                "checkpoint store not wired — server built without checkpoints"
            )
        result = await checkpoint_mcp.handle_checkpoint_milestone(
            tickets=tickets,
            checkpoints=checkpoints,
            sender=agent_role,
            phase_name=phase_name,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "checkpoint_decision",
        "Mirror a thread Decision into the checkpoint channel so "
        "position notes for this phase reference it. Pass the "
        "decision_id returned by thread_decide.",
        {"decision_id": str, "rationale": str},
    )
    async def checkpoint_decision(args):
        if checkpoints is None:
            raise RuntimeError(
                "checkpoint store not wired — server built without checkpoints"
            )
        result = await checkpoint_mcp.handle_checkpoint_decision(
            tickets=tickets,
            threads=threads,
            checkpoints=checkpoints,
            sender=agent_role,
            phase_name=phase_name,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "checkpoint_deferred",
        "Record an item you're deliberately deferring: not doing now "
        "but the evaluator should see it at handoff time. "
        "Non-blocking — surfaces on the next Handoff's deferred_items.",
        {"ticket_id": str, "item": str, "reason": str},
    )
    async def checkpoint_deferred(args):
        if checkpoints is None:
            raise RuntimeError(
                "checkpoint store not wired — server built without checkpoints"
            )
        result = await checkpoint_mcp.handle_checkpoint_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            sender=agent_role,
            phase_name=phase_name,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "checkpoint_promote_deferred",
        "Promote a deferred item from this ticket's handoff into its "
        "own child ticket. Use during handoff review when a deferred "
        "item deserves its own tracking rather than staying as a "
        "follow-up note. Creates a child ticket with parent_id set to "
        "the current ticket. Idempotent — re-calling with the same "
        "deferred_item_id returns the existing child id.",
        {
            "ticket_id": str,
            "deferred_item_id": str,
            "title": str,
            "work_type": str,
            "description": str,
            "size": str,
            "assignee": str,
            "labels": list,
        },
    )
    async def checkpoint_promote_deferred(args):
        if checkpoints is None:
            raise RuntimeError(
                "checkpoint store not wired — server built without checkpoints"
            )
        result = await checkpoint_mcp.handle_checkpoint_promote_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            bus=bus,
            sender=agent_role,
            phase_name=phase_name,
            args=args,
            project_path=project_path,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "list_tickets",
        "List tickets with optional filters",
        {"work_type": str, "status": str, "assignee": str, "parent_id": str},
    )
    async def list_tickets(args):
        results = await ticket_mcp.handle_list_tickets(tickets=tickets, args=args)
        text = "\n".join(r.model_dump_json() for r in results)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_comments",
        "Read comments on a ticket",
        {"ticket_id": str, "kind": str},
    )
    async def read_comments(args):
        results = await ticket_mcp.handle_read_comments(
            threads=threads, ticket_id=args["ticket_id"], kind=args.get("kind")
        )
        text = "\n".join(r.model_dump_json() for r in results)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "commit_progress",
        "Commit current worktree progress and record it on a ticket. "
        "The message should describe WHAT changed (e.g. 'add CRUD endpoints for todos', "
        "'fix off-by-one in pagination logic'), not just repeat the ticket title.",
        {"ticket_id": str, "message": str},
    )
    async def commit_progress(args):
        result = await ticket_mcp.handle_commit_progress(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            worktree_path=worktree_path,
            args=args,
            checkpoints=checkpoints,
            phase_name=phase_name,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    @tool(
        "record_learning",
        "Record a learning or lesson learned for your role",
        {"content": str},
    )
    async def record_learning(args):
        text = await ticket_mcp.handle_record_learning(
            memory=memory, role=agent_role, args=args
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "request_context",
        "Read a file from the worktree to get additional context",
        {"path": str},
    )
    async def request_context(args):
        text = await ticket_mcp.handle_request_context(
            worktree_path=worktree_path, args=args
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "add_dependency",
        "Add a package dependency to the project using the configured package manager. "
        "Use this instead of running install commands directly.",
        {"packages": list, "dev": bool},
    )
    async def add_dependency(args):
        result = await ticket_mcp.handle_add_dependency(
            worktree_path=worktree_path,
            package_manager=package_manager,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    all_tools = [
        create_ticket,
        read_ticket,
        update_ticket,
        comment_on_ticket,
        ask_question,
        thread_ask,
        thread_answer,
        thread_resolve_question,
        thread_object,
        thread_resolve_objection,
        thread_accept_resolution,
        thread_waive,
        thread_waive_check,
        thread_decide,
        thread_note,
        thread_escalate,
        thread_uncertain,
        thread_handoff,
        thread_accept_handoff,
        thread_reject_handoff,
        list_tickets,
        read_comments,
        commit_progress,
        record_learning,
        request_context,
    ]
    if checkpoints is not None:
        all_tools.extend(
            [
                checkpoint_milestone,
                checkpoint_decision,
                checkpoint_deferred,
                checkpoint_promote_deferred,
            ]
        )
    if package_manager:
        all_tools.append(add_dependency)

    # Init-workflow tools (brief / spec / architecture). Each is gated
    # on ``agent_cfg.allowed_tools`` so a role only sees the tools it
    # has been granted. Handlers live in ``jig.init_mcp``; wrappers
    # here translate MCP args into the handler's keyword arguments.

    if "brief_list_sections" in agent_cfg.allowed_tools:

        @tool(
            "brief_list_sections",
            "List markdown sections (H2 headings) in the project brief.",
            {},
        )
        async def brief_list_sections(args):
            sections = await init_mcp.handle_brief_list_sections(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(sections)}]}

        all_tools.append(brief_list_sections)

    if "brief_get_section" in agent_cfg.allowed_tools:

        @tool(
            "brief_get_section",
            "Read a single section of the project brief by H2 heading name.",
            {"name": str},
        )
        async def brief_get_section(args):
            text = await init_mcp.handle_brief_get_section(
                project_path=project_path,
                name=args["name"],
            )
            return {"content": [{"type": "text", "text": text}]}

        all_tools.append(brief_get_section)

    if "brief_set_section" in agent_cfg.allowed_tools:

        @tool(
            "brief_set_section",
            "Create or replace a section of the project brief.",
            {"name": str, "markdown": str},
        )
        async def brief_set_section(args):
            await init_mcp.handle_brief_set_section(
                project_path=project_path,
                name=args["name"],
                markdown=args["markdown"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(brief_set_section)

    if "po_finish_brief" in agent_cfg.allowed_tools:

        @tool(
            "po_finish_brief",
            "Signal the brief is complete and hand off to the spec-generator.",
            {"summary": str},
        )
        async def po_finish_brief(args):
            entry_id = await init_mcp.handle_po_finish_brief(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                summary=args["summary"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(po_finish_brief)

    if "l0_finalize" in agent_cfg.allowed_tools:

        @tool(
            "l0_finalize",
            "Capture the L0 pitch + problem + audience + product-level "
            "non-goals and finalize the project. Writes both "
            ".jig/spec/project.md (markdown form) and "
            ".jig/spec/project.structured.yaml (Pydantic dump). "
            "Hands off to the L1 PO. Call this exactly once when the "
            "operator has confirmed all four fields.",
            {
                "name": str,
                "pitch": str,
                "problem": str,
                "audience": str,
                "non_goals": list,
            },
        )
        async def l0_finalize(args):
            entry_id = await po_l0_mcp.handle_l0_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                name=args["name"],
                pitch=args["pitch"],
                problem=args["problem"],
                audience=args["audience"],
                non_goals=args.get("non_goals", []),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(l0_finalize)

    if "l3_finalize" in agent_cfg.allowed_tools:

        @tool(
            "l3_finalize",
            "Commit the L3 brief for one suite. Writes both "
            ".jig/spec/suites/<suite_id>/brief.md (markdown form) and "
            ".jig/spec/suites/<suite_id>/spec.structured.yaml "
            "(structured projection). The ``capabilities`` list contains "
            "dicts shaped like the StructuredSpec.Capability schema "
            "(id, title, state, summary, user_story?, behaviors?, "
            "acceptance_criteria?, excluded?, open_questions?, aliases?); "
            "every id MUST appear in suites.yaml under this suite. "
            "Optional ``non_goals`` is a list of {id, text, rationale?}. "
            "Hands off to SA. Call exactly once when the brief is ready.",
            {
                "suite_id": str,
                "intro": str,
                "capabilities": list,
                "non_goals": list,
            },
        )
        async def l3_finalize(args):
            entry_id = await po_l3_mcp.handle_l3_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                suite_id=args["suite_id"],
                intro=args["intro"],
                capabilities=args.get("capabilities", []),
                non_goals=args.get("non_goals", []),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(l3_finalize)

    if "spec_publish" in agent_cfg.allowed_tools:

        @tool(
            "spec_publish",
            "Write the structured spec YAML and emit spec_generated.",
            {"yaml_content": str, "advisory_notes": list},
        )
        async def spec_publish(args):
            await init_mcp.handle_spec_publish(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                yaml_content=args["yaml_content"],
                advisory_notes=args.get("advisory_notes", []),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(spec_publish)

    if "spec_report_gaps" in agent_cfg.allowed_tools:
        from jig.spec_generator import Gap as _Gap

        @tool(
            "spec_report_gaps",
            "Report blocking and advisory gaps found while validating the brief.",
            {"gaps": list},
        )
        async def spec_report_gaps(args):
            gaps = [_Gap.model_validate(g) for g in args["gaps"]]
            await init_mcp.handle_spec_report_gaps(
                tickets=tickets,
                threads=threads,
                bus=bus,
                gaps=gaps,
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(spec_report_gaps)

    if "spec_get_field" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_field",
            "Read a field from the structured spec by dotted path.",
            {"path": str},
        )
        async def spec_get_field(args):
            value = await init_mcp.handle_spec_get_field(
                project_path=project_path,
                path=args["path"],
            )
            return {"content": [{"type": "text", "text": json.dumps(value)}]}

        all_tools.append(spec_get_field)

    if "spec_list_fields" in agent_cfg.allowed_tools:

        @tool(
            "spec_list_fields",
            "List all dotted field paths defined in the structured spec.",
            {},
        )
        async def spec_list_fields(args):
            fields = await init_mcp.handle_spec_list_fields(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(fields)}]}

        all_tools.append(spec_list_fields)

    if "spec_list_capabilities" in agent_cfg.allowed_tools:

        @tool(
            "spec_list_capabilities",
            "List capabilities in the project spec. Optional `state` filter "
            "('backlog', 'planned', 'in_progress', 'built', 'archived'). "
            "Returns id, title, state for each — lightweight summary; "
            "use `spec_get_capability` for full content.",
            {"state": str},
        )
        async def spec_list_capabilities(args):
            out = await init_mcp.handle_spec_list_capabilities(
                project_path=project_path,
                state=args.get("state"),
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_list_capabilities)

    if "spec_get_capability" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_capability",
            "Get a full Capability by id (or alias). Returns id, title, "
            "state, summary, user_story, behaviors (each with description, "
            "examples, acceptance_criteria), capability-level "
            "acceptance_criteria, excluded, open_questions, tickets, aliases.",
            {"id": str},
        )
        async def spec_get_capability(args):
            out = await init_mcp.handle_spec_get_capability(
                project_path=project_path, id=args["id"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_get_capability)

    if "spec_get_behavior" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_behavior",
            "Get a full Behavior by capability_id + behavior_id.",
            {"capability_id": str, "behavior_id": str},
        )
        async def spec_get_behavior(args):
            out = await init_mcp.handle_spec_get_behavior(
                project_path=project_path,
                capability_id=args["capability_id"],
                behavior_id=args["behavior_id"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_get_behavior)

    if "spec_list_non_goals" in agent_cfg.allowed_tools:

        @tool(
            "spec_list_non_goals",
            "List all non-goals from the project spec. Returns id, text, "
            "rationale for each.",
            {},
        )
        async def spec_list_non_goals(args):
            out = await init_mcp.handle_spec_list_non_goals(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_list_non_goals)

    if "spec_get_non_goal" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_non_goal",
            "Get a full NonGoal by id (or alias).",
            {"id": str},
        )
        async def spec_get_non_goal(args):
            out = await init_mcp.handle_spec_get_non_goal(
                project_path=project_path, id=args["id"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_get_non_goal)

    if "spec_resolve_uri" in agent_cfg.allowed_tools:

        @tool(
            "spec_resolve_uri",
            "Resolve a project://spec/... URI to structured data. Supports "
            "capability ids, behavior fragments (#behavior-id), non-goal "
            "ids, and state collections (project://spec/state/<state>).",
            {"uri": str},
        )
        async def spec_resolve_uri(args):
            out = await init_mcp.handle_spec_resolve_uri(
                project_path=project_path, uri=args["uri"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_resolve_uri)

    if "spec_load_existing" in agent_cfg.allowed_tools:

        @tool(
            "spec_load_existing",
            "Read the current structured spec as-is for the regen merge. "
            "Returns {} if no spec exists yet (first-time generation). "
            "Spec-generator only.",
            {},
        )
        async def spec_load_existing(args):
            out = await init_mcp.handle_spec_load_existing(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_load_existing)

    if "spec_generate_from_brief" in agent_cfg.allowed_tools:

        @tool(
            "spec_generate_from_brief",
            "Run the full deterministic spec generation pipeline: parse "
            "the brief, load the existing spec (if any), merge with "
            "metadata preservation, validate. Returns "
            "{spec: dict | None, gaps: [...]}. If spec is None, gaps "
            "contains blocking format errors or removed-from-brief "
            "issues — call spec_report_gaps with them. If spec is set, "
            "you may add semantic gaps (contradictions, ambiguities) of "
            "your own and then call spec_publish or spec_report_gaps. "
            "Spec-generator only.",
            {},
        )
        async def spec_generate_from_brief(args):
            out = await init_mcp.handle_spec_generate_from_brief(
                project_path=project_path,
                tickets=tickets,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_generate_from_brief)

    if "arch_get_field" in agent_cfg.allowed_tools:

        @tool(
            "arch_get_field",
            "Read a field from architecture.yaml by dotted path.",
            {"path": str},
        )
        async def arch_get_field(args):
            value = await init_mcp.handle_arch_get_field(
                project_path=project_path,
                path=args["path"],
            )
            return {"content": [{"type": "text", "text": json.dumps(value)}]}

        all_tools.append(arch_get_field)

    if "arch_list_fields" in agent_cfg.allowed_tools:

        @tool(
            "arch_list_fields",
            "List all dotted field paths defined in architecture.yaml.",
            {},
        )
        async def arch_list_fields(args):
            fields = await init_mcp.handle_arch_list_fields(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(fields)}]}

        all_tools.append(arch_list_fields)

    if "arch_set_field" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_field",
            "Set a field in architecture.yaml at a dotted path.",
            {"path": str, "value": Any},
        )
        async def arch_set_field(args):
            await init_mcp.handle_arch_set_field(
                threads=threads,
                project_path=project_path,
                path=args["path"],
                value=args["value"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(arch_set_field)

    if "arch_list_templates" in agent_cfg.allowed_tools:

        @tool(
            "arch_list_templates",
            "List the scaffold templates available for sa_propose_scaffold. "
            "Returns name, description, language, framework, and deploy_target "
            "for each. Call this before proposing a template — names are not "
            "discoverable from the filesystem.",
            {},
        )
        async def arch_list_templates(args):
            templates = await init_mcp.handle_arch_list_templates()
            return {"content": [{"type": "text", "text": json.dumps(templates)}]}

        all_tools.append(arch_list_templates)

    if "sa_propose_scaffold" in agent_cfg.allowed_tools:

        @tool(
            "sa_propose_scaffold",
            "Propose a project scaffold template for the orchestrator to apply. "
            "`template_name` must match one of the names returned by "
            "`arch_list_templates`.",
            {"template_name": str, "rationale": str, "config": dict},
        )
        async def sa_propose_scaffold(args):
            await init_mcp.handle_sa_propose_scaffold(
                tickets=tickets,
                threads=threads,
                bus=bus,
                template_name=args["template_name"],
                rationale=args["rationale"],
                config=args.get("config", {}),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(sa_propose_scaffold)

    if "recent_events" in agent_cfg.allowed_tools:

        @tool(
            "recent_events",
            "Return the most recent N bus messages across all topics, "
            "oldest-to-newest. Optional kind filter. Returns up to 100.",
            {"limit": int, "kind": str},
        )
        async def recent_events(args):
            limit = min(int(args.get("limit", 50)), 100)
            kind = args.get("kind")
            msgs = await bus.recent(limit=limit, kind=kind)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "events": [
                                {
                                    "id": m.id,
                                    "timestamp": m.timestamp.isoformat(),
                                    "topic": m.topic,
                                    "kind": (m.payload or {}).get("kind"),
                                    "sender": m.sender,
                                    "to": m.to,
                                    "payload": m.payload,
                                }
                                for m in msgs
                            ],
                        }),
                    }
                ],
            }

        all_tools.append(recent_events)

    # Strict-tools mode: drop any tool whose short name isn't in the
    # role's ``allowed_tools``. Init roles (po, sa, spec-generator)
    # opt into this so e.g. PO can't reach for commit_progress via
    # ToolSearch and waste turns trying to commit a non-existent
    # git repo. Operational roles leave strict_tools=False and keep
    # the legacy "all base tools always available" behavior.
    if agent_cfg.strict_tools:
        allowed = set(agent_cfg.allowed_tools)
        all_tools = [t for t in all_tools if t.name in allowed]

    # Wrap every tool handler so the correlation context is set on
    # each incoming call. MCP tool calls arrive in fresh asyncio tasks
    # that don't inherit the factory's contextvars.
    _agent_id_stamp = f"{agent_role}:{(ticket_id or '')[:8]}"
    for t in all_tools:
        t.handler = _wrap_with_context(
            t.handler,
            ticket_id=ticket_id or None,
            phase=phase_name or None,
            role=agent_role,
            agent_id=_agent_id_stamp,
        )

    return create_sdk_mcp_server(
        name="jig",
        tools=all_tools,
    )
