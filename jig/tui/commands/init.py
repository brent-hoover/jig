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
    # /init --proceed advances the multi-level PO state machine — Track B
    # Final affordance. The operator pushes forward when ready (e.g.,
    # post-L1 finalize, ready to start L2 / L3). This is mode-distinct
    # from project bootstrap; surfacing it under the same /init verb
    # keeps the multi-level PO entrypoints discoverable.
    if args and args[0] in ("--proceed", "proceed"):
        return await _proceed(orch=orch, project_path=project_path)

    # Parse --brief PATH and --auto out of args first so they don't get
    # mistaken for the project name.
    from pathlib import Path as _Path

    brief_file: _Path | None = None
    auto = False
    positional: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--brief" and i + 1 < len(args):
            brief_file = _Path(args[i + 1])
            i += 2
            continue
        if a.startswith("--brief="):
            brief_file = _Path(a.split("=", 1)[1])
            i += 1
            continue
        if a == "--auto":
            auto = True
            i += 1
            continue
        positional.append(a)
        i += 1

    # /init with no name defaults to "this directory" — uses project_path
    # as the target, with its basename as the project name. This is the
    # natural flow after `jig create <name>` where the operator is already
    # inside the new directory.
    init_in_cwd = not positional
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
        name = positional[0]
        force = "--force" in positional[1:]

    # Resolve --brief PATH relative to project_path so the operator can
    # write `/init demo --brief evals/projects/hn-cli/brief.md` without
    # caring about the daemon's cwd.
    if brief_file is not None and not brief_file.is_absolute():
        if project_path is not None:
            brief_file = (project_path / brief_file).resolve()
        else:
            brief_file = brief_file.resolve()
    if brief_file is not None and not brief_file.is_file():
        return {
            "ok": False,
            "error": f"--brief: file not found: {brief_file}",
        }

    from jig.init_prompts import AutoPromptHandler
    from jig.init_workflow import run_init
    from jig.tui.console_stream import make_streaming_console
    from jig.tui.tui_prompts import TuiPromptHandler

    console = make_streaming_console(emitter)
    prompts = (
        AutoPromptHandler() if auto
        else TuiPromptHandler(emitter=emitter, registry=prompt_registry)
    )

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
            name=target_str, force=force, console=console, prompts=prompts,
            brief_file=brief_file,
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

    return {"ok": True}


async def _proceed(*, orch, project_path) -> dict[str, Any]:
    """Advance the multi-level PO state machine by one step.

    Inspects what's on disk + what's open in the ticket store and
    picks the next level to spawn. Bones-Final scope:

    - L0 not committed → return error (operator must `/init <name>`).
    - L0 committed, L1 not committed → ensure ``discovery`` ticket is
      open so the orchestrator dispatches the L1 PO.
    - L1 committed, L2 not committed → open the L2 ``suites`` ticket.
    - L2 committed → check the L3 suite tickets; reopen the next
      pending one.

    The result includes ``level`` (which level we just advanced to)
    and ``ticket_id`` (the ticket the orchestrator will dispatch on
    next tick).
    """
    if project_path is None:
        return {
            "ok": False,
            "error": "/init --proceed requires a project_path",
        }
    if orch is None:
        return {"ok": False, "error": "/init --proceed requires a running orchestrator"}

    from jig.po_l1_mcp import L1_TICKET_ID
    from jig.po_l2_mcp import L2_TICKET_ID
    from jig.spec_loader import (
        discovery_path,
        load_suites_index,
        spec_path,
    )
    from jig.ticket import Ticket, TicketStatus, WorkType

    # L0 gate.
    if not (project_path / "docs" / "brief.md").is_file() and not spec_path(
        project_path
    ).is_file():
        return {
            "ok": False,
            "error": (
                "no L0 project pitch on disk; run `/init <name>` to bootstrap "
                "before invoking /init --proceed"
            ),
        }

    # L1 gate.
    if not discovery_path(project_path).is_file():
        # Ensure discovery ticket exists in OPEN state so orchestrator
        # picks up the L1 PO.
        existing = await orch.tickets.get(L1_TICKET_ID)
        if existing is None:
            await orch.tickets.create(
                Ticket(
                    id=L1_TICKET_ID,
                    work_type=WorkType.BRIEF,
                    title="L1 discovery — personas + journeys",
                    created_by="user",
                )
            )
        elif existing.status != TicketStatus.OPEN:
            await orch.tickets.update(
                L1_TICKET_ID, status=TicketStatus.OPEN, assignee=None
            )
        return {
            "ok": True,
            "data": {"level": "po-l1", "ticket_id": L1_TICKET_ID},
        }

    # L2 gate.
    try:
        suites_index = load_suites_index(project_path)
    except FileNotFoundError:
        existing = await orch.tickets.get(L2_TICKET_ID)
        if existing is None:
            await orch.tickets.create(
                Ticket(
                    id=L2_TICKET_ID,
                    work_type=WorkType.BRIEF,
                    title="L2 suite organization",
                    created_by="user",
                )
            )
        elif existing.status != TicketStatus.OPEN:
            await orch.tickets.update(
                L2_TICKET_ID, status=TicketStatus.OPEN, assignee=None
            )
        return {
            "ok": True,
            "data": {"level": "po-l2", "ticket_id": L2_TICKET_ID},
        }

    # L3 gate — pick the first suite without a brief on disk.
    for suite in suites_index.suites:
        brief = (
            project_path / ".jig" / "spec" / "suites" / suite.id / "brief.md"
        )
        if brief.is_file():
            continue
        ticket_id = f"suite-{suite.id}"
        existing = await orch.tickets.get(ticket_id)
        if existing is None:
            await orch.tickets.create(
                Ticket(
                    id=ticket_id,
                    work_type=WorkType.BRIEF,
                    title=f"L3 brief — {suite.id}",
                    description=suite.summary,
                    created_by="user",
                )
            )
        elif existing.status != TicketStatus.OPEN:
            await orch.tickets.update(
                ticket_id, status=TicketStatus.OPEN, assignee=None
            )
        return {
            "ok": True,
            "data": {
                "level": "po-l3",
                "ticket_id": ticket_id,
                "suite_id": suite.id,
            },
        }

    return {
        "ok": True,
        "data": {
            "level": "po-complete",
            "message": "all PO levels committed; nothing to advance",
        },
    }
