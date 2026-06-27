"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."
The contract is plain DTOs, so importing the WHOLE ``jig.edge`` package must pull
in none of the audited engine/store/orchestrator/agent/workflow/MCP/domain
internals (see ``architecture/edge-audit.md``). Checked in a clean subprocess
that walks every ``jig.edge`` submodule, so it can't false-negative as the
package grows.
"""

from __future__ import annotations

import subprocess
import sys

# The audited violation surface (architecture/edge-audit.md): engines, stores,
# the orchestrator/coordinator, the agent + runtime, MCP servers/handlers,
# authoring workflows, PM, and engine domain types. Edge-appropriate modules
# (daemon, ws_server, config, events, tui, story, container, dev_env, …) are
# deliberately NOT here.
_FORBIDDEN_PREFIXES = (
    "jig.engines",
    "jig.store",
    "jig.orchestrator",
    "jig.coordinator",
    "jig.agent",
    "jig.runtime",
    "jig.substrate",
    "jig.mcp_server",
    "jig.persistence",
    "jig.ticket",  # ticket, ticket_events, ticket_mcp
    "jig.thread",
    "jig.pm",
    "jig.graph",
    "jig.po_",  # po_ontology_mcp, po_l0..l3
    "jig.init_workflow",
    "jig.onboard_workflow",
    "jig.quartermaster",
    "jig.canonicalize",
    "jig.catalog",
    "jig.cascade_viewer",
    "jig.section_locks",
    "jig.profile_loader",
    "jig.spec_loader",
    "jig.schemas",
    "jig.reviewers",
    "jig.check",  # checks, check_runner, check_gate, …
    "jig.boundary_rules",
    "jig.models",
    "jig.issues.mcp",  # NOT jig.issues.cli (edge-appropriate)
)


def test_edge_imports_no_engine_internals() -> None:
    code = (
        "import importlib, pkgutil, sys, jig.edge\n"
        "for m in pkgutil.walk_packages(jig.edge.__path__, prefix='jig.edge.'):\n"
        "    importlib.import_module(m.name)\n"
        f"forbidden = {_FORBIDDEN_PREFIXES!r}\n"
        "leaked = sorted(m for m in sys.modules if m.startswith(forbidden))\n"
        "print(leaked)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", (
        f"jig.edge imported engine internals: {out.stdout}"
    )
