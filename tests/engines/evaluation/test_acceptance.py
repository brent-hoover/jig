"""Evaluation bones — the BONES ACCEPTANCE GATE (Epic 8).

This is the test the whole bones phase exists to pass:

    The code-pipeline and review loop run **headless on a fixture**, without the
    daemon/TUI, using FixtureRunAgent (Epic 3) + pure decide() (Epic 4) + the
    Review contract (Epic 5) + Model entities (Epic 1) — no Orchestrator
    god-object, no mcp_server, no daemon, no TUI in the path.

``eval_run`` composes those pieces; the tests below drive a fixture through it
and assert the pipeline reaches a terminal state, the fake agent was the only
thing "run", and nothing in the path imports the legacy stack.
"""

from __future__ import annotations

import subprocess
import sys

from jig.engines.evaluation import BuildConfig, Fixture, eval_run
from jig.runtime import AgentRunResult
from jig.ticket import TicketStatus


async def test_headless_happy_path_runs_a_ticket_to_resolved() -> None:
    config = BuildConfig(
        tickets=("jig-1",),
        agent_result=AgentRunResult(status="success", final_text="done"),
    )

    result = await eval_run(config, fixtures=[Fixture(name="f1", diff="")])

    assert result.final_states["jig-1"] == TicketStatus.RESOLVED.value
    assert result.agent_runs == 1  # the fake agent ran exactly once
    assert result.findings == ()  # MechanicalReview bones stub finds nothing


async def test_failed_agent_leaves_the_ticket_failed() -> None:
    config = BuildConfig(
        tickets=("jig-1",),
        agent_result=AgentRunResult(status="failed", final_text="gave up"),
    )

    result = await eval_run(config, fixtures=[])

    assert result.final_states["jig-1"] == TicketStatus.FAILED.value
    assert result.agent_runs == 1


async def test_drives_multiple_tickets() -> None:
    config = BuildConfig(
        tickets=("jig-1", "jig-2"),
        agent_result=AgentRunResult(status="success", final_text="ok"),
    )

    result = await eval_run(config, fixtures=[])

    assert result.final_states["jig-1"] == TicketStatus.RESOLVED.value
    assert result.final_states["jig-2"] == TicketStatus.RESOLVED.value
    assert result.agent_runs == 2


def test_the_path_is_headless_no_legacy_stack() -> None:
    """Importing the eval harness must NOT pull in the Orchestrator god-object,
    the MCP server, the daemon, or the TUI. Run in a clean subprocess."""
    code = (
        "import jig.engines.evaluation, sys; "
        "legacy = [m for m in sys.modules if m in ('jig.orchestrator', "
        "'jig.daemon', 'jig.mcp_server', 'jig.ws_server') or m.startswith('jig.tui')]; "
        "print(legacy)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", f"eval path pulled in legacy stack: {out.stdout}"
