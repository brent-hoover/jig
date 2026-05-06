from typing import Any

from jig.daemon import DaemonAlreadyRunning, daemon_start, daemon_status
from jig.tui.commands import register


@register("start")
async def cmd_start(*, args: list[str], project_path, **_kwargs) -> dict[str, Any]:
    """`/start` — start the orchestrator daemon if not already running."""
    status = daemon_status(project_path)
    if status.running:
        return {"ok": True, "data": "Orchestrator already running."}
    try:
        result = daemon_start(project_path, docker=False)
        return {"ok": True, "data": f"Orchestrator started (pid={result.pid})."}
    except DaemonAlreadyRunning as exc:
        return {"ok": True, "data": str(exc)}
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
