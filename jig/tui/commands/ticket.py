"""/ticket subcommands.

Subcommands:
  /ticket new --title <t> --size <s> [--type <t>] [--assignee <a>] [--description <d>]
  /ticket update <id> <field>=<value> [<field>=<value> ...]

For list: clients can already snapshot the tickets topic. /ticket get <id>
is not needed — the snapshot covers it.
"""

from __future__ import annotations

from typing import Any

from jig.tui.commands import register


def _parse_kvs(kv_args: list[str]) -> dict[str, str]:
    """Parse ['title=foo', 'size=m'] → {'title': 'foo', 'size': 'm'}."""
    out: dict[str, str] = {}
    for arg in kv_args:
        if "=" not in arg:
            continue
        k, _, v = arg.partition("=")
        out[k.strip()] = v.strip()
    return out


def _parse_flagged(args: list[str]) -> dict[str, str]:
    """Parse ['--title', 'foo', '--size', 'm'] → {'title': 'foo', 'size': 'm'}."""
    out: dict[str, str] = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            key = args[i][2:]
            out[key] = args[i + 1]
            i += 2
        else:
            i += 1
    return out


@register("ticket")
async def cmd_ticket(
    *, args: list[str], orch, project_path, **_kwargs
) -> dict[str, Any]:
    if orch is None:
        return {"ok": False, "error": "/ticket requires a running orchestrator"}
    if not args:
        return {"ok": False, "error": "/ticket needs a subcommand (new, update)"}
    sub = args[0]
    rest = args[1:]
    if sub == "new":
        from jig.ticket_mcp import handle_create_ticket

        flagged = _parse_flagged(rest)
        if not flagged.get("title"):
            return {"ok": False, "error": "/ticket new requires --title"}
        # Translate to handle_create_ticket's expected args shape
        create_args: dict[str, Any] = {
            "title": flagged.get("title", ""),
            "work_type": flagged.get("type", "feature"),
            "size": flagged.get("size", "m"),
        }
        if "description" in flagged:
            create_args["description"] = flagged["description"]
        if "assignee" in flagged:
            create_args["assignee"] = flagged["assignee"]
        try:
            ticket_id = await handle_create_ticket(
                tickets=orch.tickets,
                bus=orch.bus,
                sender="user",
                args=create_args,
                project_path=project_path,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"create failed: {exc}"}
        return {"ok": True, "data": {"ticket_id": ticket_id}}

    if sub == "update":
        from jig.ticket_mcp import handle_update_ticket

        if not rest:
            return {"ok": False, "error": "/ticket update needs <id>"}
        ticket_id = rest[0]
        kvs = _parse_kvs(rest[1:])
        if not kvs:
            return {
                "ok": False,
                "error": "/ticket update needs at least one field=value",
            }
        update_args: dict[str, Any] = {"ticket_id": ticket_id, **kvs}
        try:
            updated = await handle_update_ticket(
                tickets=orch.tickets,
                threads=orch.threads,
                bus=orch.bus,
                sender="user",
                args=update_args,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"update failed: {exc}"}
        return {
            "ok": True,
            "data": {"ticket_id": updated.id, "status": updated.status.value},
        }

    return {"ok": False, "error": f"unknown /ticket subcommand: {sub}"}
