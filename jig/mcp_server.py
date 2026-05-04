"""MCP server factory for agent ticket tools."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig import (
    checkpoint_mcp,
    init_mcp,
    planner_pm_mcp,
    po_l0_mcp,
    po_l1_mcp,
    po_l2_mcp,
    po_l3_mcp,
    po_ontology_mcp,
    quartermaster,
    sa_incremental_mcp,
    sa_mcp,
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

    # ---- L1 PO MCP tools (Track B MVP) ------------------------------------
    # The L1 Discovery PO drives the 5-phase journey-walk per
    # docs/multi-level-spec/design.md §"L1 PO behavior". Authoring
    # tools render to .jig/spec/discovery.md + per-journey playbacks;
    # state-tracking tools update .jig/spec/discovery.state.yaml so
    # mid-walk pauses + daemon restarts can resume cleanly.

    if "discovery_set_intro" in agent_cfg.allowed_tools:

        @tool(
            "discovery_set_intro",
            "Stash the L1 discovery doc's intro paragraph (rendered "
            "above '## Personas' at finalize time). Call once early in "
            "the conversation; ``discovery_finalize`` consumes the "
            "stashed value when no explicit ``intro`` is passed.",
            {"intro": str},
        )
        async def discovery_set_intro(args):
            await po_l1_mcp.handle_discovery_set_intro(
                project_path=project_path,
                intro=args["intro"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_set_intro)

    if "discovery_add_persona" in agent_cfg.allowed_tools:

        @tool(
            "discovery_add_persona",
            "Stage a persona for the L1 discovery doc. ``persona_id`` "
            "is kebab-case (e.g. 'merchant'). Replaces any prior "
            "staging by the same id so the L1 PO can refine without "
            "duplicates.",
            {"persona_id": str, "description": str},
        )
        async def discovery_add_persona(args):
            await po_l1_mcp.handle_discovery_add_persona(
                project_path=project_path,
                persona_id=args["persona_id"],
                description=args["description"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_add_persona)

    if "discovery_set_phase" in agent_cfg.allowed_tools:

        @tool(
            "discovery_set_phase",
            "Record the L1 PO's current position in the 5-phase walk. "
            "``phase`` is 1-5 per design.md §'L1 PO behavior'; "
            "``persona_id`` / ``journey_id`` are nullable for Phase 1 "
            "(framing) which runs before the first persona is locked "
            "in. Pass empty strings to leave persona/journey unset.",
            {
                "persona_id": str,
                "journey_id": str,
                "phase": int,
                "step": int,
            },
        )
        async def discovery_set_phase(args):
            await po_l1_mcp.handle_discovery_set_phase(
                project_path=project_path,
                persona_id=args.get("persona_id") or None,
                journey_id=args.get("journey_id") or None,
                phase=args["phase"],
                step=args.get("step", 0),
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_set_phase)

    if "discovery_set_next_question" in agent_cfg.allowed_tools:

        @tool(
            "discovery_set_next_question",
            "Capture the question the L1 PO is about to ask. Call "
            "before each ``ask_question`` so resume can replay the "
            "exact thread without drift.",
            {"question": str},
        )
        async def discovery_set_next_question(args):
            await po_l1_mcp.handle_discovery_set_next_question(
                project_path=project_path,
                question=args["question"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_set_next_question)

    if "discovery_add_journey" in agent_cfg.allowed_tools:

        @tool(
            "discovery_add_journey",
            "Stage a committed journey for inclusion in discovery.md. "
            "``persona_id`` / ``journey_id`` are kebab-case "
            "(journey id convention starts with 'j-'). "
            "``capability_ids`` is the kebab-case ids the journey "
            "implies — every entry must also appear in the roster via "
            "``discovery_add_capability``. ``playback_text`` writes the "
            "Phase-5 audit trail in the same call when present.",
            {
                "persona_id": str,
                "journey_id": str,
                "title": str,
                "narrative": str,
                "capability_ids": list,
                "playback_text": str,
            },
        )
        async def discovery_add_journey(args):
            await po_l1_mcp.handle_discovery_add_journey(
                project_path=project_path,
                persona_id=args["persona_id"],
                journey_id=args["journey_id"],
                title=args["title"],
                narrative=args["narrative"],
                capability_ids=args.get("capability_ids", []),
                playback_text=args.get("playback_text") or None,
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_add_journey)

    if "discovery_add_capability" in agent_cfg.allowed_tools:

        @tool(
            "discovery_add_capability",
            "Append (or merge by id) a capability into the L1 roster. "
            "Use the operator's verbs in the description. Re-calling "
            "with the same id union-merges ``journey_ids`` so a "
            "capability shared across journeys lands cited by all of "
            "them.",
            {
                "capability_id": str,
                "description": str,
                "journey_ids": list,
            },
        )
        async def discovery_add_capability(args):
            await po_l1_mcp.handle_discovery_add_capability(
                project_path=project_path,
                capability_id=args["capability_id"],
                description=args["description"],
                journey_ids=args.get("journey_ids", []),
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_add_capability)

    if "discovery_stash_pending_capability" in agent_cfg.allowed_tools:

        @tool(
            "discovery_stash_pending_capability",
            "Bookmark a capability extracted mid-walk before Phase-5 "
            "commit. Stashed entries survive interruption; commit them "
            "via ``discovery_add_capability`` once the operator confirms "
            "the playback. Idempotent on (capability_id, journey_id).",
            {
                "capability_id": str,
                "description": str,
                "journey_id": str,
            },
        )
        async def discovery_stash_pending_capability(args):
            await po_l1_mcp.handle_discovery_stash_pending_capability(
                project_path=project_path,
                capability_id=args["capability_id"],
                description=args["description"],
                journey_id=args["journey_id"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_stash_pending_capability)

    if "discovery_clear_pending" in agent_cfg.allowed_tools:

        @tool(
            "discovery_clear_pending",
            "Drop all stashed pending capabilities for a journey — call "
            "after the journey commits via ``discovery_add_journey``.",
            {"journey_id": str},
        )
        async def discovery_clear_pending(args):
            await po_l1_mcp.handle_discovery_clear_pending(
                project_path=project_path,
                journey_id=args["journey_id"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_clear_pending)

    if "discovery_set_playback" in agent_cfg.allowed_tools:

        @tool(
            "discovery_set_playback",
            "Write the per-journey Phase-5 playback markdown to "
            ".jig/spec/discovery/playbacks/<journey_id>.md. "
            "``discovery_add_journey`` accepts an inline ``playback_text`` "
            "for the common case; this entrypoint exists for separate "
            "or revised playbacks.",
            {"journey_id": str, "playback_text": str},
        )
        async def discovery_set_playback(args):
            await po_l1_mcp.handle_discovery_set_playback(
                project_path=project_path,
                journey_id=args["journey_id"],
                playback_text=args["playback_text"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(discovery_set_playback)

    if "discovery_load_state" in agent_cfg.allowed_tools:

        @tool(
            "discovery_load_state",
            "Read .jig/spec/discovery.state.yaml for the L1 PO's resume "
            "greeting. Returns ``{state: null}`` when no prior session "
            "exists; otherwise the structured DiscoveryState dump.",
            {},
        )
        async def discovery_load_state(args):
            out = await po_l1_mcp.handle_discovery_load_state(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(discovery_load_state)

    if "discovery_finalize" in agent_cfg.allowed_tools:

        @tool(
            "discovery_finalize",
            "Synthesize the L1 discovery doc, clear in-flight state, "
            "and hand off to L2 PO. Writes both .jig/spec/discovery.md "
            "(markdown) and .jig/spec/discovery.structured.yaml "
            "(structured cache). When ``personas`` / ``journeys`` / "
            "``capability_roster`` are passed, they override the staged "
            "sidecars from per-tool calls. Validation: every persona "
            "needs a journey, every journey needs a capability, ids "
            "kebab-case + unique, references resolve. Call exactly "
            "once when the operator says L1 is done.",
            {
                "project_name": str,
                "intro": str,
                "personas": list,
                "journeys": list,
                "capability_roster": list,
            },
        )
        async def discovery_finalize(args):
            entry_id = await po_l1_mcp.handle_discovery_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                project_name=args["project_name"],
                intro=args.get("intro") or None,
                personas=args.get("personas"),
                journeys=args.get("journeys"),
                capability_roster=args.get("capability_roster"),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(discovery_finalize)

    # ---- Project ontology MCP tools (Track B6 MVP) ------------------------
    # The L1 PO is the primary author — terms surface during journey
    # walks; downstream agents (SA / VD / PM / dev / reviewer) read the
    # same file so terminology stays consistent across the project's
    # artifacts and code. See docs/multi-level-spec/design.md
    # §"Project ontology — capturing the operator's domain vocabulary".

    if "ontology_stash_term" in agent_cfg.allowed_tools:

        @tool(
            "ontology_stash_term",
            "Buffer a domain term that surfaced mid-conversation; the "
            "full definition gets captured later via ``ontology_add_term`` "
            "(typically at Phase-5 playback). ``context`` is a one-line "
            "cue (a journey id, the operator's prior answer) so resume "
            "can re-anchor without re-deriving the prompt. Idempotent on "
            "lowercased term — re-stashing updates the recorded context.",
            {"term": str, "context": str},
        )
        async def ontology_stash_term(args):
            await po_ontology_mcp.handle_ontology_stash_term(
                project_path=project_path,
                term=args["term"],
                context=args["context"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(ontology_stash_term)

    if "ontology_add_term" in agent_cfg.allowed_tools:

        @tool(
            "ontology_add_term",
            "Commit a confirmed term to ``.jig/spec/ontology.md`` — the "
            "project's ubiquitous-language vocabulary read by every "
            "subsequent agent. Replaces by lowercased term so re-adding "
            "swaps the definition + examples in place (preserving "
            "first-mention reading order). ``examples`` is optional; "
            "blank entries are dropped. Folds clear-from-pending so an "
            "LLM that forgets the explicit clear step doesn't leak "
            "stale state.",
            {"term": str, "definition": str, "examples": list},
        )
        async def ontology_add_term(args):
            await po_ontology_mcp.handle_ontology_add_term(
                project_path=project_path,
                term=args["term"],
                definition=args["definition"],
                examples=args.get("examples") or [],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

        all_tools.append(ontology_add_term)

    if "ontology_get_terms" in agent_cfg.allowed_tools:

        @tool(
            "ontology_get_terms",
            "Return the committed project ontology as a JSON dump "
            "(``{terms: [{term, definition, examples}, ...]}``). Absent "
            "ontology surfaces as ``{terms: []}`` rather than an error "
            "so downstream readers can call opportunistically.",
            {},
        )
        async def ontology_get_terms(args):
            out = await po_ontology_mcp.handle_ontology_get_terms(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(ontology_get_terms)

    if "ontology_lookup" in agent_cfg.allowed_tools:

        @tool(
            "ontology_lookup",
            "Look up a single ontology entry by term (case-insensitive). "
            "Returns the matching ``OntologyTerm`` JSON dump or "
            "``null`` when absent — callers fall back to general "
            "vocabulary on miss rather than raising.",
            {"term": str},
        )
        async def ontology_lookup(args):
            out = await po_ontology_mcp.handle_ontology_lookup(
                project_path=project_path,
                term=args["term"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(ontology_lookup)

    if "l2_finalize" in agent_cfg.allowed_tools:

        @tool(
            "l2_finalize",
            "Author the L2 suite organization at .jig/spec/suites.yaml "
            "and hand off to the L3 PO. ``suites`` is a list of dicts "
            "matching the Suite schema (id, title, summary, "
            "capabilities). Optional ``crosscutting_non_goals`` is a "
            "list of {id, text, rationale?}. Validation: every L1 "
            "capability must appear in exactly one suite — the "
            "validator surfaces a friendly diff (missing / extras / "
            "duplicates) when not. Soft target of 3-5 capabilities per "
            "suite — outside that range surfaces a warning in the "
            "handoff summary but does not reject. Call exactly once "
            "when the operator has confirmed the grouping.",
            {"suites": list, "crosscutting_non_goals": list},
        )
        async def l2_finalize(args):
            entry_id = await po_l2_mcp.handle_l2_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                suites=args.get("suites", []),
                crosscutting_non_goals=args.get("crosscutting_non_goals", []),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(l2_finalize)

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

    if "sa_finalize" in agent_cfg.allowed_tools:

        @tool(
            "sa_finalize",
            "Write the v2 SA bones artifacts: ``architecture.yaml`` "
            "(project-level: data stores, modules, intent) and "
            "``modules/<module-id>/contracts.yaml`` (per-module: owned "
            "collections, integration AC). ``architecture`` is a dict "
            "matching the ``Architecture`` schema; must have at least "
            "one ``data_stores`` entry and one ``modules`` entry "
            "(each module needs ``intent``). ``module_contracts`` is a "
            "dict matching ``ContractsFile``; its ``module`` must "
            "reference a module id from ``architecture`` and must have "
            "at least one ``owns`` entry and one ``integration_ac`` "
            "entry. Hands off to PM. Call exactly once when the "
            "architecture is ready.",
            {"architecture": dict, "module_contracts": dict},
        )
        async def sa_finalize(args):
            entry_id = await sa_mcp.handle_sa_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                architecture=args["architecture"],
                module_contracts=args["module_contracts"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(sa_finalize)

    # ---- v2 SA MVP incremental authoring (Track C MVP) -------------------
    # The MVP SA walks modules + integration boundaries one by one,
    # accumulating contracts as it goes. Each upsert is idempotent
    # (re-set with the same id replaces the entry) so the agent can
    # iterate freely. ``arch_finalize`` re-validates + hands off to PM
    # via the same path the bones one-shot uses. See
    # ``docs/sa-architecture/design.md`` §"SA workflow — discovery loop".

    if "arch_set_module" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_module",
            "Upsert one Module entry into architecture.yaml. ``module`` "
            "is a dict matching the ``Module`` schema (id, title, "
            "summary, implements_capabilities, owns, tier_hint, "
            "requires_tracer_bullet, n_a_categories, intent). "
            "Idempotent — re-setting the same id replaces the entry. "
            "Returns the module id for use in subsequent module_set_* calls.",
            {"module": dict},
        )
        async def arch_set_module(args):
            mid = await sa_incremental_mcp.handle_arch_set_module(
                project_path=project_path,
                module=args["module"],
            )
            return {"content": [{"type": "text", "text": mid}]}

        all_tools.append(arch_set_module)

    if "arch_set_data_store" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_data_store",
            "Upsert one DataStore entry. ``data_store`` is a dict "
            "matching the ``DataStore`` schema (id, kind, rationale?, "
            "accessed_by). Idempotent on id.",
            {"data_store": dict},
        )
        async def arch_set_data_store(args):
            sid = await sa_incremental_mcp.handle_arch_set_data_store(
                project_path=project_path,
                data_store=args["data_store"],
            )
            return {"content": [{"type": "text", "text": sid}]}

        all_tools.append(arch_set_data_store)

    if "arch_set_shared_contract" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_shared_contract",
            "Upsert one SharedContract. ``shared_contract`` is a dict "
            "matching the schema (id, type, description?, schema_ref?, "
            "payload_ref?, publisher?, subscribers?). Idempotent on id.",
            {"shared_contract": dict},
        )
        async def arch_set_shared_contract(args):
            sid = await sa_incremental_mcp.handle_arch_set_shared_contract(
                project_path=project_path,
                shared_contract=args["shared_contract"],
            )
            return {"content": [{"type": "text", "text": sid}]}

        all_tools.append(arch_set_shared_contract)

    if "arch_set_cross_cutting_policy" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_cross_cutting_policy",
            "Upsert one CrossCuttingPolicy. ``policy`` is a dict "
            "matching the schema (id, polarity, rule, "
            "auto_generates_integration_ac?). Idempotent on id.",
            {"policy": dict},
        )
        async def arch_set_cross_cutting_policy(args):
            pid = await sa_incremental_mcp.handle_arch_set_cross_cutting_policy(
                project_path=project_path,
                policy=args["policy"],
            )
            return {"content": [{"type": "text", "text": pid}]}

        all_tools.append(arch_set_cross_cutting_policy)

    if "arch_set_open_question" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_open_question",
            "Upsert one architecture-level OpenQuestion. ``open_question`` "
            "is a dict (id, text, blocking?). Idempotent on id. Use "
            "module_set_open_question for module-scoped questions.",
            {"open_question": dict},
        )
        async def arch_set_open_question(args):
            qid = await sa_incremental_mcp.handle_arch_set_open_question(
                project_path=project_path,
                open_question=args["open_question"],
            )
            return {"content": [{"type": "text", "text": qid}]}

        all_tools.append(arch_set_open_question)

    if "arch_set_risk" in agent_cfg.allowed_tools:

        @tool(
            "arch_set_risk",
            "Upsert one Risk on architecture.yaml. ``risk`` is a dict "
            "(id, text, impact, likelihood, status, spike_ticket?, "
            "accepted_if?, blocking?, dependent_contracts?, "
            "cascade_breaking_likely?, intent?). Idempotent on id. "
            "Validation: when ``status`` is past ``open`` (i.e. "
            "``spike_proposed``, ``spike_running``, ``mitigated``, "
            "``accepted``, ``confirmed_impossible``), "
            "``dependent_contracts`` MUST be non-empty AND ``intent`` "
            "MUST be set — the cascade workflow needs both. ``open`` "
            "status escapes the gate so noted-but-uncommitted risks "
            "can be captured cheaply.",
            {"risk": dict},
        )
        async def arch_set_risk(args):
            rid = await sa_incremental_mcp.handle_arch_set_risk(
                project_path=project_path,
                risk=args["risk"],
            )
            return {"content": [{"type": "text", "text": rid}]}

        all_tools.append(arch_set_risk)

    if "module_set_owned_collection" in agent_cfg.allowed_tools:

        @tool(
            "module_set_owned_collection",
            "Upsert one OwnedCollection on a module's contracts.yaml. "
            "``owned_collection`` is a dict (collection, db, schema_ref?, "
            "write_access, read_access). Keyed by ``collection`` name. "
            "Idempotent.",
            {"module_id": str, "owned_collection": dict},
        )
        async def module_set_owned_collection(args):
            cid = await sa_incremental_mcp.handle_module_set_owned_collection(
                project_path=project_path,
                module_id=args["module_id"],
                owned_collection=args["owned_collection"],
            )
            return {"content": [{"type": "text", "text": cid}]}

        all_tools.append(module_set_owned_collection)

    if "module_set_external_dependency" in agent_cfg.allowed_tools:

        @tool(
            "module_set_external_dependency",
            "Upsert one ExternalDependency on a module's contracts.yaml. "
            "``external_dependency`` is a dict (id, kind, rate_limit?, "
            "auth?, failure_mode?, max_size?). Idempotent on id.",
            {"module_id": str, "external_dependency": dict},
        )
        async def module_set_external_dependency(args):
            did = await sa_incremental_mcp.handle_module_set_external_dependency(
                project_path=project_path,
                module_id=args["module_id"],
                external_dependency=args["external_dependency"],
            )
            return {"content": [{"type": "text", "text": did}]}

        all_tools.append(module_set_external_dependency)

    if "module_set_integration_ac" in agent_cfg.allowed_tools:

        @tool(
            "module_set_integration_ac",
            "Upsert one capability's integration-AC list on a module. "
            "``integration_ac`` is a dict (capability, must). Keyed by "
            "``capability``. Idempotent — re-setting replaces the full "
            "MUST list for that capability.",
            {"module_id": str, "integration_ac": dict},
        )
        async def module_set_integration_ac(args):
            cid = await sa_incremental_mcp.handle_module_set_integration_ac(
                project_path=project_path,
                module_id=args["module_id"],
                integration_ac=args["integration_ac"],
            )
            return {"content": [{"type": "text", "text": cid}]}

        all_tools.append(module_set_integration_ac)

    if "module_set_behavioral_contract" in agent_cfg.allowed_tools:

        @tool(
            "module_set_behavioral_contract",
            "Upsert one BehavioralContract on a module. "
            "``behavioral_contract`` is a dict matching the schema "
            "(id, applies_to?, scope?, precondition?, postcondition?, "
            "invariant?, side_effects?, side_effect_required?, "
            "enforcement?, intent). Returns ``{id, warnings}`` so the "
            "agent sees authoring-quality issues before finalize. "
            "Idempotent on id.",
            {"module_id": str, "behavioral_contract": dict},
        )
        async def module_set_behavioral_contract(args):
            result = await sa_incremental_mcp.handle_module_set_behavioral_contract(
                project_path=project_path,
                module_id=args["module_id"],
                behavioral_contract=args["behavioral_contract"],
            )
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        all_tools.append(module_set_behavioral_contract)

    if "module_set_data_contract" in agent_cfg.allowed_tools:

        @tool(
            "module_set_data_contract",
            "Upsert one DataContract on a module. ``data_contract`` is "
            "a dict (id, type, description?, schema_ref?, fields?, "
            "intent). Idempotent on id.",
            {"module_id": str, "data_contract": dict},
        )
        async def module_set_data_contract(args):
            did = await sa_incremental_mcp.handle_module_set_data_contract(
                project_path=project_path,
                module_id=args["module_id"],
                data_contract=args["data_contract"],
            )
            return {"content": [{"type": "text", "text": did}]}

        all_tools.append(module_set_data_contract)

    if "module_set_open_question" in agent_cfg.allowed_tools:

        @tool(
            "module_set_open_question",
            "Upsert one module-scoped OpenQuestion. ``open_question`` "
            "is a dict (id, text, blocking?). Idempotent on id.",
            {"module_id": str, "open_question": dict},
        )
        async def module_set_open_question(args):
            qid = await sa_incremental_mcp.handle_module_set_open_question(
                project_path=project_path,
                module_id=args["module_id"],
                open_question=args["open_question"],
            )
            return {"content": [{"type": "text", "text": qid}]}

        all_tools.append(module_set_open_question)

    if "arch_finalize" in agent_cfg.allowed_tools:

        @tool(
            "arch_finalize",
            "Finalize the incrementally-authored architecture + per-"
            "module contracts. Re-validates against the schemas, runs "
            "the SA checklist on each module (raises on unmet "
            "categories without ``n_a_categories`` exemption), surfaces "
            "behavioral-contract authoring warnings as a Note on the "
            "architecture ticket, posts the same Handoff the bones path "
            "posts (phase=PM), resolves the architecture ticket. Call "
            "exactly once when all modules + contracts are authored.",
            {"summary": str},
        )
        async def arch_finalize(args):
            entry_id = await sa_incremental_mcp.handle_arch_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                summary=args["summary"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(arch_finalize)

    if "plan_finalize" in agent_cfg.allowed_tools:

        @tool(
            "plan_finalize",
            "Write the v2 build plan: ``.jig/plan/build-plan.yaml`` "
            "(epics × bones / mvp / final layers, ordered "
            "``bones_first`` by default). ``plan`` is a dict matching "
            "the ``BuildPlan`` schema (top-level keys: ``project``, "
            "``ordering_rule``, ``epics``, ``stalled``, "
            "``open_questions``). Every epic requires ``intent`` "
            "(problem / simplest_solution / complications_considered) "
            "and at least one epic must have a non-empty bones layer "
            "for ``bones_first`` ordering to dispatch. Ticket ids "
            "must be unique across the whole plan. Hands off to the "
            "Coordinator. Call exactly once when the plan is ready.",
            {"plan": dict},
        )
        async def plan_finalize(args):
            entry_id = await planner_pm_mcp.handle_plan_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                plan=args["plan"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": entry_id}]}

        all_tools.append(plan_finalize)

    if "quartermaster_briefing" in agent_cfg.allowed_tools:

        @tool(
            "quartermaster_briefing",
            "Produce an operator-facing briefing of the analytics event "
            "stream over the last 7 days. Returns markdown with a "
            "headline (tickets completed/failed/in-progress, escalations, "
            "average cycle time), notable patterns (module repeated "
            "escalations, reviewer-comment repeats, stalled tickets), "
            "and a top-3 attention-recommendation list. Read-only, "
            "deterministic — no LLM interpretation. Call this when the "
            "operator asks for a briefing.",
            {},
        )
        async def quartermaster_briefing(args):
            md = await quartermaster.handle_quartermaster_briefing(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": md}]}

        all_tools.append(quartermaster_briefing)

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
