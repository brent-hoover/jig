from typing import Any

from jig.tui.commands import register


@register("status")
async def cmd_status(*, args: list[str], orch, project_path, **_kwargs) -> dict[str, Any]:
    """`/status` — daemon + agent overview."""
    if orch is not None and hasattr(orch, "list_active_agents"):
        active = await orch.list_active_agents()
    else:
        active = []
    return {
        "ok": True,
        "data": {
            "agents_active": len(active),
            "agents": [
                {"role": getattr(a, "role", "?"), "ticket": getattr(a, "ticket_id", None)}
                for a in active
            ],
        },
    }
