"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."
The contract itself is plain DTOs, so importing ``jig.edge`` must pull in none
of the engines, stores, orchestrator, or agent. Checked in a clean subprocess.
"""

from __future__ import annotations

import subprocess
import sys


def test_edge_imports_no_engine_internals() -> None:
    code = (
        "import jig.edge.api, sys; "
        "bad = [m for m in sys.modules if m.startswith(('jig.engines', 'jig.store', "
        "'jig.orchestrator', 'jig.coordinator', 'jig.agent', 'jig.runtime', "
        "'jig.substrate', 'jig.mcp_server'))]; "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", (
        f"jig.edge imported engine internals: {out.stdout}"
    )
