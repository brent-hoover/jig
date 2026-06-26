"""Runtime bones — FixtureRunAgent (Epic 3, task 4).

The canned implementation: returns a preset result without spawning anything.
This is the bone the headless eval harness (Epic 8) drives.
"""

from __future__ import annotations

from jig.runtime import AgentRunResult, FixtureRunAgent, RunAgent


async def test_fixture_returns_its_canned_result() -> None:
    canned = AgentRunResult(status="blocked", final_text="needs a decision")
    fixture = FixtureRunAgent(canned)

    result = await fixture(ctx=object())

    assert result is canned


async def test_fixture_defaults_to_success() -> None:
    result = await FixtureRunAgent()(ctx=object())
    assert result.status == "success"


async def test_fixture_records_calls_for_assertions() -> None:
    fixture = FixtureRunAgent()
    ctx_a, ctx_b = object(), object()

    await fixture(ctx_a)
    await fixture(ctx_b)

    assert fixture.calls == [ctx_a, ctx_b]


def test_fixture_satisfies_the_run_agent_protocol() -> None:
    assert isinstance(FixtureRunAgent(), RunAgent)
