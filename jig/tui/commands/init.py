"""/init <name> — run jig init inside the daemon, stream output + prompts to TUI.

Operator types `/init dogfood` in Now. The handler creates a streaming
Console (whose output ships as agents/render events) and a
TuiPromptHandler (whose ask_* methods round-trip via prompts/request +
prompt_reply commands), then awaits the existing run_init function.

The handler does NOT return until run_init completes — i.e., a
{type: "result", ok: true} envelope arrives at the end of the entire
init flow. During execution, the operator sees inline output and is
prompted via the TUI's input field.
"""
from __future__ import annotations

from typing import Any

from jig.tui.commands import register


@register("init")
async def cmd_init(
    *,
    args: list[str],
    orch,
    project_path,
    prompt_registry,
    emitter,
    **_kwargs,
) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "/init requires a project name (e.g. /init dogfood)"}
    name = args[0]
    force = "--force" in args[1:]

    from jig.init_workflow import run_init
    from jig.tui.console_stream import make_streaming_console
    from jig.tui.tui_prompts import TuiPromptHandler

    console = make_streaming_console(emitter)
    prompts = TuiPromptHandler(emitter=emitter, registry=prompt_registry)

    try:
        await run_init(name=name, force=force, console=console, prompts=prompts)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"init failed: {exc}"}

    # Init may have just bootstrapped the project. Trigger an orchestrator
    # reload so the daemon picks up new config + stores and the TUI's
    # subsequent snapshots reflect the freshly created state.
    if orch is not None:
        try:
            await orch.reload()
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                "orchestrator reload after init failed: %s", exc, exc_info=True
            )

    return {"ok": True, "data": {"name": name}}
