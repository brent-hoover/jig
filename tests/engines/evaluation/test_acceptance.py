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

import pytest

from jig.engines.enforcement import InvariantContext
from jig.engines.evaluation import BuildConfig, Fixture, eval_run
from jig.model import Finding
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


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("failed", TicketStatus.FAILED),
        ("blocked", TicketStatus.BLOCKED),
        ("needs_info", TicketStatus.NEEDS_INFO),
    ],
)
async def test_non_success_agent_maps_to_its_terminal_status(
    status: str, expected: TicketStatus
) -> None:
    config = BuildConfig(
        tickets=("jig-1",),
        agent_result=AgentRunResult(status=status, final_text="x"),
    )

    result = await eval_run(config, fixtures=[])

    assert result.final_states["jig-1"] == expected.value
    assert result.agent_runs == 1


async def test_unexpected_agent_status_fails_loudly() -> None:
    config = BuildConfig(
        tickets=("jig-1",),
        agent_result=AgentRunResult(status="bogus", final_text=""),
    )

    with pytest.raises(ValueError, match="unexpected agent status"):
        await eval_run(config, fixtures=[])


async def test_drives_multiple_tickets() -> None:
    config = BuildConfig(
        tickets=("jig-1", "jig-2"),
        agent_result=AgentRunResult(status="success", final_text="ok"),
    )

    result = await eval_run(config, fixtures=[])

    assert result.final_states["jig-1"] == TicketStatus.RESOLVED.value
    assert result.final_states["jig-2"] == TicketStatus.RESOLVED.value
    assert result.agent_runs == 2


async def test_review_loop_runs_over_each_fixture_diff() -> None:
    # Prove the review loop actually runs (the default stub returns nothing, so
    # it can't show this): inject a Review that records the diffs it sees and
    # returns a finding, then assert both reached EvalResult.
    seen_diffs: list[str] = []

    class _RecordingReview:
        async def __call__(
            self, diff: str, invariant_context: InvariantContext
        ) -> list[Finding]:
            seen_diffs.append(diff)
            return [Finding(invariant="containment", message=f"drift in {diff}")]

    config = BuildConfig(
        tickets=("jig-1",),
        agent_result=AgentRunResult(status="success", final_text="ok"),
    )

    result = await eval_run(
        config,
        fixtures=[Fixture(name="a", diff="diffA"), Fixture(name="b", diff="diffB")],
        review=_RecordingReview(),
    )

    assert seen_diffs == ["diffA", "diffB"]
    assert {f.message for f in result.findings} == {"drift in diffA", "drift in diffB"}
    assert all(isinstance(f, Finding) for f in result.findings)


def test_the_path_is_headless_no_legacy_stack() -> None:
    """Importing the eval harness must NOT pull in the Orchestrator god-object,
    the MCP server, the daemon, or the TUI. Run in a clean subprocess."""
    # The eval path must use FixtureRunAgent — never the real agent stack
    # (jig.agent / jig.runtime.real) or the daemon/TUI/MCP/orchestrator.
    code = (
        "import jig.engines.evaluation, sys; "
        "legacy = [m for m in sys.modules if m in ('jig.orchestrator', "
        "'jig.daemon', 'jig.mcp_server', 'jig.ws_server', 'jig.agent', "
        "'jig.runtime.real') or m.startswith('jig.tui')]; "
        "print(legacy)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", f"eval path pulled in legacy stack: {out.stdout}"
