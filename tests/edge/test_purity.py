"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."

Enforced as **default-deny**: importing the whole ``jig.edge`` package may pull
in only edge-appropriate ``jig`` modules (the daemon front door, config, events,
the TUI, …). ANY other ``jig.*`` module — every engine, store, MCP handler,
workflow, agent, or domain type — is a violation by default, so the test can't
false-negative as new engine modules are added (a denylist would have to chase
each ``*_mcp`` etc.). See ``architecture/edge-audit.md``.
"""

from __future__ import annotations

import subprocess
import sys

# The ONLY jig modules the edge may import. Everything else is an engine
# internal. Edge-appropriate per architecture/edge-audit.md "keep" list; MVP
# expands this deliberately as it routes CLI/TUI through the daemon API.
_ALLOWED_PREFIXES = (
    "jig.edge",
    "jig.daemon",
    "jig.ws_server",
    "jig.config",
    "jig.events",
    "jig.logging_setup",
    "jig.safe_path",
    "jig.container",
    "jig.dev_env",
    "jig.story",
    "jig.tui",
    "jig.issues.cli",
    "jig.sim.cli",
)


def test_edge_imports_only_edge_appropriate_modules() -> None:
    code = (
        "import importlib, pkgutil, sys, jig.edge\n"
        "for m in pkgutil.walk_packages(jig.edge.__path__, prefix='jig.edge.'):\n"
        "    importlib.import_module(m.name)\n"
        f"allowed = {_ALLOWED_PREFIXES!r}\n"
        "def ok(m):\n"
        "    return any(m == a or m.startswith(a + '.') for a in allowed)\n"
        "leaked = sorted(m for m in sys.modules if m.startswith('jig.') and not ok(m))\n"
        "print(leaked)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", (
        f"jig.edge imported non-edge-appropriate modules: {out.stdout}"
    )
