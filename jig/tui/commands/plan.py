from typing import Any

from jig.tui.commands import register


@register("plan")
async def cmd_plan(*, project_path, orch, **_kwargs) -> dict[str, Any]:
    """`/plan` — create a planning ticket if one doesn't already exist."""
    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket, WorkType

    jig_dir = project_path / ".jig"
    if not (jig_dir / "spec" / "architecture.yaml").is_file():
        return {"ok": False, "error": "Project not initialized. Run /init first."}

    store_dir = jig_dir / "store"
    tickets = TicketStore(store_dir / "tickets.jsonl")
    await tickets.load()

    existing = await tickets.get("planning")
    if existing is not None:
        return {"ok": True, "data": "Planning ticket already exists."}

    spec_path = jig_dir / "spec" / "project.structured.yaml"
    await tickets.create(
        Ticket(
            id="planning",
            work_type=WorkType.PLANNING,
            title="Project planning",
            description=(
                "Break down the project spec into implementation tickets.\n\n"
                f"Spec: {spec_path}"
            ),
            workflow="project",
            created_by="tui",
        )
    )

    # If the orchestrator is live, kick it to pick up the new ticket immediately.
    if orch is not None and hasattr(orch, "_start_ready_tickets"):
        await orch._start_ready_tickets()

    return {"ok": True, "data": "Planning ticket created. PM agent will begin shortly."}
