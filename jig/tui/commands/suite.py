"""``/suite`` slash command — list / init / refresh suites (Track B Final).

Per ``docs/multi-level-spec/design.md`` §"Workflow integration":

- ``/suite list`` — list every suite with its brief status (pending /
  brief_ready / resolved).
- ``/suite init <id>`` — invoke L3 PO scoped to one suite. The L3
  agent dispatch happens through the orchestrator; for Final scope
  we simply create the suite ticket (id ``suite-<id>``) so the
  orchestrator picks it up on next dispatch tick.
- ``/suite refresh <id>`` — re-author the brief for an already-built
  suite. Re-creates the ticket in ``open`` so the L3 PO dispatch picks
  it up again. Existing brief is left in place; the L3 agent overwrites
  on its next finalize.
"""
from __future__ import annotations

from typing import Any

from jig.tui.commands import register


@register("suite")
async def cmd_suite(
    *,
    args: list[str],
    orch,
    project_path,
    **_kwargs,
) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "/suite needs a subcommand (list, init, refresh)"}
    sub = args[0]
    rest = args[1:]
    if sub == "list":
        return await _suite_list(project_path)
    if sub in ("init", "refresh"):
        if not rest:
            return {
                "ok": False,
                "error": f"/suite {sub} requires a suite id (e.g. /suite {sub} catalog)",
            }
        suite_id = rest[0]
        return await _suite_dispatch(orch, project_path, suite_id, refresh=(sub == "refresh"))
    return {"ok": False, "error": f"unknown /suite subcommand: {sub}"}


async def _suite_list(project_path) -> dict[str, Any]:
    """Return a per-suite status row.

    Status values:

    - ``pending`` — suite is in suites.yaml but has no brief.
    - ``brief_ready`` — brief exists on disk but the suite ticket is
      still open.
    - ``resolved`` — brief exists and the suite ticket is resolved.
    - ``no-suite-index`` — there's no suites.yaml on disk yet (returned
      as a single sentinel row so the caller can print a friendly
      "Author L2 first" message).
    """
    if project_path is None:
        return {"ok": False, "error": "/suite list requires a project_path"}
    from pathlib import Path

    from jig.spec_loader import load_suites_index

    try:
        index = load_suites_index(project_path)
    except FileNotFoundError:
        return {"ok": True, "data": {"suites": [], "status": "no-suite-index"}}

    suites: list[dict[str, str]] = []
    for s in index.suites:
        brief_md = (
            Path(project_path) / ".jig" / "spec" / "suites" / s.id / "brief.md"
        )
        status = "pending"
        if brief_md.is_file():
            status = "brief_ready"
        suites.append(
            {
                "id": s.id,
                "title": s.title,
                "summary": s.summary,
                "capabilities": list(s.capabilities),
                "status": status,
            }
        )
    return {"ok": True, "data": {"suites": suites, "status": "ok"}}


async def _suite_dispatch(
    orch,
    project_path,
    suite_id: str,
    *,
    refresh: bool,
) -> dict[str, Any]:
    """Create / re-open the suite ticket for L3 PO dispatch.

    Bones-Final scope: this command stages the ticket so the
    orchestrator's next dispatch tick picks it up. We don't synchronously
    spawn the L3 agent here (that path runs through ``run_agent`` /
    orchestrator) — that's the orchestrator's responsibility once the
    ticket lands in ``open`` status.
    """
    if orch is None:
        return {"ok": False, "error": "/suite requires a running orchestrator"}
    if project_path is None:
        return {"ok": False, "error": "/suite requires a project_path"}

    # Validate the suite exists in the index.
    try:
        from jig.spec_loader import load_suites_index

        index = load_suites_index(project_path)
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "no suites.yaml on disk; run L2 organizer first",
        }
    suite = index.suite_by_id(suite_id)
    if suite is None:
        known = [s.id for s in index.suites]
        return {
            "ok": False,
            "error": f"suite {suite_id!r} not in suites.yaml (known: {known})",
        }

    from jig.ticket import Ticket, TicketStatus, WorkType

    ticket_id = f"suite-{suite_id}"
    existing = await orch.tickets.get(ticket_id)
    if existing is None:
        ticket = Ticket(
            id=ticket_id,
            work_type=WorkType.BRIEF,
            title=f"L3 brief — {suite_id}",
            description=suite.summary,
            created_by="user",
        )
        await orch.tickets.create(ticket)
        action = "created"
    else:
        if refresh:
            # Reopen the ticket so the orchestrator dispatches L3 again.
            await orch.tickets.update(
                ticket_id, status=TicketStatus.OPEN, assignee=None
            )
            action = "reopened"
        else:
            # Init on an existing ticket is a no-op; dispatch was
            # already wired. Surface the current state so the operator
            # can decide whether to refresh.
            action = "already-exists"
    return {
        "ok": True,
        "data": {
            "ticket_id": ticket_id,
            "suite_id": suite_id,
            "action": action,
        },
    }
