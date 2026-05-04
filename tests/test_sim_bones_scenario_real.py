"""Real-mode bones scenario wiring (Track H, bones-done milestone).

These tests exercise the real-mode dispatch path WITHOUT actually
spending LLM tokens — the SDK's ``query`` is monkeypatched to yield
canned ``ResultMessage`` events. They guard the wiring shape:

* ``Driver(real_mode=True)`` routes the dev step through the
  orchestrator-spawn path (``_handle_real_dev_dispatch``).
* The CLI's ``--real`` flag accepts the flag, prompts, and instantiates
  a real-mode Driver instead of erroring out.
* Cost aggregation reads ``AgentCompleted.cost_estimate_usd`` from the
  analytics store after the dev step finishes.

Skipped by default — opt in with ``uv run pytest -m real`` or
``--run-real``. Skipping happens at collection time via
``tests/conftest.py``.

To run the FULL real-mode invocation against the real Claude API
(operator-only — costs money, takes minutes):

    export CLAUDE_CODE_OAUTH_TOKEN=...   # `claude setup-token` to get one
    uv run jig sim run \\
        tests/scenarios/bones-walking-skeleton.scenario.yaml \\
        --real --yes

Bones cost target: < $1, runtime < 10 min
(see ``docs/v2.0/implementation/v2-plan.md`` §"Milestones / gates").
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.sim.cli import sim
from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario

SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-walking-skeleton.scenario.yaml"
)


def _seed_repo(root: Path) -> None:
    """Initialize a fixture repo so the orchestrator's worktree path
    has a base branch to fork from. Mirrors the CLI's _seed_repo."""
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    for k, v in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", k, v], cwd=root, check=True, capture_output=True
        )
    (root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=root, check=True, capture_output=True
    )


def _install_fake_sdk_query(monkeypatch: pytest.MonkeyPatch, cost_usd: float) -> None:
    """Replace the SDK's ``query`` with a tiny canned stream.

    The fake yields one assistant text block + one ResultMessage with
    ``total_cost_usd=cost_usd``, then exits. The orchestrator's
    ``_run_agent_with_analytics`` wrap emits the ``AgentCompleted``
    analytics event regardless of what the SDK returned, so the cost
    aggregation path runs end-to-end.

    Note: the orchestrator's bones glue does NOT yet wire
    ``RunAgentResult.cost`` into the AgentCompleted event — the
    ``cost_estimate_usd`` field stays None unless we set it explicitly.
    For real-mode wiring tests we focus on the SHAPE: that the
    orchestrator path runs, the ticket terminates, and the cost
    aggregation function executes without error. Real cost capture is
    a separate (out-of-scope-for-this-test) wiring item the operator's
    real-mode invocation will validate end-to-end.
    """
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
    )

    async def fake_query(*, prompt, options, transport=None):
        # Drain at least one prompt message so the stream task doesn't
        # deadlock; the orchestrator yields the initial prompt then
        # waits for terminal status from the bus.
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[TextBlock(text="acknowledged real-mode bones dispatch")],
            model="claude-test",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="real-mode-test",
            total_cost_usd=cost_usd,
            usage={},
            result="real-mode dev finished",
        )

    from jig import agent as agent_module

    monkeypatch.setattr(agent_module, "query", fake_query)


def _stub_orchestrator_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the orchestrator's post-completion side-effects.

    The real ``_on_ticket_completed`` runs ``merge_ticket`` +
    ``remove_worktree`` against the host project; in a real-mode
    wiring test the worktree is a real path under tmp_path but those
    helpers expect a fully-configured git project (with the
    materialized ticket's branch present and mergeable). We patch
    them to no-op so the test focuses on the dispatch + agent-spawn
    path, not the post-merge git plumbing.

    Also patch ``_ensure_worktree`` so the test doesn't depend on the
    full ``create_worktree`` git plumbing — we still want the
    orchestrator to think the worktree exists, which is a
    cheap pre-existing dir.
    """
    async def fake_merge(*args, **kwargs):
        return "stub-merge-real"

    async def fake_remove(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.merge_ticket", fake_merge)
    monkeypatch.setattr("jig.worktree.remove_worktree", fake_remove)


def _stub_ensure_worktree(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    """Stub ``Orchestrator._ensure_worktree`` to return a pre-made dir.

    Mirrors ``tests/_phase5p_helpers.build_orch``. The real
    ``create_worktree`` runs ``git worktree add``, which works against
    our seeded repo but adds setup cost without exercising real-mode
    wiring. The dir returned is real on disk so the agent's cwd is
    valid.
    """
    from jig.orchestrator import Orchestrator

    worktree_dir = project_root / "real-mode-worktree"
    worktree_dir.mkdir(exist_ok=True)
    # Initialize so subprocess.run("git", "diff", ...) inside the
    # orchestrator/agent path doesn't blow up — though the
    # monkeypatched SDK doesn't actually run anything.
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=worktree_dir,
        check=True,
        capture_output=True,
    )

    async def fake_ensure(self, ticket):
        return worktree_dir

    monkeypatch.setattr(Orchestrator, "_ensure_worktree", fake_ensure)


# ---- Driver-level wiring -------------------------------------------------


@pytest.mark.real
def test_driver_real_mode_flag_routes_dev_to_orchestrator_handler():
    """``Driver(real_mode=True)`` swaps the dev handler, not just a flag.

    Smoke test that doesn't run anything — confirms the constructor's
    handler-table swap so a regression here surfaces fast.
    """
    from jig.sim.driver import (
        _handle_mock_dev_commit,
        _handle_real_dev_dispatch,
    )
    from jig.sim.scenario import StepKind

    mock_driver = Driver(real_mode=False)
    real_driver = Driver(real_mode=True)

    assert mock_driver.real_mode is False
    assert real_driver.real_mode is True
    assert mock_driver._handlers[StepKind.MOCK_DEV_COMMIT.value] is _handle_mock_dev_commit
    assert real_driver._handlers[StepKind.MOCK_DEV_COMMIT.value] is _handle_real_dev_dispatch


@pytest.mark.real
@pytest.mark.asyncio
async def test_real_mode_dev_dispatch_invokes_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Real-mode dev dispatch boots the orchestrator and waits for terminal.

    Uses the canned-SDK monkeypatch so no LLM cost. We exercise just
    the wiring shape — bootstrap → orchestrator startup → dispatch →
    terminal → cost aggregation — by running a slim scenario that
    stops at the dev step. The canned SDK doesn't produce a commit so
    we skip the reviewer step (which would correctly flag empty-diff
    against the bones reviewer); the FULL real-mode invocation
    against the live SDK is the operator's call (see module docstring).

    Verifies:
      * a ``Project``/workflow/role config gets bootstrapped on
        ctx.project_root,
      * the orchestrator dispatches the materialized ticket,
      * the ticket reaches a terminal status (resolved via the
        orchestrator's completion path),
      * AgentSpawned + AgentCompleted analytics events land.
    """
    from jig.sim.driver import _aggregate_agent_cost  # noqa: F401  (smoke import)
    from jig.sim.scenario import Scenario, ScenarioStep, StepKind
    from jig.sim.assertions import (
        AnalyticsEventEmittedAssertion,
        TicketStatusAssertion,
    )

    _seed_repo(tmp_path)
    _install_fake_sdk_query(monkeypatch, cost_usd=0.05)
    _stub_ensure_worktree(monkeypatch, tmp_path)
    _stub_orchestrator_completion(monkeypatch)

    full_scenario = load_scenario(SCENARIO_PATH)
    # Run every step UP TO the dev dispatch, then drop the reviewer
    # step (canned SDK produces no commit, so a real reviewer would
    # correctly fail; that's not what this test is gating).
    steps_through_dev: list[ScenarioStep] = []
    for s in full_scenario.steps:
        steps_through_dev.append(s)
        if s.kind == StepKind.MOCK_DEV_COMMIT.value:
            break
    slim_scenario = Scenario(
        spec_version=full_scenario.spec_version,
        id=full_scenario.id + "-real-wiring",
        description="real-mode wiring shape (skips reviewer)",
        persona=full_scenario.persona,
        estimated_cost_usd_max=full_scenario.estimated_cost_usd_max,
        steps=steps_through_dev,
        final_assertions=[
            TicketStatusAssertion(
                ticket_id="tb-catalog-ingest", status="resolved"
            ),
            AnalyticsEventEmittedAssertion(event_kind="agent_spawned"),
            AnalyticsEventEmittedAssertion(event_kind="agent_completed"),
        ],
    )

    driver = Driver(real_mode=True)
    report = await driver.run(slim_scenario, project_root=tmp_path)

    assert report.passed, report.failure_summary()

    # Bootstrap landed.
    assert (tmp_path / ".jig" / "config.yaml").is_file()
    assert (tmp_path / ".jig" / "workflows" / "default.yaml").is_file()
    assert (tmp_path / ".jig" / "roles" / "dev.yaml").is_file()

    # Spawn/complete events are how the bones-done gate measures cost +
    # latency, so guard their presence explicitly.
    spawn_events = [
        e for e in report.captured_events if e.kind == "agent_spawned"
    ]
    complete_events = [
        e for e in report.captured_events if e.kind == "agent_completed"
    ]
    assert spawn_events, "expected at least one AgentSpawned event in real mode"
    assert complete_events, "expected at least one AgentCompleted event in real mode"


@pytest.mark.real
@pytest.mark.asyncio
async def test_real_mode_aggregates_cost_into_ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cost aggregation sums AgentCompleted.cost_estimate_usd across run.

    The orchestrator's bones-tier wiring does NOT yet stamp the SDK's
    total_cost_usd onto the AgentCompleted event (that's an MVP item),
    so we synthesize one event ourselves into the analytics store and
    verify the aggregator picks it up. This covers the aggregator
    contract independent of the SDK→event plumbing.
    """
    from jig.analytics.events import AgentCompleted
    from jig.sim.driver import _aggregate_agent_cost, _isolated_run

    _seed_repo(tmp_path)
    async with _isolated_run(tmp_path) as ctx:
        await ctx.analytics.append(
            AgentCompleted(
                agent_id="dev:abc",
                status="success",
                duration_ms=1234,
                cost_estimate_usd=0.42,
                simulator=True,
            )
        )
        await ctx.analytics.append(
            AgentCompleted(
                agent_id="dev:def",
                status="success",
                duration_ms=10,
                cost_estimate_usd=0.13,
                simulator=True,
            )
        )
        # ``None`` cost (SDK didn't report) treated as 0.0 — the
        # aggregator tolerates partial data without poisoning the sum.
        await ctx.analytics.append(
            AgentCompleted(
                agent_id="dev:ghi",
                status="success",
                duration_ms=10,
                cost_estimate_usd=None,
                simulator=True,
            )
        )

        cost = await _aggregate_agent_cost(ctx)
        assert cost == pytest.approx(0.55)


# ---- CLI wiring ----------------------------------------------------------


@pytest.mark.real
def test_cli_real_flag_no_longer_rejects():
    """``jig sim run --real`` instantiates a real Driver instead of erroring.

    Pre-fix, the CLI raised ``ClickException`` whenever ``--real`` was
    passed. We assert the new behavior by patching ``Driver.run`` to
    return an empty pass report so the CLI exits cleanly.
    """
    from jig.sim import cli as sim_cli
    from jig.sim.driver import ScenarioReport

    captured = {}

    class _FakeDriver:
        def __init__(self, *, real_mode: bool = False) -> None:
            captured["real_mode"] = real_mode

        async def run(self, scenario, *, project_root):
            captured["scenario_id"] = scenario.id
            return ScenarioReport(scenario_id=scenario.id)

    runner = CliRunner()
    with runner.isolated_filesystem():
        scenario_yaml = SCENARIO_PATH.read_text()
        Path("scn.yaml").write_text(scenario_yaml)
        # Monkeypatch Driver inside the CLI module without using the
        # pytest.MonkeyPatch fixture (CliRunner has its own scope).
        original = sim_cli.Driver
        sim_cli.Driver = _FakeDriver  # type: ignore[assignment]
        try:
            result = runner.invoke(sim, ["run", "scn.yaml", "--real"])
        finally:
            sim_cli.Driver = original  # type: ignore[assignment]

    assert result.exit_code == 0, result.output
    assert captured.get("real_mode") is True
    assert captured.get("scenario_id") == "bones-walking-skeleton"


@pytest.mark.real
def test_cli_real_flag_does_not_prompt_under_pytest():
    """The confirmation prompt is skipped when PYTEST_CURRENT_TEST is set.

    Test-time invocation must not deadlock on stdin. The CLI's
    ``_under_pytest`` helper detects pytest via the env var.
    """
    from jig.sim import cli as sim_cli
    from jig.sim.driver import ScenarioReport

    class _FakeDriver:
        def __init__(self, *, real_mode: bool = False) -> None:
            pass

        async def run(self, scenario, *, project_root):
            return ScenarioReport(scenario_id=scenario.id)

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("scn.yaml").write_text(SCENARIO_PATH.read_text())
        original = sim_cli.Driver
        sim_cli.Driver = _FakeDriver  # type: ignore[assignment]
        try:
            # No `input=` provided — would hang if the prompt fired.
            result = runner.invoke(sim, ["run", "scn.yaml", "--real"])
        finally:
            sim_cli.Driver = original  # type: ignore[assignment]

    assert result.exit_code == 0, result.output


@pytest.mark.real
def test_cli_mock_mode_remains_default_and_does_not_prompt():
    """Default invocation (no --real) keeps the existing zero-cost path."""
    from jig.sim import cli as sim_cli

    captured: dict = {}

    class _FakeDriver:
        def __init__(self, *, real_mode: bool = False) -> None:
            captured["real_mode"] = real_mode

        async def run(self, scenario, *, project_root):
            from jig.sim.driver import ScenarioReport

            return ScenarioReport(scenario_id=scenario.id)

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("scn.yaml").write_text(SCENARIO_PATH.read_text())
        original = sim_cli.Driver
        sim_cli.Driver = _FakeDriver  # type: ignore[assignment]
        try:
            result = runner.invoke(sim, ["run", "scn.yaml"])
        finally:
            sim_cli.Driver = original  # type: ignore[assignment]

    assert result.exit_code == 0, result.output
    assert captured.get("real_mode") is False
