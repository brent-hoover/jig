"""MCP server factory for agent ticket tools."""

import json
from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig import checkpoint_mcp, thread_mcp, ticket_mcp
from jig.models import RoleConfig
from jig.store import Message, MessageBus, MessageType
from jig.store.checkpoints import CheckpointStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Question, SystemEvent
from jig.ticket import TicketStatus


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
        {"work_type": str, "size": str, "title": str, "description": str, "assignee": str, "parent_id": str, "depends_on": list, "workflow": str, "labels": list},
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
        t = await ticket_mcp.handle_read_ticket(tickets=tickets, ticket_id=args["ticket_id"])
        return {"content": [{"type": "text", "text": t.model_dump_json()}]}

    @tool(
        "update_ticket",
        "Update fields on an existing ticket",
        {"ticket_id": str, "status": str, "description": str, "assignee": str, "labels": list},
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
        "Ask the operator a question. Posts question comment(s) and pauses the ticket (needs_info). "
        "The orchestrator will resume you once the operator answers. "
        "Use this instead of creating question tickets or manually setting needs_info.",
        {"ticket_id": str, "question": str, "questions": list},
    )
    async def ask_question(args):
        # Operator-pause UX: post blocking Question entries targeted at
        # "any_human", flip the ticket to needs_info, and let the TUI
        # drive the answer flow via the ws_server `answer_questions`
        # command. Typed agent-to-agent Q&A goes through thread_ask.
        ticket_id = args["ticket_id"]
        questions: list[str] = list(args.get("questions") or [])
        if isinstance(args.get("question"), str):
            questions.append(args["question"])
        if not questions:
            raise ValueError("at least one question is required")

        ticket = await tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"ticket {ticket_id} not found")

        comment_ids: list[str] = []
        for q in questions:
            cid = await threads.post(Question(
                ticket_id=ticket_id,
                author=agent_role,
                target="any_human",
                question=q,
                blocking=True,
            ))
            comment_ids.append(cid)
            await bus.publish(Message(
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
            ))

        before_status = ticket.status
        updated = await tickets.update(ticket_id, status=TicketStatus.NEEDS_INFO)
        if before_status != TicketStatus.NEEDS_INFO:
            await threads.post(SystemEvent(
                ticket_id=ticket_id,
                author=agent_role,
                event_type="status_change",
                content=f"status {before_status.value} -> needs_info",
            ))
        await bus.publish(Message(
            sender=agent_role,
            to=updated.assignee or "broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "ticket_updated",
                "ticket_id": ticket_id,
                "status": "needs_info",
            },
            topic=f"tickets.{ticket_id}",
        ))

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
        "Override an objection with explicit justification. Authorization is "
        "enforced against config.waiver_authority — if your role isn't in the "
        "list, this fails. The waiver and the original objection both stay "
        "in the thread as audit trail.",
        {"objection_id": str, "justification": str},
    )
    async def thread_waive(args):
        result = await thread_mcp.handle_thread_waive(
            threads=threads,
            bus=bus,
            sender=agent_role,
            args=args,
            project_path=project_path,
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
            [checkpoint_milestone, checkpoint_decision, checkpoint_deferred]
        )
    if package_manager:
        all_tools.append(add_dependency)

    return create_sdk_mcp_server(
        name="jig",
        tools=all_tools,
    )
