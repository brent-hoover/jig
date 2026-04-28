"""--print mode: run a single slash command against the daemon, print
the result to stdout, exit. CI / scripting escape hatch."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from jig.daemon import daemon_paths, daemon_status
from jig.tui.daemon_client import DaemonClient
from jig.tui.slash import SlashParseError, parse_slash


def run_print(command_line: str) -> int:
    """Run a slash command and return an exit code (0 on success).

    Exit codes:
      0 — command succeeded
      1 — command returned ok=False
      2 — slash command syntax error
      3 — daemon not running
    """
    try:
        parsed = parse_slash(command_line)
    except SlashParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    project_path = Path.cwd()
    status = daemon_status(project_path)
    if not status.running:
        print(
            "error: daemon not running; start it with `jig daemon start`",
            file=sys.stderr,
        )
        return 3

    addr = daemon_paths(project_path).socket_addr_file.read_text().strip()
    return asyncio.run(_dispatch(addr, parsed.name, parsed.args))


async def _dispatch(addr: str, name: str, args: list[str]) -> int:
    client = DaemonClient(addr_provider=lambda: addr)
    await client.connect()
    try:
        await client.send_command(name, {"args": args})
        async for msg in client.messages():
            if msg.get("type") == "result":
                if msg.get("ok"):
                    payload = msg.get("data")
                    if isinstance(payload, (dict, list)):
                        print(json.dumps(payload, indent=2))
                    elif payload is None:
                        # ok=True but no payload (e.g. void commands)
                        pass
                    else:
                        print(payload)
                    return 0
                else:
                    print(f"error: {msg.get('error', 'unknown')}", file=sys.stderr)
                    return 1
            # ignore snapshots/events that may arrive before the result
    finally:
        await client.close()
    return 0
