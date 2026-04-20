"""MCP server factory for agent ticket tools."""

import json
from pathlib import Path

from claude_agent_sdk import tool, create_sdk_mcp_server

from jig import thread_mcp, ticket_mcp
from jig.models import RoleConfig
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


def create_agent_mcp_server(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    agent_role: str,
    agent_cfg: RoleConfig,
    worktree_path: Path,
    project_path: Path,
    valid_roles: frozenset[str] = frozenset(),
    package_manager: str = "",
):
    """Create a Jig MCP server for a worker agent.

    ``threads`` is the Phase 4 typed thread-entry store; it sits on
    the same JSONL file as ``comments`` through Phase 4 (Task H
    collapses the two). Task C exposes three agent-facing tools
    (``thread_ask`` / ``thread_answer`` / ``thread_resolve_question``)
    that write typed entries via this store. The legacy
    ``ask_question`` tool stays registered for the operator-pause
    UX used by the WebSocket + TUI flow.
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
            comments=comments,
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
            tickets=tickets, comments=comments, bus=bus, sender=agent_role, args=args
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
            comments=comments,
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
        result = await ticket_mcp.handle_ask_question(
            tickets=tickets, comments=comments, bus=bus, sender=agent_role, args=args
        )
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
            comments=comments, ticket_id=args["ticket_id"], kind=args.get("kind")
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
            comments=comments,
            bus=bus,
            sender=agent_role,
            worktree_path=worktree_path,
            args=args,
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
    if package_manager:
        all_tools.append(add_dependency)

    return create_sdk_mcp_server(
        name="jig",
        tools=all_tools,
    )
