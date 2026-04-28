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
    # /init with no name defaults to "this directory" — uses project_path
    # as the target, with its basename as the project name. This is the
    # natural flow after `jig create <name>` where the operator is already
    # inside the new directory.
    init_in_cwd = not args
    if init_in_cwd:
        if project_path is None:
            return {
                "ok": False,
                "error": "/init requires a project name (e.g. /init dogfood)",
            }
        name = project_path.resolve().name
        if not name:
            return {
                "ok": False,
                "error": "/init: cannot infer name from project_path; pass one explicitly",
            }
        force = False
    else:
        name = args[0]
        force = "--force" in args[1:]

    from jig.init_workflow import run_init
    from jig.tui.console_stream import make_streaming_console
    from jig.tui.tui_prompts import TuiPromptHandler

    console = make_streaming_console(emitter)
    prompts = TuiPromptHandler(emitter=emitter, registry=prompt_registry)

    # Resolve the target as ABSOLUTE relative to the daemon's project_path
    # so the daemon process's cwd (which can differ from where the operator
    # launched `jig`) doesn't affect path resolution. run_init uses
    # Path(name) internally, so passing a fully-resolved string works.
    #
    # When init_in_cwd is True (no args), the target IS project_path itself.
    # Otherwise the target is project_path / name (a new sub-project).
    if project_path is not None:
        if init_in_cwd:
            target_path = project_path.resolve()
        else:
            target_path = (project_path / name).resolve()
        target_str = str(target_path)
    else:
        target_str = name

    try:
        await run_init(
            name=target_str, force=force, console=console, prompts=prompts
        )
    except Exception as exc:  # noqa: BLE001
        # Log the full traceback to the daemon log so we can debug what
        # actually failed. The wire envelope only carries str(exc).
        import logging

        logging.getLogger(__name__).exception(
            "/init %s failed", name
        )
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
