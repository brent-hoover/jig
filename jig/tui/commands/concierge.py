"""/concierge "<query>" — spawn a one-shot read-only concierge agent.

Free-text input on the Now screen routes here. The handler creates an
ephemeral concierge ticket, spawns the concierge role agent against it,
and lets agent_text events flow back to the TUI via the typed agents/text
events the existing classifier already produces.

The concierge has read-only tools (spec queries, ticket read, recent
events). It cannot write — it can only answer questions or recommend
slash commands the operator can run themselves.
"""

from __future__ import annotations

import uuid
from typing import Any

from jig.tui.commands import register


@register("concierge")
async def cmd_concierge(
    *,
    args: list[str],
    orch,
    project_path,
    emitter,
    **_kwargs,
) -> dict[str, Any]:
    if orch is None:
        return {"ok": False, "error": "concierge requires a running orchestrator"}
    if not args:
        return {"ok": False, "error": "/concierge requires a query"}
    query = " ".join(args).strip()
    if not query:
        return {"ok": False, "error": "/concierge query was empty"}

    from jig.agent import run_agent
    from jig.persistence import load_role
    from jig.project import load_project
    from jig.runtime import AgentSpawnContext, SpawnReason
    from jig.ticket import Ticket, TicketStatus, WorkType

    try:
        role_cfg = load_role(project_path, "concierge")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"concierge role not loadable: {exc}"}

    try:
        project = load_project(project_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"concierge project not loadable: {exc}"}

    # Create the concierge ticket with workflow="thread" and status=RESOLVED
    # so the orchestrator never picks it up for the full spec→test→…→document
    # pipeline. The concierge is a one-shot read-only Q&A run — it does not
    # generate workflow tickets. Without this guard, every free-text query
    # the operator submits seeds a 6-phase implementation pipeline against
    # the literal text of their question.
    ticket_id = f"concierge-{uuid.uuid4().hex[:8]}"
    ticket = Ticket(
        id=ticket_id,
        work_type=WorkType.SPIKE,
        workflow="thread",
        status=TicketStatus.RESOLVED,
        title=query[:80],
        description=query,
        created_by="user",
    )
    await orch.tickets.create(ticket)

    ctx = AgentSpawnContext(
        role="concierge",
        role_cfg=role_cfg,
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=ticket,
        parent=None,
        worktree_path=project_path,
        project=project,
        tickets=orch.tickets,
        threads=orch.threads,
        memory=orch.memory,
        bus=orch.bus,
    )

    try:
        await run_agent(ctx, emitter=emitter)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"concierge run failed: {exc}"}
    return {"ok": True, "data": {"ticket_id": ticket_id}}
