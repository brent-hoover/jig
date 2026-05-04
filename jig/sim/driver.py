"""Synthetic operator driver (Track H2+H6, bones).

Loads a scenario, sets up per-run isolation, dispatches each scripted
step against the real v2 helpers, captures outcomes, and evaluates
assertions. Bones runs in mock mode by default — the dev step is a
deterministic helper that produces a small commit satisfying the
contract-compliance reviewer's bones checks. No LLM calls in mock mode.

Per-run isolation: each ``Driver.run`` rooted at the caller-supplied
``project_root`` (a ``tmp_path`` in tests, an operator-supplied tmpdir
in CLI mode). The driver creates a fresh ``.jig/store/`` tree, flips
``JIG_SIMULATOR=true`` for the duration of the run so analytics events
get tagged ``simulator: true`` (per ``jig.analytics.emitter`` already
wired), and restores the prior env on exit so concurrent test runs
don't leak.

Real mode (``Driver(real_mode=True)``): the ``mock_dev_commit`` step is
re-routed to ``_handle_real_dev_dispatch``, which boots an
``Orchestrator`` rooted at ``ctx.project_root``, lets its dispatch loop
pick up the materialized ticket, and waits for a terminal status before
shutting down. After the dev step completes the driver aggregates
``AgentCompleted.cost_estimate_usd`` from the analytics store into
``ctx.cost_usd`` so the ``cost_under_budget`` assertion gates real
spend. The orchestrator path uses the existing ``run_agent`` glue —
the SDK invocation is the same path production uses, just rooted at
the simulator's tmp dir. Operators opt in via ``jig sim run --real``
(see ``jig.sim.cli``).
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import AnalyticsEvent
from jig.analytics.store import AnalyticsStore
from jig.atomic import atomic_write_text
from jig.coordinator import Coordinator
from jig.planner_pm_mcp import PLANNER_TICKET_ID, handle_plan_finalize
from jig.po_l0_mcp import handle_l0_finalize
from jig.po_l1_mcp import L1_TICKET_ID, handle_discovery_finalize
from jig.po_l2_mcp import L2_TICKET_ID, handle_l2_finalize
from jig.po_l3_mcp import handle_l3_finalize
from jig.reviewers import ContractComplianceReviewer, ReviewerComment
from jig.sa_incremental_mcp import (
    handle_arch_complete_spike,
    handle_arch_finalize,
    handle_arch_propose_spike,
    handle_arch_set_cross_cutting_policy,
    handle_arch_set_data_store,
    handle_arch_set_module,
    handle_arch_set_open_question,
    handle_arch_set_risk,
    handle_arch_set_shared_contract,
    handle_module_set_behavioral_contract,
    handle_module_set_data_contract,
    handle_module_set_external_dependency,
    handle_module_set_integration_ac,
    handle_module_set_open_question,
    handle_module_set_owned_collection,
)
from jig.sa_mcp import SA_TICKET_ID
from jig.schemas.arch import Architecture, ContractsFile
from jig.schemas.plan import BuildPlan
from jig.schemas.po import SuitesIndex
from jig.vd_mcp import VD_TICKET_ID, handle_vd_finalize
from jig.sim.assertions import (
    AnalyticsEventEmittedAssertion,
    ArtifactWrittenAssertion,
    CostUnderBudgetAssertion,
    ReviewerReturnedNoCriticalAssertion,
    TicketStatusAssertion,
)
from jig.sim.scenario import Scenario, ScenarioStep, StepKind
from jig.spec_loader import (
    architecture_path,
    module_contracts_path,
    suites_index_path,
    write_build_plan,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore

# Re-export TicketStateChanged for the status-change callback.
from jig.analytics.events import TicketStateChanged
from jig.ticket import Ticket, WorkType

__all__ = [
    "AssertionResult",
    "Driver",
    "DriverContext",
    "ScenarioReport",
    "StepOutcome",
    "TUI_TRACE_RELPATH",
    "TuiDriverAdapter",
]


# ---- result dataclasses --------------------------------------------------


@dataclass
class AssertionResult:
    """Outcome of one assertion check."""

    kind: str
    passed: bool
    detail: str  # short prose explaining the result; surfaces on failure


@dataclass
class StepOutcome:
    """Per-step record: what ran, what assertions checked, did they pass."""

    kind: str
    error: str | None = None  # set if the step itself raised
    assertions: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.error is None and all(a.passed for a in self.assertions)


@dataclass
class ScenarioReport:
    """Driver run report — what the synthetic operator returns."""

    scenario_id: str
    step_outcomes: list[StepOutcome] = field(default_factory=list)
    final_assertion_results: list[AssertionResult] = field(default_factory=list)
    captured_events: list[AnalyticsEvent] = field(default_factory=list)
    captured_reviewer_comments: dict[str, list[ReviewerComment]] = field(
        default_factory=dict,
    )
    cost_usd: float = 0.0  # mock mode: 0.0; real mode: aggregated AgentCompleted.cost
    # Track H Final — policy-driven turns sampled for this run. Empty
    # for scripted scenarios. Each entry is a ``jig.sim.policy.PolicyTurn``.
    policy_turns: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            all(s.passed for s in self.step_outcomes)
            and all(a.passed for a in self.final_assertion_results)
        )

    def failed_assertions(self) -> list[AssertionResult]:
        out: list[AssertionResult] = []
        for s in self.step_outcomes:
            out.extend(a for a in s.assertions if not a.passed)
        out.extend(a for a in self.final_assertion_results if not a.passed)
        return out

    def failure_summary(self) -> str:
        """Multi-line summary of every failure — for pytest assertion msg."""
        lines: list[str] = [f"scenario {self.scenario_id} FAILED:"]
        for s in self.step_outcomes:
            if s.error is not None:
                lines.append(f"  step {s.kind}: ERROR {s.error}")
            for a in s.assertions:
                if not a.passed:
                    lines.append(f"  step {s.kind}: {a.kind} → {a.detail}")
        for a in self.final_assertion_results:
            if not a.passed:
                lines.append(f"  final: {a.kind} → {a.detail}")
        return "\n".join(lines)


# ---- driver context (per-run state) --------------------------------------


@dataclass
class DriverContext:
    """Wired stores + emitter for one scenario run.

    Created fresh per ``Driver.run`` — no cross-run state. The
    ``reviewer_comments`` map captures the last-invocation comment list
    per reviewer id so the ``reviewer_returned_no_critical`` assertion
    can read it without re-running the reviewer.
    """

    project_root: Path
    tickets: TicketStore
    threads: ThreadStore
    bus: MessageBus
    analytics: AnalyticsStore
    emitter: EventEmitter
    reviewer_comments: dict[str, list[ReviewerComment]] = field(default_factory=dict)
    cost_usd: float = 0.0
    # Track E MVP — captured SQL fired by dev-provisioning steps. Tests
    # introspect this list; production drivers leave it empty.
    dev_provisioning_sql: list[str] = field(default_factory=list)
    dev_provisioning_env_vars: dict[str, str] = field(default_factory=dict)
    # Track I Final — last computed quartermaster calibration (after
    # invoke_quartermaster_feedback). Scenario assertions read off
    # this so the calibration shape doesn't have to leak into
    # ArtifactWrittenAssertion.
    quartermaster_calibration: dict[str, int] = field(default_factory=dict)
    # Track G Final — selection result from the most recent
    # invoke_specialty_reviewer step. Scenario assertions inspect the
    # list directly to verify the dispatcher picked the requested
    # reviewer (mock-mode only — no LLM agent spawned).
    specialty_reviewer_selection: list[str] = field(default_factory=list)
    # Track G Final — disposition counts from the most recent
    # invoke_severity_disposition step. Three integers (blocked /
    # consulted-SA / deferred) so scenario assertions can introspect
    # without re-loading the disposition module.
    last_disposition_blocked: int = 0
    last_disposition_consulted_sa: int = 0
    last_disposition_deferred: int = 0
    # Track E Final — captured outputs from dev-ephemeral / fixture
    # steps. Tests + scenario assertions read these to verify the
    # per_agent_ephemeral lifecycle + the recorded-fixture replay
    # produced the expected URL / response body.
    last_ephemeral_url: str | None = None
    last_ephemeral_path: str | None = None
    last_fixture_response: dict | None = None
    # Track C Final — captured outputs from cascade-mitigation steps.
    # ``last_cascade_id`` carries the id of the cascade most recently
    # acted on; ``last_cascade_action`` names the action ("rejected",
    # "staged", "resolved"); ``last_cascade_stage_count`` records how
    # many stages were produced. ``last_next_layer`` carries the name
    # the Coordinator returned from the override handler so a
    # scenario can introspect.
    last_cascade_id: str | None = None
    last_cascade_action: str | None = None
    last_cascade_stage_count: int = 0
    last_next_layer: str | None = None
    # Track H Final — policy-driven turn record. Populated when the
    # active scenario has ``policy_driven: true``. Each per-step
    # response sampled via ``jig.sim.policy.apply_policy`` lands here
    # so a scenario / test can audit the deterministic outputs.
    policy_turns: list = field(default_factory=list)
    # The persona Pydantic model loaded for the current scenario. Set
    # by the driver before any step handler runs; ``None`` outside of
    # a scenario run. Carries the response_templates the policy module
    # samples from.
    persona: object | None = None
    # The scenario seed for the current run (reproducibility for
    # policy-driven turns). ``None`` for scripted scenarios.
    scenario_seed: int | None = None
    # Track H Final — TUI-driving mode marker. When non-None, step
    # handlers that mutate state should record the intended TUI
    # interaction in the trace log + tag analytics events with this
    # value so the report can distinguish daemon-API steps from
    # would-be-TUI steps.
    tui_via_tag: str | None = None
    # Track F Final — captured outputs from tier-promotion + calibration
    # sim steps. ``last_tier_promotion`` carries the from/to tier rung
    # the promotion handler just resolved; ``last_calibration_sample``
    # carries the synthesized sample id for assertion-time
    # introspection.
    last_tier_promotion_from: str | None = None
    last_tier_promotion_to: str | None = None
    last_calibration_sample_size: str | None = None
    # Track D Final — captured outputs from vision-diff / accessibility /
    # responsive steps. ``last_vision_diff_count`` records the number of
    # vision-diff comments (so a scenario can assert "got the canned
    # diffs back"); ``last_a11y_rule_ids`` + ``last_responsive_breakpoints``
    # carry the rule labels / breakpoint hits for fine-grained checks.
    last_vision_diff_count: int = 0
    last_a11y_rule_ids: list[str] = field(default_factory=list)
    last_responsive_breakpoints: list[str] = field(default_factory=list)


# ---- step handler signature ---------------------------------------------


# Step handlers receive (ctx, step) and may run async work. Built-in
# handlers cover the bones StepKind values; tests can register custom
# handlers via ``Driver.register_step_handler`` for synthetic step
# kinds (see test_driver_sets_simulator_env_var_during_run).
StepHandler = Callable[
    ["DriverContext", ScenarioStep],
    Awaitable[None],
]


# ---- TUI-driving mode (Track H Final hook) ------------------------------


# Trace log relpath for the TUI-driving stub. Lives under ``.jig/sim/``
# so all simulator-owned state nests cleanly. JSONL semantics — one row
# per recorded TUI interaction.
TUI_TRACE_RELPATH = Path(".jig") / "sim" / "tui-trace.jsonl"


class TuiDriverAdapter:
    """Thin TUI-driving adapter (Track H Final stub).

    For Final scope this is a stub — actual TUI driving lands with the
    TUI track. The stub records the intended TUI interactions
    structurally to ``.jig/sim/tui-trace.jsonl`` so a scenario
    asserting that step X "would have been a TUI interaction" can
    introspect the trace. Dispatch still goes through the same
    MCP-direct handlers; nothing actually drives a TUI.

    Real TUI driving (spawning the textual app, sending keystrokes,
    asserting on screen state) is a v2.x extension.
    """

    def __init__(self, *, project_root: Path) -> None:
        self._project_root = project_root
        self._path = project_root / TUI_TRACE_RELPATH
        self._started = False

    @property
    def path(self) -> Path:
        return self._path

    async def start(self) -> None:
        """Initialize the trace log file (truncate any prior contents)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate so each run starts clean — the trace is run-scoped,
        # not append-forever like the analytics store.
        self._path.write_text("")
        self._started = True

    async def record(
        self,
        *,
        step_kind: str,
        params: dict,
        tag: str,
    ) -> None:
        """Record one intended TUI interaction.

        Format is one JSON object per line (JSONL) so downstream tools
        can stream-parse without loading the whole file.
        """
        import json

        if not self._started:
            await self.start()
        row = {
            "step_kind": step_kind,
            "params": params,
            "tui_via": tag,
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    async def stop(self) -> None:
        """No-op for the stub. Real adapter would tear down the TUI process."""
        return None


# ---- driver -------------------------------------------------------------


class Driver:
    """The bones synthetic operator driver.

    Instantiate once per scenario library; call ``run(scenario,
    project_root)`` per execution. Stateless across runs — each ``run``
    builds its own ``DriverContext`` against a fresh tmp dir.

    ``real_mode`` swaps the dev-step handler from the deterministic
    mock helper (``_handle_mock_dev_commit``) to the orchestrator-spawn
    path (``_handle_real_dev_dispatch``). All other steps are mode-
    agnostic — only dev dispatch differs because that's the only step
    that, in real mode, would invoke an LLM agent. Defaults to mock so
    test suites and CI never accidentally cost money.
    """

    def __init__(
        self, *, real_mode: bool = False, tui_mode: bool = False
    ) -> None:
        self._real_mode = real_mode
        self._tui_mode = tui_mode
        self._tui_adapter: TuiDriverAdapter | None = None
        dev_handler: StepHandler = (
            _handle_real_dev_dispatch if real_mode else _handle_mock_dev_commit
        )
        self._handlers: dict[str, StepHandler] = {
            StepKind.INVOKE_L0_FINALIZE.value: _handle_l0_finalize,
            StepKind.INVOKE_L1_FINALIZE.value: _handle_invoke_l1_finalize,
            StepKind.INVOKE_L2_FINALIZE.value: _handle_invoke_l2_finalize,
            StepKind.INVOKE_L3_FINALIZE.value: _handle_l3_finalize,
            StepKind.WRITE_SUITES_YAML.value: _handle_write_suites_yaml,
            StepKind.WRITE_ARCHITECTURE.value: _handle_write_architecture,
            StepKind.WRITE_MODULE_CONTRACTS.value: _handle_write_module_contracts,
            StepKind.WRITE_BUILD_PLAN.value: _handle_write_build_plan,
            StepKind.INVOKE_SA_INCREMENTAL.value: _handle_invoke_sa_incremental,
            StepKind.INVOKE_RISK_AND_SPIKE.value: _handle_invoke_risk_and_spike,
            StepKind.INVOKE_PLAN_FINALIZE.value: _handle_invoke_plan_finalize,
            StepKind.MATERIALIZE_TICKETS.value: _handle_materialize_tickets,
            StepKind.INVOKE_COORDINATOR_CYCLE.value: _handle_invoke_coordinator_cycle,
            StepKind.DEFER_TICKET.value: _handle_defer_ticket,
            StepKind.TRIAGE_DEFERRED.value: _handle_triage_deferred,
            StepKind.MOCK_DEV_COMMIT.value: dev_handler,
            StepKind.RUN_REVIEWER.value: _handle_run_reviewer,
            StepKind.INVOKE_DEV_PROVISIONING.value: (
                _handle_invoke_dev_provisioning
            ),
            StepKind.INVOKE_DEV_EPHEMERAL.value: (
                _handle_invoke_dev_ephemeral
            ),
            StepKind.INVOKE_FIXTURE_REPLAY.value: (
                _handle_invoke_fixture_replay
            ),
            StepKind.INVOKE_VD_FINALIZE.value: _handle_invoke_vd_finalize,
            StepKind.INVOKE_QUARTERMASTER_FEEDBACK.value: (
                _handle_invoke_quartermaster_feedback
            ),
            StepKind.INVOKE_SPECIALTY_REVIEWER.value: (
                _handle_invoke_specialty_reviewer
            ),
            StepKind.INVOKE_SEVERITY_DISPOSITION.value: (
                _handle_invoke_severity_disposition
            ),
            StepKind.INVOKE_CASCADE_REJECT.value: (
                _handle_invoke_cascade_reject
            ),
            StepKind.INVOKE_CASCADE_STAGE.value: (
                _handle_invoke_cascade_stage
            ),
            StepKind.INVOKE_CASCADE_RISK_LOW.value: (
                _handle_invoke_cascade_risk_low
            ),
            StepKind.INVOKE_TIER_PROMOTION.value: (
                _handle_invoke_tier_promotion
            ),
            StepKind.INVOKE_CALIBRATION_RECORD.value: (
                _handle_invoke_calibration_record
            ),
            StepKind.INVOKE_VISION_DIFF.value: (
                _handle_invoke_vision_diff
            ),
            StepKind.INVOKE_ACCESSIBILITY_REVIEW.value: (
                _handle_invoke_accessibility_review
            ),
            StepKind.INVOKE_RESPONSIVE_REVIEW.value: (
                _handle_invoke_responsive_review
            ),
        }

    @property
    def real_mode(self) -> bool:
        """True when this driver routes the dev step to the real Claude path."""
        return self._real_mode

    @property
    def tui_mode(self) -> bool:
        """True when step invocations route through the TUI driver adapter (stub)."""
        return self._tui_mode

    def register_step_handler(
        self, kind: str, handler: StepHandler
    ) -> None:
        """Register an extra step handler — used by tests for synthetic kinds."""
        self._handlers[kind] = handler

    async def run(
        self, scenario: Scenario, *, project_root: Path
    ) -> ScenarioReport:
        report = ScenarioReport(scenario_id=scenario.id)
        async with _isolated_run(project_root) as ctx:
            # Track H Final — policy-driven scenarios pre-load the
            # persona + scenario seed onto the context so any handler
            # that wants to sample a response has the inputs in scope.
            if scenario.policy_driven:
                from jig.sim.persona import load_persona, persona_path
                from jig.sim.policy import apply_policy

                ctx.persona = load_persona(persona_path(scenario.persona))
                ctx.scenario_seed = scenario.scenario_seed
                # Eagerly sample one turn per step kind so the
                # ``policy_turns`` log captures the sequence the
                # scenario "intended" without each handler needing to
                # opt in. Future LLM-driven turns would replace this.
                for step in scenario.steps:
                    turn = apply_policy(
                        ctx.persona,
                        prompt=str(step.params)[:200],
                        prompt_kind="confirm_gate",
                        scenario_seed=scenario.scenario_seed,
                    )
                    ctx.policy_turns.append(turn)

            # Track H Final — TUI-driving mode hook. When the driver
            # was constructed with tui_mode=True we tag the context so
            # step handlers can mark their writes as "would have been
            # a TUI interaction" and the trace log records the intent.
            if self._tui_mode:
                ctx.tui_via_tag = "tui"
                self._tui_adapter = TuiDriverAdapter(
                    project_root=project_root
                )
                await self._tui_adapter.start()

            for step in scenario.steps:
                # Surface the actual handler name in real mode so the
                # report doesn't lie ("mock_dev_commit" PASS during a
                # real-mode run is misleading). Mode-aware label only;
                # the YAML kind stays the same.
                display_kind = step.kind
                if self._real_mode and step.kind == StepKind.MOCK_DEV_COMMIT.value:
                    display_kind = "real_dev_dispatch"
                outcome = StepOutcome(kind=display_kind)
                handler = self._handlers.get(step.kind)
                if handler is None:
                    outcome.error = f"no handler registered for kind {step.kind!r}"
                else:
                    # Track H Final — tui_mode: record the intended TUI
                    # interaction before dispatching the (still
                    # MCP-direct, stub) handler. Real TUI driving lands
                    # with the TUI track Final.
                    if self._tui_mode and self._tui_adapter is not None:
                        await self._tui_adapter.record(
                            step_kind=step.kind,
                            params=step.params,
                            tag=ctx.tui_via_tag or "tui",
                        )
                    try:
                        await handler(ctx, step)
                    except Exception as e:
                        outcome.error = f"{type(e).__name__}: {e}"
                if outcome.error is None:
                    for a in step.assertions:
                        outcome.assertions.append(
                            await _evaluate_assertion(ctx, a)
                        )
                report.step_outcomes.append(outcome)
            # Cost aggregation: drain pending writes so AgentCompleted
            # events the orchestrator emit_nowait'd land in the store
            # before we sum. Mock mode emits no AgentCompleted events,
            # so this resolves to 0.0 — consistent with the existing
            # mock-mode contract.
            await ctx.emitter.drain()
            ctx.cost_usd = await _aggregate_agent_cost(ctx)
            for a in scenario.final_assertions:
                report.final_assertion_results.append(
                    await _evaluate_assertion(ctx, a)
                )
            report.captured_events = await ctx.analytics.all()
            report.captured_reviewer_comments = dict(ctx.reviewer_comments)
            report.cost_usd = ctx.cost_usd
            report.policy_turns = list(ctx.policy_turns)
            if self._tui_mode and self._tui_adapter is not None:
                await self._tui_adapter.stop()
        return report


# ---- per-run isolation context -----------------------------------------


@asynccontextmanager
async def _isolated_run(project_root: Path):
    """Set up + tear down a per-run ``DriverContext``.

    Flips ``JIG_SIMULATOR=true`` on entry so events get tagged
    (per ``jig.analytics.emitter``); restores prior value on exit so
    concurrent tests don't see leakage. We never delete ``project_root``
    — pytest's ``tmp_path`` does that for tests; the CLI uses its own
    tempdir lifecycle (see ``jig.sim.cli``).
    """
    store_dir = project_root / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    spec_dir = project_root / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)

    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    bus = MessageBus(store_dir / "messages.jsonl")
    analytics = AnalyticsStore(store_dir / "analytics.jsonl")
    for s in (tickets, threads, bus, analytics):
        await s.load()

    emitter = EventEmitter(analytics, simulator_mode=True)

    # Wire the status-change callback so coordinator-materialized tickets
    # emit TicketStateChanged events (mirrors orchestrator's wiring in
    # jig.orchestrator.Orchestrator.startup).
    def _on_status_change(
        ticket_id: str, from_state: str | None, to_state: str
    ) -> None:
        emitter.emit_nowait(
            TicketStateChanged(
                ticket_id=ticket_id,
                from_state=from_state,
                to_state=to_state,
            )
        )

    tickets.set_status_change_callback(_on_status_change)

    ctx = DriverContext(
        project_root=project_root,
        tickets=tickets,
        threads=threads,
        bus=bus,
        analytics=analytics,
        emitter=emitter,
    )

    prior = os.environ.get("JIG_SIMULATOR")
    os.environ["JIG_SIMULATOR"] = "true"
    try:
        yield ctx
    finally:
        if prior is None:
            os.environ.pop("JIG_SIMULATOR", None)
        else:
            os.environ["JIG_SIMULATOR"] = prior


# ---- step handlers ------------------------------------------------------


async def _handle_l0_finalize(ctx: DriverContext, step: ScenarioStep) -> None:
    """Invoke handle_l0_finalize. Auto-creates the project ticket if missing.

    The L0 helper assumes its ticket exists (resolve_after_handoff is a
    no-op if the ticket is missing, which would silently swallow the
    expected status transition); we create it to mirror the operator's
    /init flow.
    """
    if await ctx.tickets.get("project") is None:
        await ctx.tickets.create(
            Ticket(
                id="project",
                work_type=WorkType.BRIEF,
                title="L0 project pitch",
                created_by="sim-driver",
            )
        )
    params = dict(step.params)
    params.setdefault("author", "po-l0")
    await handle_l0_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        **params,
    )


async def _handle_invoke_l1_finalize(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Invoke handle_discovery_finalize. Auto-creates the discovery ticket.

    Bones scenarios hand-write suites.yaml directly and skip L1; MVP+
    scenarios use this step to exercise the L1 PO authoring path. The
    synthetic operator passes a complete ``DiscoveryDoc`` payload
    (personas / journeys / capability_roster) so the multi-turn
    conversation is collapsed to one call — the real LLM-driven walk
    only happens in real-mode runs (out of MVP scope).
    """
    if await ctx.tickets.get(L1_TICKET_ID) is None:
        await ctx.tickets.create(
            Ticket(
                id=L1_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="L1 discovery — personas + journeys",
                created_by="sim-driver",
            )
        )
    params = dict(step.params)
    params.setdefault("author", "po-l1")
    await handle_discovery_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        **params,
    )


async def _handle_invoke_l2_finalize(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Invoke handle_l2_finalize. Auto-creates the L2 suites ticket.

    Bones scenarios hand-write suites.yaml directly via
    ``write_suites_yaml`` and skip the L2 PO; MVP+ scenarios use this
    step to exercise the L2 PO authoring path. The synthetic operator
    passes a complete ``suites`` list (matching ``SuitesIndex.suites``)
    so the multi-turn proposing UX is collapsed to one call — the
    real LLM-driven walk only happens in real-mode runs (out of MVP
    scope).
    """
    if await ctx.tickets.get(L2_TICKET_ID) is None:
        await ctx.tickets.create(
            Ticket(
                id=L2_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="L2 suite organization",
                created_by="sim-driver",
            )
        )
    params = dict(step.params)
    params.setdefault("author", "po-l2")
    await handle_l2_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        **params,
    )


async def _handle_l3_finalize(ctx: DriverContext, step: ScenarioStep) -> None:
    """Invoke handle_l3_finalize. Auto-creates the suite ticket if missing."""
    suite_id = step.params["suite_id"]
    ticket_id = f"suite-{suite_id}"
    if await ctx.tickets.get(ticket_id) is None:
        await ctx.tickets.create(
            Ticket(
                id=ticket_id,
                work_type=WorkType.BRIEF,
                title=f"L3 brief — {suite_id}",
                created_by="sim-driver",
            )
        )
    params = dict(step.params)
    params.setdefault("author", "po-l3")
    await handle_l3_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        **params,
    )


async def _handle_write_suites_yaml(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Operator-hand-write of L2 ``.jig/spec/suites.yaml`` (bones)."""
    raw = step.params["suites_index"]
    # Validate via the schema so a malformed scenario fails before we
    # write garbage to disk.
    index = (
        raw
        if isinstance(raw, SuitesIndex)
        else SuitesIndex.model_validate(raw)
    )
    yaml_text = yaml.safe_dump(index.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(suites_index_path(ctx.project_root), yaml_text)


async def _handle_write_architecture(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Operator-hand-write of ``architecture.yaml`` per bones (skips SA agent)."""
    raw = step.params["architecture"]
    arch = (
        raw
        if isinstance(raw, Architecture)
        else Architecture.model_validate(raw)
    )
    yaml_text = yaml.safe_dump(arch.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(architecture_path(ctx.project_root), yaml_text)


async def _handle_write_module_contracts(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Operator-hand-write of one module's ``contracts.yaml`` per bones."""
    module_id = step.params["module_id"]
    raw = step.params["contracts"]
    contracts = (
        raw
        if isinstance(raw, ContractsFile)
        else ContractsFile.model_validate(raw)
    )
    yaml_text = yaml.safe_dump(
        contracts.model_dump(mode="json"), sort_keys=False
    )
    atomic_write_text(
        module_contracts_path(ctx.project_root, module_id), yaml_text
    )


async def _handle_write_build_plan(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Operator-hand-write of the build plan (bones has no Planner agent)."""
    raw = step.params["plan"]
    plan = raw if isinstance(raw, BuildPlan) else BuildPlan.model_validate(raw)
    write_build_plan(ctx.project_root, plan)


# Dispatch table mapping ``calls[*].tool`` to the matching incremental
# handler. Lives at module scope so the handler can stay flat — each
# entry is a thin wrapper that pulls the right kwargs out of the
# scenario YAML payload. New incremental upserts land here when the
# MCP layer adds them.
_SA_INCREMENTAL_HANDLERS = {
    "arch_set_module": lambda *, project_path, params: handle_arch_set_module(
        project_path=project_path, module=params["module"]
    ),
    "arch_set_data_store": lambda *, project_path, params: handle_arch_set_data_store(
        project_path=project_path, data_store=params["data_store"]
    ),
    "arch_set_shared_contract": lambda *, project_path, params: handle_arch_set_shared_contract(
        project_path=project_path, shared_contract=params["shared_contract"]
    ),
    "arch_set_cross_cutting_policy": lambda *, project_path, params: handle_arch_set_cross_cutting_policy(
        project_path=project_path, policy=params["policy"]
    ),
    "arch_set_open_question": lambda *, project_path, params: handle_arch_set_open_question(
        project_path=project_path, open_question=params["open_question"]
    ),
    "module_set_owned_collection": lambda *, project_path, params: handle_module_set_owned_collection(
        project_path=project_path,
        module_id=params["module_id"],
        owned_collection=params["owned_collection"],
    ),
    "module_set_external_dependency": lambda *, project_path, params: handle_module_set_external_dependency(
        project_path=project_path,
        module_id=params["module_id"],
        external_dependency=params["external_dependency"],
    ),
    "module_set_integration_ac": lambda *, project_path, params: handle_module_set_integration_ac(
        project_path=project_path,
        module_id=params["module_id"],
        integration_ac=params["integration_ac"],
    ),
    "module_set_behavioral_contract": lambda *, project_path, params: handle_module_set_behavioral_contract(
        project_path=project_path,
        module_id=params["module_id"],
        behavioral_contract=params["behavioral_contract"],
    ),
    "module_set_data_contract": lambda *, project_path, params: handle_module_set_data_contract(
        project_path=project_path,
        module_id=params["module_id"],
        data_contract=params["data_contract"],
    ),
    "module_set_open_question": lambda *, project_path, params: handle_module_set_open_question(
        project_path=project_path,
        module_id=params["module_id"],
        open_question=params["open_question"],
    ),
}


async def _handle_invoke_sa_incremental(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Drive a sequence of SA upsert calls + a final ``arch_finalize``.

    Scenario YAML shape::

        kind: invoke_sa_incremental
        params:
          calls:
            - tool: arch_set_module
              params: { module: { ... } }
            - tool: module_set_owned_collection
              params: { module_id: ..., owned_collection: { ... } }
            ...
          finalize:
            summary: "MVP-SA finalize"
            author: sa-mvp           # optional; defaults to sa-mvp

    Auto-creates the architecture ticket if missing (mirrors the L0 /
    L1 / L2 / L3 / planner finalize patterns — the finalize handler
    expects its ticket to exist or ``resolve_after_handoff`` no-ops
    silently).
    """
    if await ctx.tickets.get(SA_TICKET_ID) is None:
        await ctx.tickets.create(
            Ticket(
                id=SA_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="SA — architecture",
                created_by="sim-driver",
            )
        )

    calls = step.params.get("calls") or []
    for entry in calls:
        tool_name = entry["tool"]
        handler = _SA_INCREMENTAL_HANDLERS.get(tool_name)
        if handler is None:
            raise ValueError(
                f"invoke_sa_incremental: unknown tool {tool_name!r}; "
                f"known: {sorted(_SA_INCREMENTAL_HANDLERS)!r}"
            )
        await handler(
            project_path=ctx.project_root, params=entry.get("params", {})
        )

    finalize = dict(step.params.get("finalize") or {})
    finalize.setdefault("author", "sa-mvp")
    if "summary" not in finalize:
        raise ValueError(
            "invoke_sa_incremental: finalize.summary is required"
        )
    await handle_arch_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        summary=finalize["summary"],
        author=finalize["author"],
    )


async def _handle_invoke_risk_and_spike(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Drive the risk-register + spike + (optional) cascade workflow.

    Scenario YAML shape::

        kind: invoke_risk_and_spike
        params:
          risk:
            id: r-shopify-delta
            text: "..."
            impact: medium
            likelihood: medium
            status: open
          propose:
            summary: "..."
            dependent_contracts:
              - project://arch/modules/catalog-ingest/contracts#...
          complete:
            finding: "..."
            status: mitigated   # or accepted / confirmed_impossible
          author: sa-mvp        # optional; defaults to sa-mvp

    Author the risk first (via ``arch_set_risk``), propose a spike
    (which transitions the risk + creates a SPIKE ticket), then
    complete the spike with the requested outcome. The
    ``confirmed_impossible`` outcome triggers cascade-proposal
    generation — the bones-with-cascade scenario gates on the
    artifact landing under ``.jig/arch/cascades/``.

    The architecture ticket is auto-created if missing; the spike
    handler creates the spike ticket itself. Mirrors the L0 / L1 / L2 /
    L3 / SA invoke-handler pattern.
    """
    if await ctx.tickets.get(SA_TICKET_ID) is None:
        await ctx.tickets.create(
            Ticket(
                id=SA_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="SA — architecture",
                created_by="sim-driver",
            )
        )

    author = step.params.get("author") or "sa-mvp"

    risk_payload = step.params.get("risk")
    if risk_payload is None:
        raise ValueError(
            "invoke_risk_and_spike: params.risk is required"
        )
    risk_id = await handle_arch_set_risk(
        project_path=ctx.project_root, risk=risk_payload
    )

    propose = step.params.get("propose") or {}
    if "summary" not in propose:
        raise ValueError(
            "invoke_risk_and_spike: params.propose.summary is required"
        )
    spike_id = await handle_arch_propose_spike(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        risk_id=risk_id,
        summary=propose["summary"],
        dependent_contracts=propose.get("dependent_contracts", []),
        author=author,
    )

    complete = step.params.get("complete") or {}
    if "finding" not in complete or "status" not in complete:
        raise ValueError(
            "invoke_risk_and_spike: params.complete.finding and "
            "params.complete.status are required"
        )
    await handle_arch_complete_spike(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        spike_ticket_id=spike_id,
        finding=complete["finding"],
        status=complete["status"],
        author=author,
        # Pass the driver's emitter so the cascade branch can fire its
        # RiskStatusChanged event into the per-run analytics store.
        emitter=ctx.emitter,
        # Track C Final mitigation #3: when status is
        # ``mitigated_with_constraints`` the scenario YAML must
        # supply a ``constraint`` clause; the SA helper raises
        # without it. ``constraint`` is None for the other outcomes.
        constraint=complete.get("constraint"),
    )


async def _handle_invoke_vd_finalize(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Invoke handle_vd_finalize. Auto-creates the VD ticket if missing.

    Scenario YAML shape::

        kind: invoke_vd_finalize
        params:
          frontend:
            stack: { framework: htmx_alpine, ... }
            allowed_dependencies: [...]
            intent:
              problem: "..."
              simplest_solution: "..."
              complications_considered: { ... }
          wireframes:
            - screen_id: signup
              html: "<!-- wireframe-meta: {...} --><html>...</html>"
              meta:
                screen_id: signup
                title: Sign up
                ...
          summary: "VD finalize"
          author: vd          # optional; defaults to vd

    Mirrors the L0 / L1 / L2 / L3 / SA / Planner invoke patterns —
    auto-creates the ticket so resolve_after_handoff has a target.
    """
    if await ctx.tickets.get(VD_TICKET_ID) is None:
        await ctx.tickets.create(
            Ticket(
                id=VD_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="VD — frontend",
                created_by="sim-driver",
            )
        )
    params = dict(step.params)
    params.setdefault("author", "vd")
    if "frontend" not in params:
        raise ValueError(
            "invoke_vd_finalize: params.frontend is required"
        )
    if "summary" not in params:
        raise ValueError(
            "invoke_vd_finalize: params.summary is required"
        )
    await handle_vd_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        frontend=params["frontend"],
        wireframes=params.get("wireframes") or [],
        summary=params["summary"],
        author=params["author"],
    )


async def _handle_invoke_plan_finalize(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Invoke handle_plan_finalize. Auto-creates the planner ticket if missing.

    Mirrors the L0 / L3 / SA invoke handlers — the finalize handler
    expects its ticket to exist (resolve_after_handoff is a no-op
    otherwise, swallowing the expected status transition). MVP+
    scenarios use this step to exercise the agent path; bones
    scenarios continue to use ``write_build_plan`` for the operator
    hand-write path.
    """
    if await ctx.tickets.get(PLANNER_TICKET_ID) is None:
        await ctx.tickets.create(
            Ticket(
                id=PLANNER_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="Planner — build plan",
                created_by="sim-driver",
            )
        )
    params = dict(step.params)
    params.setdefault("author", "planner-pm")
    await handle_plan_finalize(
        tickets=ctx.tickets,
        threads=ctx.threads,
        bus=ctx.bus,
        project_path=ctx.project_root,
        **params,
    )


async def _handle_materialize_tickets(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Coordinator dispatch — materialize the bones-layer tickets.

    Optional ``visual_references_by_ticket`` (Track D MVP) is a
    ``{ticket_id: [screen_id, ...]}`` map — after materialization, the
    handler patches each named ticket's ``visual_references`` field so
    the visual_compliance reviewer sees a non-empty list. This is the
    sim-time stand-in for the Planner-PM authoring visual_references
    on UI tickets directly (which lands in a later track).

    Optional ``labels_by_ticket`` (Track G Final) is a
    ``{ticket_id: [label, ...]}`` map — after materialization, the
    handler patches each named ticket's ``labels`` field so a
    specialty-reviewer dispatch step (``invoke_specialty_reviewer``)
    sees the labels the planner would have authored. Stand-in for
    Planner-PM authoring labels directly.
    """
    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    await coord.materialize_ready_tickets()

    visual_refs = step.params.get("visual_references_by_ticket") or {}
    for ticket_id, screens in visual_refs.items():
        if not isinstance(screens, list):
            raise ValueError(
                f"materialize_tickets.visual_references_by_ticket[{ticket_id!r}] "
                f"must be a list, got {type(screens).__name__}"
            )
        await ctx.tickets.update(ticket_id, visual_references=list(screens))

    labels_by_ticket = step.params.get("labels_by_ticket") or {}
    for ticket_id, labels in labels_by_ticket.items():
        if not isinstance(labels, list):
            raise ValueError(
                f"materialize_tickets.labels_by_ticket[{ticket_id!r}] "
                f"must be a list, got {type(labels).__name__}"
            )
        await ctx.tickets.update(ticket_id, labels=list(labels))


async def _handle_invoke_coordinator_cycle(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Run one ``Coordinator.dispatch_cycle`` against the build plan.

    MVP-tier sim step: refreshes per-epic layer statuses from the live
    ticket store, then materializes the next ready layer per the
    plan's ``OrderingRule`` (default ``BONES_FIRST``). Bones scenarios
    keep using ``materialize_tickets`` (one-shot bones layer); MVP
    scenarios use this step to walk bones → mvp → final across cycles.
    """
    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    await coord.dispatch_cycle(ctx.project_root)


async def _handle_defer_ticket(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Defer a ticket via ``Coordinator.defer_ticket``.

    Scenario YAML shape::

        kind: defer_ticket
        params:
          ticket_id: tb-catalog-ingest
          reason: "blocked on external API spec"
          notes: "operator deferred for triage"  # optional

    Appends one row to ``.jig/plan/deferred-queue.jsonl`` and stamps
    ``deferred_at`` on the ticket. MVP+ scenarios use this step to
    exercise the DEFERRED queue path; bones scenarios don't touch it.
    """
    ticket_id = step.params["ticket_id"]
    reason = step.params.get("reason") or ""
    notes = step.params.get("notes") or ""
    if not reason:
        raise ValueError(
            "defer_ticket: params.reason is required"
        )
    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    await coord.defer_ticket(ticket_id, reason=reason, notes=notes)


async def _handle_triage_deferred(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Run one mechanical triage pass over the DEFERRED queue.

    Scenario YAML shape::

        kind: triage_deferred
        params: {}

    Triage decisions land on the driver context for assertion-time
    inspection. The mechanical heuristic (no LLM) recommends
    ``rematerialize`` / ``leave_deferred`` / ``close`` per
    ``Coordinator.triage_deferred``; the synthetic operator can
    inspect the recommendations via the captured-events / artifact
    assertions. MVP+ scenarios; bones scenarios don't touch it.
    """
    del step  # no params yet — triage takes none
    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    await coord.triage_deferred(ctx.project_root)


# Keywords-from-AC pattern. Mock dev produces a file containing every
# significant AC token so the bones contract-compliance reviewer's
# token-disjoint check passes. The reviewer's stopword + length>=4
# filter is in jig.reviewers.contract_compliance._significant_tokens.
_TOKEN_RE = re.compile(r"[a-zA-Z]{4,}")


def _ac_tokens_for_ticket(
    project_root: Path, ticket: Ticket
) -> set[str]:
    """Pull every AC token the ticket's reviewer would check.

    Returns the union across every IntegrationAcceptance for every
    capability the ticket claims (or every AC if capability_ids is
    empty). Mock dev uses these as the file content so the reviewer's
    token-match check passes.
    """
    if ticket.module_id is None:
        return set()
    contracts_src = module_contracts_path(project_root, ticket.module_id)
    if not contracts_src.is_file():
        return set()
    contracts = ContractsFile.model_validate(
        yaml.safe_load(contracts_src.read_text()) or {}
    )
    cap_set = set(ticket.capability_ids)
    tokens: set[str] = set()
    for ac in contracts.integration_ac:
        if cap_set and ac.capability not in cap_set:
            continue
        for must_text in ac.must:
            for tok in _TOKEN_RE.findall(must_text):
                tokens.add(tok.lower())
    return tokens


async def _handle_mock_dev_commit(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Mock dev: deterministic helper that commits a satisfying file.

    Replaces the real dev-agent run for CI/mock mode. The file content
    is the every-AC-token set so the contract-compliance reviewer's
    token-disjoint check passes. Worktree layout matches what
    ``jig.worktree.create_worktree`` produces (the reviewer's default
    convention) so tests don't need to override the worktree path.

    Real mode (Track H driver-CLI) replaces this handler with one that
    spawns a real Claude agent against the project_root.
    """
    ticket_id = step.params["ticket_id"]
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"mock_dev_commit: ticket {ticket_id!r} missing — did you "
            "forget a materialize_tickets step before this?"
        )

    # Worktree layout per jig.reviewers.contract_compliance default
    # convention: <project_root>/.jig/worktrees/<ticket_id>/
    worktree = ctx.project_root / ".jig" / "worktrees" / ticket_id
    worktree.mkdir(parents=True, exist_ok=True)

    _git(worktree, "init", "-b", "main")
    _git(worktree, "config", "user.email", "sim@jig")
    _git(worktree, "config", "user.name", "sim")
    _git(worktree, "config", "commit.gpgsign", "false")
    # Empty base commit so `git diff main..HEAD` has a base ref. The
    # bones reviewer falls back to working-tree diff if this ref is
    # missing, but giving it a clean base mirrors the orchestrator's
    # worktree shape (jig.worktree.create_worktree branches off main).
    _git(worktree, "commit", "--allow-empty", "-m", "base")
    _git(worktree, "checkout", "-b", f"jig/sim-{ticket_id}")

    ac_tokens = _ac_tokens_for_ticket(ctx.project_root, ticket)
    # Even with no AC tokens (no contracts file), produce *some* diff
    # so the empty-diff critical comment doesn't fire — bones needs a
    # non-empty diff for the existence check. Track D MVP: also include
    # every wireframe screen-id the ticket references so the
    # visual_compliance reviewer's reference check passes for UI tickets.
    body_tokens = sorted(ac_tokens) or ["bones", "tracer", "bullet", "spine"]
    visual_refs = sorted(ticket.visual_references)
    body = (
        f"# {ticket.title}\n\n"
        f"# Mock dev commit — references AC tokens for the bones reviewer:\n"
        + " ".join(body_tokens)
        + "\n"
        + (
            f"# Wireframe screens implemented: {' '.join(visual_refs)}\n"
            if visual_refs
            else ""
        )
    )
    impl = worktree / f"{ticket_id}.py"
    impl.write_text(body)
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"mock dev commit for {ticket_id}")


def _git(cwd: Path, *args: str) -> None:
    """Run a git subcommand inside ``cwd``; raise on nonzero."""
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True
    )


# ---- real-mode dev dispatch ---------------------------------------------


# Default workflow installed for real-mode bones runs. One phase, one
# role — the dev does the whole thing. Bones doesn't need spec/test/
# review phases (the contract-compliance reviewer fires later as its
# own step). Keeping it minimal also keeps the real-mode cost ceiling
# tight: one agent invocation per ticket. MVP gets a richer workflow.
_REAL_MODE_WORKFLOW_NAME = "default"
_REAL_MODE_DEV_ROLE = "dev"

# Polling interval for waiting on the orchestrator to drive a ticket
# to a terminal status. Short enough that test wall-clock stays low,
# long enough that we're not pegging the event loop.
_REAL_MODE_POLL_INTERVAL_S = 0.25
# Hard cap so a stuck agent can't hang the simulator forever. The
# bones runtime budget is "under 10 minutes" per
# docs/implementation/v2-plan.md; 15 minutes gives margin without
# hiding pathological slowness.
_REAL_MODE_TIMEOUT_S = 15 * 60


class _RealModeProgress:
    """Operator-visible progress emitter for the real-mode wait loop.

    Writes to stderr so stdout stays reserved for the final scenario
    report. Silent under pytest (PYTEST_CURRENT_TEST set) so test output
    isn't polluted.
    """

    def __init__(self, *, ticket_id: str) -> None:
        self._ticket_id = ticket_id
        self._silent = "PYTEST_CURRENT_TEST" in os.environ

    def emit(self, msg: str) -> None:
        if self._silent:
            return
        print(f"[sim:{self._ticket_id}] {msg}", file=sys.stderr, flush=True)


async def _handle_real_dev_dispatch(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Real-mode dev: spawn a Claude agent via the existing orchestrator path.

    Bootstraps a minimal v2 project on ``ctx.project_root`` (config +
    one workflow + dev role), starts an ``Orchestrator`` against it,
    and waits for the materialized ticket to reach a terminal status.
    The orchestrator's existing dispatch loop owns agent spawn,
    streaming, lifecycle, and analytics emission — we don't reach
    inside it. Cost aggregation runs after this step finishes and reads
    the analytics store the orchestrator already populated.

    The ``_seed_repo``-style git base must exist (the CLI seeds it; the
    test harness pre-seeds via the test fixture). Without it the
    orchestrator's worktree creation fails because there's no main
    branch to branch off of.
    """
    from jig.orchestrator import Orchestrator
    from jig.ticket import TicketStatus

    ticket_id = step.params["ticket_id"]
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"real_dev_dispatch: ticket {ticket_id!r} missing — did you "
            "forget a materialize_tickets step before this?"
        )

    _bootstrap_real_mode_project(ctx.project_root)

    # Real-mode runs can take minutes; without operator-visible progress
    # it looks like a hang. We surface ticket-status transitions and a
    # heartbeat to stderr so the operator knows the agent is alive.
    # stderr (not stdout) so the final scenario report stays the only
    # stdout signal.
    progress = _RealModeProgress(ticket_id=ticket_id)
    progress.emit("orchestrator starting…")

    orch = Orchestrator(project_path=ctx.project_root)
    await orch.startup()
    try:
        progress.emit("orchestrator ready; waiting for ticket dispatch")
        # The orchestrator's startup runs ``_start_ready_tickets``
        # which picks up our materialized ticket and dispatches it.
        # We just wait for the ticket to terminate.
        terminal = {
            TicketStatus.RESOLVED,
            TicketStatus.FAILED,
            TicketStatus.MERGE_CONFLICT,
        }
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _REAL_MODE_TIMEOUT_S
        last_status: TicketStatus | None = None
        last_heartbeat = loop.time()
        while loop.time() < deadline:
            current = await orch.tickets.get(ticket_id)  # type: ignore[union-attr]
            if current is not None and current.status != last_status:
                progress.emit(f"ticket status → {current.status.value}")
                last_status = current.status
            if current is not None and current.status in terminal:
                break
            now = loop.time()
            if now - last_heartbeat > 30.0:
                progress.emit(
                    f"still waiting… ({int(now - (deadline - _REAL_MODE_TIMEOUT_S))}s elapsed)"
                )
                last_heartbeat = now
            await asyncio.sleep(_REAL_MODE_POLL_INTERVAL_S)
        else:
            raise TimeoutError(
                f"real_dev_dispatch: ticket {ticket_id!r} did not reach a "
                f"terminal status within {_REAL_MODE_TIMEOUT_S}s"
            )
    finally:
        progress.emit("orchestrator shutting down")
        await orch.shutdown()
    progress.emit("dispatch complete")

    # Refresh the driver's stores from disk — the orchestrator has its
    # own JsonlStore instances and our in-memory copies don't see its
    # writes. Without this the post-step ticket_status assertion reads
    # stale data and (correctly) reports the orchestrator's RESOLVED
    # update never happened in our view.
    await ctx.tickets.load()
    await ctx.threads.load()
    await ctx.analytics.load()


def _bootstrap_real_mode_project(project_root: Path) -> None:
    """Write the minimum project config the orchestrator needs to dispatch.

    A real-mode run needs:
      * ``.jig/config.yaml`` with a ``Project`` (load_project must succeed)
      * ``.jig/workflows/default.yaml`` (the orchestrator loads ticket
        ``workflow="default"`` at dispatch time)
      * ``.jig/roles/dev.yaml`` (the workflow's only role)

    We deliberately ship a one-phase workflow so a single agent
    invocation drives the whole ticket. MVP can extend this. The role
    config is the shipped ``dev`` default re-saved into the project so
    the persistence loader's project-overrides-defaults rule resolves
    cleanly without us depending on the defaults dir.
    """
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.persistence import load_role, save_role, save_workflow
    from jig.project import Project, save_project

    save_project(
        project_root,
        Project(
            id="sim-real",
            name="sim-real",
            path=str(project_root),
            language="python",
            package_manager="uv",
        ),
    )

    workflow = WorkflowConfig(
        name=_REAL_MODE_WORKFLOW_NAME,
        phases=[
            PhaseConfig(
                name="implement",
                role=_REAL_MODE_DEV_ROLE,
                task_template="Implement the bones ticket: {ticket_title}",
                acceptance_criteria=(
                    "All integration AC tokens from the module's "
                    "contracts.yaml appear in the diff."
                ),
            ),
        ],
    )
    save_workflow(project_root, workflow)

    # Re-save the shipped dev role into the project so the role loader
    # finds it in the project layer (avoids depending on the defaults
    # directory at runtime — keeps the project self-contained for the
    # sim run).
    try:
        dev_role = load_role(project_root, _REAL_MODE_DEV_ROLE)
    except FileNotFoundError:
        # Defensive: if the shipped default disappears, ship a minimal
        # one rather than failing the bootstrap. This path should only
        # fire if jig itself is broken.
        dev_role = RoleConfig(
            role=_REAL_MODE_DEV_ROLE,
            phase_prompt=(
                "You are a development agent. Implement the ticket and "
                "call update_ticket(status=resolved) when finished."
            ),
        )
    save_role(project_root, dev_role)


async def _aggregate_agent_cost(ctx: DriverContext) -> float:
    """Sum ``AgentCompleted.cost_estimate_usd`` for this run.

    Treats ``None`` as 0.0 (mock-mode events, or SDK responses with no
    cost reported). Mock-mode runs never emit ``AgentCompleted``
    events, so this is effectively a no-op there. Real-mode runs see
    the orchestrator's per-agent emission (one event per spawn) and
    sum across the whole run.
    """
    events = await ctx.analytics.by_kind("agent_completed")
    return sum(
        (getattr(e, "cost_estimate_usd", None) or 0.0)
        for e in events
    )


async def _handle_invoke_dev_provisioning(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Exercise the dev-env provisioning happy path with an in-memory SQL recorder.

    Scenario YAML shape::

        kind: invoke_dev_provisioning
        params:
          ticket_id: tb-catalog-ingest
          cleanup: true        # optional; default true (drop on success)
          success: true        # optional; default true

    The handler reads ``.jig/dev/manifest.yaml``, builds a
    ``ProvisioningRegistry`` with an in-memory SQL recorder, runs
    ``provision_for_agent`` and (if cleanup=True) ``cleanup_for_agent``.
    Captured SQL + the env-var map are stamped on ``ctx`` so
    scenario-level assertions can introspect.
    """
    from jig.dev_env.manifest import derive_manifest
    from jig.dev_env.orchestrator_hook import (
        cleanup_for_agent,
        provision_for_agent,
    )
    from jig.dev_env.provisioning import ProvisioningRegistry
    from jig.spec_loader import (
        dev_manifest_path,
        load_architecture,
        save_dev_manifest,
    )

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError("invoke_dev_provisioning: params.ticket_id is required")
    do_cleanup = bool(step.params.get("cleanup", True))
    success = bool(step.params.get("success", True))
    agent_id = step.params.get("agent_id") or "sim-dev"

    # Auto-derive the manifest on first call so a scenario only needs
    # the architecture step (with dev_provisioning blocks) before
    # invoking this step. Real operators would do this via
    # ``jig dev manifest`` or the ``dev_derive_manifest`` MCP tool.
    if not dev_manifest_path(ctx.project_root).is_file():
        arch = load_architecture(ctx.project_root)
        save_dev_manifest(ctx.project_root, derive_manifest(arch))

    captured_sql: list[str] = []

    async def _recorder(sql: str) -> None:
        captured_sql.append(sql)

    registry = ProvisioningRegistry(postgres_sql_executor=_recorder)
    env_map = await provision_for_agent(
        ctx.project_root,
        agent_id=agent_id,
        ticket_id=ticket_id,
        registry=registry,
    )
    if do_cleanup:
        await cleanup_for_agent(
            ctx.project_root,
            agent_id=agent_id,
            ticket_id=ticket_id,
            success=success,
            registry=registry,
        )
    ctx.dev_provisioning_sql = captured_sql
    ctx.dev_provisioning_env_vars = env_map


async def _handle_invoke_quartermaster_feedback(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Record one quartermaster feedback row + refresh the calibration on ctx.

    Scenario YAML shape::

        kind: invoke_quartermaster_feedback
        params:
          briefing_id: brief-test-1
          useful: false                                # required
          not_useful_pattern_ids:                      # optional
            - module_repeated_escalations
          note: "operator decided this was noise"      # optional

    After recording the feedback row, the handler reloads the live
    calibration into ``ctx.quartermaster_calibration`` so a follow-
    up assertion (e.g. ``artifact_written`` against the JSONL +
    operator-supplied content checks) can verify the threshold
    actually shifted. Track I Final exercise of the feedback loop.
    """
    from jig.quartermaster import (
        get_pattern_calibration,
        record_feedback,
    )

    if "briefing_id" not in step.params:
        raise ValueError(
            "invoke_quartermaster_feedback: params.briefing_id is required"
        )
    if "useful" not in step.params:
        raise ValueError(
            "invoke_quartermaster_feedback: params.useful is required"
        )

    await record_feedback(
        ctx.project_root,
        briefing_id=step.params["briefing_id"],
        useful=bool(step.params["useful"]),
        not_useful_pattern_ids=step.params.get("not_useful_pattern_ids") or [],
        note=step.params.get("note") or None,
    )
    cal = await get_pattern_calibration(ctx.project_root)
    ctx.quartermaster_calibration = dict(cal.thresholds)


async def _handle_invoke_specialty_reviewer(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Verify dispatch selected the requested specialty reviewer.

    Scenario YAML shape::

        kind: invoke_specialty_reviewer
        params:
          ticket_id: tb-catalog-ingest
          expected_reviewer: reviewer-security    # required

    Mock-mode only: doesn't spawn the LLM agent. Calls
    ``select_reviewers_for_ticket(ticket, project_root=ctx.project_root)``
    and stamps the full selected list on
    ``ctx.specialty_reviewer_selection`` so downstream artifact /
    per-step assertions can introspect. Raises if the requested
    reviewer wasn't selected — the synthetic operator wants to know
    immediately when its selection assumption is wrong.
    """
    from jig.reviewers.dispatch import select_reviewers_for_ticket

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError(
            "invoke_specialty_reviewer: params.ticket_id is required"
        )
    expected = step.params.get("expected_reviewer")
    if not expected:
        raise ValueError(
            "invoke_specialty_reviewer: params.expected_reviewer is required"
        )

    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"invoke_specialty_reviewer: ticket {ticket_id!r} missing"
        )

    selection = select_reviewers_for_ticket(
        ticket, project_root=ctx.project_root
    )
    ctx.specialty_reviewer_selection = list(selection)

    if expected not in selection:
        raise AssertionError(
            f"invoke_specialty_reviewer: expected reviewer "
            f"{expected!r} was NOT selected; got {selection!r}"
        )


async def _handle_invoke_severity_disposition(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Apply severity-tier disposition against synthesized comments.

    Scenario YAML shape::

        kind: invoke_severity_disposition
        params:
          ticket_id: tb-catalog-ingest
          comments:
            - severity: notable           # required
              reviewer: reviewer-pattern-conformance  # optional
              prose: "..."                # optional; defaults to filler
              file: jig/foo.py            # optional; ensures self-check pass
              confidence: 0.85            # optional; defaults to 0.85

    Mock-mode handler — no LLM. Builds the synthesized comment list,
    runs ``apply_severity_disposition`` against the live ticket store
    + Coordinator + ThreadStore, and stamps the disposition counts on
    the driver context for assertion-time introspection.

    The notable→deferred branch routes through the live Coordinator
    so a scenario asserting against ``.jig/plan/deferred-queue.jsonl``
    sees the row land. Critical→FAILED routes through the ticket
    store so a ticket_status assertion sees the flip.
    """
    from jig.reviewers.comment import (
        ReviewerComment,
        ReviewerCommentType,
        Severity,
    )
    from jig.reviewers.disposition import apply_severity_disposition

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError(
            "invoke_severity_disposition: params.ticket_id is required"
        )
    comments_raw = step.params.get("comments") or []
    if not isinstance(comments_raw, list):
        raise ValueError(
            "invoke_severity_disposition: params.comments must be a list"
        )

    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"invoke_severity_disposition: ticket {ticket_id!r} missing"
        )

    comments: list[ReviewerComment] = []
    for entry in comments_raw:
        severity = entry.get("severity")
        if not severity:
            raise ValueError(
                "invoke_severity_disposition: each comment requires a severity"
            )
        confidence = entry.get("confidence", 0.85)
        # Critical comments always pass the self-check; for important /
        # notable we compose a default prose long enough + an anchor
        # so the disposition path doesn't trip the gate.
        prose = entry.get(
            "prose",
            "Severity-disposition fixture comment for the synthetic "
            "operator scenario; long enough to pass the gate.",
        )
        comments.append(
            ReviewerComment(
                type=ReviewerCommentType(entry.get("type", "pattern-divergence")),
                severity=Severity(severity),
                reviewer=entry.get(
                    "reviewer", "reviewer-pattern-conformance"
                ),
                prose=prose,
                confidence=confidence,
                file=entry.get("file", "jig/foo.py"),
                contract_uri=entry.get("contract_uri"),
            )
        )

    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    result = await apply_severity_disposition(
        comments,
        ticket,
        ctx.tickets,
        coord,
        threads=ctx.threads,
    )
    ctx.last_disposition_blocked = len(result.blocked_by)
    ctx.last_disposition_consulted_sa = len(result.consulted_sa)
    ctx.last_disposition_deferred = len(result.deferred)


async def _handle_invoke_cascade_reject(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Reject the most recent cascade proposal.

    Scenario YAML shape::

        kind: invoke_cascade_reject
        params:
          risk_id: r-shopify-delta   # required
          reason: not_relevant       # required
          actor: operator            # optional; defaults to "operator"

    Discovers the cascade artifact via glob over ``.jig/arch/cascades/``
    so the YAML doesn't have to know the writer-stamped timestamp.
    Stamps ``last_cascade_id`` + ``last_cascade_action`` on the driver
    context for assertion-time introspection.
    """
    from jig.sa_incremental_mcp import handle_arch_reject_cascade
    from jig.spec_loader import cascades_dir

    risk_id = step.params.get("risk_id")
    reason = step.params.get("reason")
    if not risk_id or not reason:
        raise ValueError(
            "invoke_cascade_reject: params.risk_id and params.reason "
            "are required"
        )
    actor = step.params.get("actor") or "operator"
    cascade_id = _resolve_latest_cascade_id(
        cascades_dir(ctx.project_root), risk_id
    )
    entry = await handle_arch_reject_cascade(
        project_path=ctx.project_root,
        cascade_id=cascade_id,
        reason=reason,
        actor=actor,
    )
    ctx.last_cascade_id = cascade_id
    ctx.last_cascade_action = entry.action


async def _handle_invoke_cascade_stage(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Stage the most recent cascade and approve every produced stage.

    Scenario YAML shape::

        kind: invoke_cascade_stage
        params:
          risk_id: r-shopify-delta   # required
          chunk_size: 1              # optional; defaults to 5
          actor: operator            # optional; defaults to "operator"
          approve_all: true          # optional; defaults to true

    Mock-mode driver — exercises the staged → stage_approved → resolved
    transition. ``approve_all=false`` leaves the cascade in ``staged``
    so a scenario can verify the partial-approval state. Stamps
    ``last_cascade_id`` and ``last_cascade_stage_count`` on the driver
    context.
    """
    from jig.sa_incremental_mcp import (
        handle_arch_approve_cascade_stage,
        handle_arch_stage_cascade,
    )
    from jig.spec_loader import cascades_dir

    risk_id = step.params.get("risk_id")
    if not risk_id:
        raise ValueError(
            "invoke_cascade_stage: params.risk_id is required"
        )
    actor = step.params.get("actor") or "operator"
    chunk_size = int(step.params.get("chunk_size", 5))
    approve_all = bool(step.params.get("approve_all", True))

    cascade_id = _resolve_latest_cascade_id(
        cascades_dir(ctx.project_root), risk_id
    )
    stages = await handle_arch_stage_cascade(
        project_path=ctx.project_root,
        cascade_id=cascade_id,
        actor=actor,
        chunk_size=chunk_size,
    )
    ctx.last_cascade_id = cascade_id
    ctx.last_cascade_stage_count = len(stages)
    if approve_all:
        for s in stages:
            await handle_arch_approve_cascade_stage(
                project_path=ctx.project_root,
                cascade_id=cascade_id,
                stage_id=s.stage_id,
                actor=actor,
            )
        ctx.last_cascade_action = "resolved"
    else:
        ctx.last_cascade_action = "staged"


async def _handle_invoke_cascade_risk_low(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Set ``cascade_risk_low`` on the named module(s) and read the
    Coordinator's ``next_layer_ready`` to verify the override fires.

    Scenario YAML shape::

        kind: invoke_cascade_risk_low
        params:
          modules:
            - id: m-categorization
              rationale: standalone CRUD; no shared shapes
          # The plan must already exist on disk (build via
          # ``write_build_plan`` upstream); the handler reloads it
          # before introspecting.
          expected_next_layer: mvp   # optional assertion shorthand

    The handler stamps the resolved next-layer name on the driver
    context as ``last_next_layer`` so assertions can introspect; if
    ``expected_next_layer`` is given, mismatches raise so the scenario
    fails loud.
    """
    from jig.coordinator import Coordinator
    from jig.sa_incremental_mcp import handle_arch_set_cascade_risk_low
    from jig.spec_loader import load_build_plan

    modules = step.params.get("modules") or []
    if not modules:
        raise ValueError(
            "invoke_cascade_risk_low: params.modules is required (list "
            "of {id, rationale} dicts)"
        )
    for m in modules:
        mid = m.get("id")
        rationale = m.get("rationale") or ""
        if not mid:
            raise ValueError(
                "invoke_cascade_risk_low: each modules[] entry needs an id"
            )
        await handle_arch_set_cascade_risk_low(
            project_path=ctx.project_root,
            module_id=mid,
            low=True,
            rationale=rationale,
        )

    # Re-read the plan and ask the Coordinator what's next.
    plan = load_build_plan(ctx.project_root)
    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    next_layer = coord.next_layer_ready(plan)
    ctx.last_next_layer = next_layer

    expected = step.params.get("expected_next_layer")
    if expected is not None and expected != next_layer:
        raise AssertionError(
            f"invoke_cascade_risk_low: expected next_layer="
            f"{expected!r}, got {next_layer!r}"
        )


def _resolve_latest_cascade_id(cdir: Path, risk_id: str) -> str:
    """Find the most recent cascade artifact for ``risk_id``; return its id.

    Sort by filename — the timestamp suffix is monotonic so the lex-
    sort gives chronological order. Raises FileNotFoundError when no
    matching artifact exists so a scenario step that runs out-of-order
    fails loud rather than silently passing nothing through.
    """
    matches = sorted(cdir.glob(f"{risk_id}-*.yaml"))
    if not matches:
        raise FileNotFoundError(
            f"no cascade artifact for risk {risk_id!r} under {cdir}"
        )
    return matches[-1].stem


async def _handle_run_reviewer(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Invoke the contract-compliance reviewer; cache comments on ctx.

    Bones ships only contract-compliance (Track G2). The reviewer-set
    selection logic in ``jig.reviewers.dispatch.select_reviewers_for_ticket``
    will, MVP, return additional reviewers; for bones we pin to the
    one reviewer the bones scenario gates on.
    """
    ticket_id = step.params["ticket_id"]
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"run_reviewer: ticket {ticket_id!r} missing"
        )
    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(ticket, ctx.project_root)
    ctx.reviewer_comments[reviewer.reviewer_id] = comments
    all_comments = list(comments)

    # Track D MVP: also run visual_compliance when the ticket has
    # visual_references — otherwise UI-flavored scenarios can't gate
    # on the reviewer's critical-comment count via this step. The
    # reviewer no-ops when references is empty so non-UI tickets pay
    # nothing.
    if ticket.visual_references:
        # Local import: visual_compliance imports the wireframe linter
        # via the spec_loader path helpers; pulling it in lazily keeps
        # the driver module's import surface unchanged for non-UI runs.
        from jig.reviewers.visual_compliance import VisualComplianceReviewer

        vc = VisualComplianceReviewer()
        vc_comments = await vc.review(ticket, ctx.project_root)
        ctx.reviewer_comments[vc.reviewer_id] = vc_comments
        all_comments.extend(vc_comments)

    # Bones success means resolving the ticket. The orchestrator's
    # post-review wiring lands in MVP (Track G3 two-cadence + Track F
    # cycle-completion), but bones needs SOMETHING to flip the ticket
    # to RESOLVED so the ticket_status assertion passes. Using the
    # critical-comment count as the gate mirrors the design's
    # "critical = block" rule (G10).
    has_critical = any(c.severity == "critical" for c in all_comments)
    if not has_critical:
        from jig.ticket import TicketStatus
        await ctx.tickets.update_status(ticket_id, TicketStatus.RESOLVED)


# ---- assertion evaluation ------------------------------------------------


async def _evaluate_assertion(
    ctx: DriverContext, assertion: BaseModel
) -> AssertionResult:
    """Dispatch to the per-kind check function.

    The discriminated-union shape means we get the concrete type back
    from the loader; we still ``isinstance``-dispatch (rather than
    table-of-kind) because each check needs a different signature.
    """
    if isinstance(assertion, ArtifactWrittenAssertion):
        return _check_artifact_written(ctx, assertion)
    if isinstance(assertion, AnalyticsEventEmittedAssertion):
        return await _check_analytics_event_emitted(ctx, assertion)
    if isinstance(assertion, TicketStatusAssertion):
        return await _check_ticket_status(ctx, assertion)
    if isinstance(assertion, ReviewerReturnedNoCriticalAssertion):
        return _check_reviewer_no_critical(ctx, assertion)
    if isinstance(assertion, CostUnderBudgetAssertion):
        return _check_cost_under_budget(ctx, assertion)
    return AssertionResult(
        kind=type(assertion).__name__,
        passed=False,
        detail=f"unknown assertion type {type(assertion).__name__!r}",
    )


def _check_artifact_written(
    ctx: DriverContext, a: ArtifactWrittenAssertion
) -> AssertionResult:
    path = ctx.project_root / a.path
    if not path.is_file():
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=f"path {a.path!r} does not exist (project_root={ctx.project_root})",
        )
    if a.contains is not None:
        text = path.read_text()
        if a.contains not in text:
            return AssertionResult(
                kind=a.kind,
                passed=False,
                detail=(
                    f"path {a.path!r} exists but does not contain "
                    f"{a.contains!r}"
                ),
            )
    return AssertionResult(kind=a.kind, passed=True, detail=f"{a.path} OK")


async def _check_analytics_event_emitted(
    ctx: DriverContext, a: AnalyticsEventEmittedAssertion
) -> AssertionResult:
    # Drain any in-flight emit_nowait writes so events the just-finished
    # step fired land in the store before we read it. Multiple drains
    # are safe (drain returns when no pending tasks).
    await ctx.emitter.drain()
    events = await ctx.analytics.by_kind(a.event_kind)
    if not events:
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=f"no analytics event of kind {a.event_kind!r} captured",
        )
    if a.field_constraints:
        for ev in events:
            ev_dict = ev.model_dump(mode="json")
            if all(ev_dict.get(k) == v for k, v in a.field_constraints.items()):
                return AssertionResult(
                    kind=a.kind, passed=True, detail=f"{a.event_kind} OK"
                )
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=(
                f"events of kind {a.event_kind!r} found ({len(events)}) "
                f"but none matched constraints {a.field_constraints!r}"
            ),
        )
    return AssertionResult(
        kind=a.kind, passed=True, detail=f"{a.event_kind} ({len(events)})"
    )


async def _check_ticket_status(
    ctx: DriverContext, a: TicketStatusAssertion
) -> AssertionResult:
    t = await ctx.tickets.get(a.ticket_id)
    if t is None:
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=f"ticket {a.ticket_id!r} not found in store",
        )
    if t.status.value != a.status:
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=(
                f"ticket {a.ticket_id!r} status is {t.status.value!r}, "
                f"expected {a.status!r}"
            ),
        )
    return AssertionResult(
        kind=a.kind,
        passed=True,
        detail=f"{a.ticket_id} → {a.status}",
    )


def _check_reviewer_no_critical(
    ctx: DriverContext, a: ReviewerReturnedNoCriticalAssertion
) -> AssertionResult:
    if a.reviewer_id not in ctx.reviewer_comments:
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=(
                f"reviewer {a.reviewer_id!r} was never invoked during "
                "this run"
            ),
        )
    comments = ctx.reviewer_comments[a.reviewer_id]
    criticals = [c for c in comments if c.severity == "critical"]
    if criticals:
        prose = "; ".join(c.prose[:60] for c in criticals)
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=(
                f"reviewer {a.reviewer_id!r} returned "
                f"{len(criticals)} critical comment(s): {prose}"
            ),
        )
    return AssertionResult(
        kind=a.kind,
        passed=True,
        detail=f"{a.reviewer_id} → 0 critical ({len(comments)} total)",
    )


def _check_cost_under_budget(
    ctx: DriverContext, a: CostUnderBudgetAssertion
) -> AssertionResult:
    if ctx.cost_usd >= a.usd:
        return AssertionResult(
            kind=a.kind,
            passed=False,
            detail=f"cost {ctx.cost_usd:.4f} >= budget {a.usd:.4f}",
        )
    return AssertionResult(
        kind=a.kind,
        passed=True,
        detail=f"cost {ctx.cost_usd:.4f} < budget {a.usd:.4f}",
    )


async def _handle_invoke_dev_ephemeral(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Exercise the per_agent_ephemeral SQLite path end-to-end.

    Scenario YAML shape::

        kind: invoke_dev_ephemeral
        params:
          ticket_id: tb-catalog-ingest
          service_id: ephem            # optional; default ``ephem``
          namespace_template: "agent_{ticket_id}"  # optional
          cleanup: true                # optional; default true

    Builds a one-service manifest in-memory (no architecture.yaml
    dependency — the scenario just wants to exercise the dispatcher),
    runs ``provision_agent_namespace`` rooted at ``ctx.project_root``,
    then (if cleanup=True) runs ``cleanup_agent_namespace``. Stamps
    ``ctx.last_ephemeral_url`` + ``ctx.last_ephemeral_path`` so a
    follow-up assertion can introspect.
    """
    from jig.dev_env.ephemeral import ephemeral_root
    from jig.dev_env.provisioning import (
        ProvisioningRegistry,
        cleanup_agent_namespace,
        provision_agent_namespace,
    )
    from jig.schemas.dev_env import DevManifest, ManifestService

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError(
            "invoke_dev_ephemeral: params.ticket_id is required"
        )
    service_id = step.params.get("service_id") or "ephem"
    namespace_template = (
        step.params.get("namespace_template") or "agent_{ticket_id}"
    )
    do_cleanup = bool(step.params.get("cleanup", True))
    success = bool(step.params.get("success", True))
    agent_id = step.params.get("agent_id") or "sim-dev"

    manifest = DevManifest(
        services=[
            ManifestService(
                id=service_id,
                kind="sqlite",
                strategy="per_agent_ephemeral",
                namespace_template=namespace_template,
            ),
        ],
        connection_string_templates={service_id: ""},
    )
    registry = ProvisioningRegistry(project_root=ctx.project_root)
    url_map = await provision_agent_namespace(
        manifest,
        agent_id=agent_id,
        ticket_id=ticket_id,
        registry=registry,
    )
    url = url_map.get(service_id)
    if not url:
        raise AssertionError(
            "invoke_dev_ephemeral: provisioner returned no URL for "
            f"service={service_id!r}"
        )
    ctx.last_ephemeral_url = url

    # Walk to the actual file so the assertion suite can verify it landed.
    root = ephemeral_root(ctx.project_root)
    files = list((root / service_id).glob("*.db"))
    if not files:
        raise AssertionError(
            f"invoke_dev_ephemeral: no .db file found under {root / service_id}"
        )
    ctx.last_ephemeral_path = str(files[0])

    if do_cleanup:
        await cleanup_agent_namespace(
            manifest,
            agent_id=agent_id,
            ticket_id=ticket_id,
            success=success,
            registry=registry,
        )


async def _handle_invoke_fixture_replay(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Exercise the vcr-style cassette record + replay round-trip.

    Scenario YAML shape::

        kind: invoke_fixture_replay
        params:
          service_id: shopify-api      # required
          method: GET                  # required
          url: https://api.shopify.com/products  # required
          body: { ... }                # optional
          response: { status: 200, json: { products: [] } }  # required

    Pre-records a cassette via ``FixtureStore.record``, builds a
    ``FixtureMiddleware`` in REPLAY_ONLY mode (no backing client —
    a missed cassette would raise loudly per design.md §"replay_only
    default"), and calls ``record_or_replay`` with the same shape.
    Stamps the response on ``ctx.last_fixture_response`` so an
    assertion can verify the replay matched the recording.
    """
    from jig.dev_env.fixtures import (
        FixtureCassette,
        FixtureMiddleware,
        FixtureMode,
        FixtureStore,
        request_signature,
    )

    service_id = step.params.get("service_id")
    if not service_id:
        raise ValueError(
            "invoke_fixture_replay: params.service_id is required"
        )
    method = step.params.get("method")
    if not method:
        raise ValueError(
            "invoke_fixture_replay: params.method is required"
        )
    url = step.params.get("url")
    if not url:
        raise ValueError(
            "invoke_fixture_replay: params.url is required"
        )
    response = step.params.get("response")
    if response is None:
        raise ValueError(
            "invoke_fixture_replay: params.response is required"
        )
    body = step.params.get("body")

    store = FixtureStore(ctx.project_root)
    sig = request_signature(method, url, body)
    await store.record(
        FixtureCassette(
            service_id=service_id,
            request_signature=sig,
            request={"method": method, "url": url, "body": body or {}},
            response=response,
        )
    )

    middleware = FixtureMiddleware(
        service_id=service_id,
        store=store,
        mode=FixtureMode.REPLAY_ONLY,
        client=None,  # REPLAY_ONLY must never hit a client.
    )
    replayed = await middleware.record_or_replay(
        method, url, headers=None, body=body
    )
    ctx.last_fixture_response = dict(replayed)
    if replayed != response:
        raise AssertionError(
            "invoke_fixture_replay: replayed response does not match "
            f"recorded one (got {replayed!r}, expected {response!r})"
        )


# ---- Track F Final — tier promotion + calibration sim handlers ---------


async def _handle_invoke_tier_promotion(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Force a mid-work tier promotion against a ticket.

    Scenario YAML shape::

        kind: invoke_tier_promotion
        params:
          ticket_id: tb-catalog-ingest      # required; must already exist
          contract_uri: "project://arch/.../#x"  # optional; default placeholder
          # Number of failed per-commit checks to inject before triggering
          # the promotion. Defaults to 3 (matching the auto-escalation
          # threshold).
          failures: 3

    Synthesizes the analytics events the auto-escalation checker
    needs (PerCommitCheckFailed × failures), then runs
    decide_promotion + promote_ticket_tier against the live store.
    Stamps the resolved promotion onto the driver context.
    """
    from jig.analytics.events import PerCommitCheckFailed
    from jig.auto_escalation import check_escalation_signals
    from jig.pm.tier_promotion import decide_promotion, promote_ticket_tier

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError(
            "invoke_tier_promotion: params.ticket_id is required"
        )
    failures = int(step.params.get("failures", 3))
    contract_uri = step.params.get(
        "contract_uri",
        "project://arch/modules/sim/contracts#owns/x/write_access",
    )
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"invoke_tier_promotion: ticket {ticket_id!r} missing — did you "
            "forget a materialize_tickets step before this?"
        )

    agent_id = step.params.get("agent_id") or f"dev:{ticket_id[:8]}"
    for i in range(failures):
        await ctx.analytics.append(
            PerCommitCheckFailed(
                ticket_id=ticket_id,
                agent_id=agent_id,
                commit_sha=f"sim{i:03d}",
                reviewer_role="contract_compliance",
                violation_category="ownership",
                contract_uri=contract_uri,
                severity="critical",
                auto_applied=False,
            )
        )

    signals = await check_escalation_signals(ticket_id, ctx.analytics)
    current_tier = ticket.dev_tier or "standard"
    target = decide_promotion(signals, current_tier)
    if target is None:
        ctx.last_tier_promotion_from = current_tier
        ctx.last_tier_promotion_to = current_tier
        return

    record = await promote_ticket_tier(
        ctx.tickets,
        ticket_id,
        target,
        reason="sim: " + (signals[0].detail or signals[0].kind),
        signal=signals[0],
        emitter=ctx.emitter,
        agent_id=agent_id,
    )
    ctx.last_tier_promotion_from = record.from_tier
    ctx.last_tier_promotion_to = record.to_tier


async def _handle_invoke_calibration_record(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Persist one synthetic calibration sample.

    Scenario YAML shape::

        kind: invoke_calibration_record
        params:
          ticket_id: tb-catalog-ingest      # required
          size: m                           # required (xs|s|m|l|xl)
          dev_tier: standard                # optional; default standard
          observed_turns: 25                # optional; default 0
          observed_tool_calls: 25           # optional; default 0
          observed_duration_ms: 240000      # optional
          observed_cost_usd: 1.5            # optional
          completion_status: success        # optional; default success

    Stamps the size onto the driver context so an assertion can verify
    the sample was persisted at the right slot.
    """
    from jig.pm.calibration import CalibrationSample, CalibrationStore

    ticket_id = step.params.get("ticket_id")
    size = step.params.get("size")
    if not ticket_id or not size:
        raise ValueError(
            "invoke_calibration_record: params.ticket_id and params.size "
            "are required"
        )

    store = CalibrationStore(ctx.project_root)
    await store.load()
    sample = CalibrationSample(
        ticket_id=ticket_id,
        size=size,
        dev_tier=step.params.get("dev_tier") or "standard",
        layer=step.params.get("layer"),
        observed_turns=int(step.params.get("observed_turns", 0)),
        observed_tool_calls=int(step.params.get("observed_tool_calls", 0)),
        observed_duration_ms=int(step.params.get("observed_duration_ms", 0)),
        observed_cost_usd=float(step.params.get("observed_cost_usd", 0.0)),
        completion_status=step.params.get(
            "completion_status", "success"
        ),  # type: ignore[arg-type]
    )
    await store.append(sample)
    ctx.last_calibration_sample_size = size


# ---- Track D Final — vision / a11y / responsive sim steps -------------


async def _handle_invoke_vision_diff(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Run the FullVisualComplianceReviewer with the StubVisionProvider.

    Scenario YAML shape::

        kind: invoke_vision_diff
        params:
          ticket_id: tb-post-a-job          # required
          screen_id: post-a-job             # required
          screenshot_present: true          # optional; default True
          differences:                      # optional; default []
            - kind: layout-shift
              description: header drifted left
              severity: important
              location_hint: header

    Mock-mode only — no real vision LLM. Builds a
    ``StubVisionProvider`` from the canned ``differences``, calls
    ``FullVisualComplianceReviewer.review_full`` rooted at the
    project's screenshots dir (``.jig/sim/screenshots/``), and stamps
    the resulting comments on ``ctx.reviewer_comments`` +
    ``ctx.last_vision_diff_count`` so assertion-time checks can
    introspect.
    """
    from jig.reviewers.vision_provider import (
        StubVisionProvider,
        VisionDiffResult,
        VisualDifference,
    )
    from jig.reviewers.visual_compliance_full import (
        FullVisualComplianceReviewer,
    )

    ticket_id = step.params.get("ticket_id")
    screen_id = step.params.get("screen_id")
    if not ticket_id or not screen_id:
        raise ValueError(
            "invoke_vision_diff: params.ticket_id and params.screen_id "
            "are required"
        )
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"invoke_vision_diff: ticket {ticket_id!r} missing"
        )

    screenshots_dir = ctx.project_root / ".jig" / "sim" / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    if step.params.get("screenshot_present", True):
        # Drop a tiny placeholder so the reviewer reaches the vision
        # call. Bytes content is irrelevant — the StubVisionProvider
        # ignores the inputs and returns the canned result.
        (screenshots_dir / f"{screen_id}.png").write_bytes(b"PNG-stub")

    diffs = [
        VisualDifference(
            kind=d["kind"],
            description=d["description"],
            severity=d.get("severity", "important"),
            location_hint=d.get("location_hint"),
        )
        for d in (step.params.get("differences") or [])
    ]
    stub = StubVisionProvider(result=VisionDiffResult(differences=diffs))
    reviewer = FullVisualComplianceReviewer()
    comments = await reviewer.review_full(
        ticket,
        ctx.project_root,
        stub,
        screenshots_dir,
    )
    ctx.reviewer_comments[reviewer.reviewer_id] = comments
    ctx.last_vision_diff_count = sum(
        1 for c in comments if c.type == "visual-vision-diff"
    )


async def _handle_invoke_accessibility_review(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Run AccessibilityReviewer end-to-end against the ticket's wireframes.

    Scenario YAML shape::

        kind: invoke_accessibility_review
        params:
          ticket_id: tb-post-a-job        # required
          screen_id: post-a-job           # optional
          html: |                         # optional override
            <!-- wireframe-meta: {...} -->
            <html>...</html>

    When ``html`` is supplied the handler writes it to the wireframe
    path before running the reviewer — useful for forcing a specific
    WCAG violation pattern without authoring a full wireframe via
    ``invoke_vd_finalize``. Stamps the resulting WCAG rule ids on
    ``ctx.last_a11y_rule_ids``.
    """
    from jig.reviewers.accessibility import AccessibilityReviewer
    from jig.spec_loader import save_wireframe

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError(
            "invoke_accessibility_review: params.ticket_id is required"
        )
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"invoke_accessibility_review: ticket {ticket_id!r} missing"
        )

    if step.params.get("html"):
        screen_id = step.params.get("screen_id")
        if not screen_id:
            raise ValueError(
                "invoke_accessibility_review: params.screen_id is "
                "required when params.html is supplied"
            )
        save_wireframe(ctx.project_root, screen_id, step.params["html"])

    reviewer = AccessibilityReviewer()
    comments = await reviewer.review(ticket, ctx.project_root)
    ctx.reviewer_comments[reviewer.reviewer_id] = comments
    ctx.last_a11y_rule_ids = [
        c.wcag_rule_id for c in comments if c.wcag_rule_id is not None
    ]


async def _handle_invoke_responsive_review(
    ctx: DriverContext, step: ScenarioStep
) -> None:
    """Run ResponsiveDesignReviewer end-to-end against the ticket's wireframes.

    Scenario YAML shape mirrors the accessibility handler — same
    optional ``html`` override pattern. Stamps the breakpoint labels
    surfaced by violations on ``ctx.last_responsive_breakpoints``.
    """
    from jig.reviewers.responsive import ResponsiveDesignReviewer
    from jig.spec_loader import save_wireframe

    ticket_id = step.params.get("ticket_id")
    if not ticket_id:
        raise ValueError(
            "invoke_responsive_review: params.ticket_id is required"
        )
    ticket = await ctx.tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(
            f"invoke_responsive_review: ticket {ticket_id!r} missing"
        )

    if step.params.get("html"):
        screen_id = step.params.get("screen_id")
        if not screen_id:
            raise ValueError(
                "invoke_responsive_review: params.screen_id is "
                "required when params.html is supplied"
            )
        save_wireframe(ctx.project_root, screen_id, step.params["html"])

    reviewer = ResponsiveDesignReviewer()
    comments = await reviewer.review(ticket, ctx.project_root)
    ctx.reviewer_comments[reviewer.reviewer_id] = comments
    ctx.last_responsive_breakpoints = [
        c.breakpoint for c in comments if c.breakpoint is not None
    ]
