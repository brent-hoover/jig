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
from jig.po_l3_mcp import handle_l3_finalize
from jig.reviewers import ContractComplianceReviewer, ReviewerComment
from jig.schemas.arch import Architecture, ContractsFile
from jig.schemas.plan import BuildPlan
from jig.schemas.po import SuitesIndex
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


# ---- step handler signature ---------------------------------------------


# Step handlers receive (ctx, step) and may run async work. Built-in
# handlers cover the bones StepKind values; tests can register custom
# handlers via ``Driver.register_step_handler`` for synthetic step
# kinds (see test_driver_sets_simulator_env_var_during_run).
StepHandler = Callable[
    ["DriverContext", ScenarioStep],
    Awaitable[None],
]


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

    def __init__(self, *, real_mode: bool = False) -> None:
        self._real_mode = real_mode
        dev_handler: StepHandler = (
            _handle_real_dev_dispatch if real_mode else _handle_mock_dev_commit
        )
        self._handlers: dict[str, StepHandler] = {
            StepKind.INVOKE_L0_FINALIZE.value: _handle_l0_finalize,
            StepKind.INVOKE_L3_FINALIZE.value: _handle_l3_finalize,
            StepKind.WRITE_SUITES_YAML.value: _handle_write_suites_yaml,
            StepKind.WRITE_ARCHITECTURE.value: _handle_write_architecture,
            StepKind.WRITE_MODULE_CONTRACTS.value: _handle_write_module_contracts,
            StepKind.WRITE_BUILD_PLAN.value: _handle_write_build_plan,
            StepKind.INVOKE_PLAN_FINALIZE.value: _handle_invoke_plan_finalize,
            StepKind.MATERIALIZE_TICKETS.value: _handle_materialize_tickets,
            StepKind.MOCK_DEV_COMMIT.value: dev_handler,
            StepKind.RUN_REVIEWER.value: _handle_run_reviewer,
        }

    @property
    def real_mode(self) -> bool:
        """True when this driver routes the dev step to the real Claude path."""
        return self._real_mode

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
    """Coordinator dispatch — materialize the bones-layer tickets."""
    coord = Coordinator(tickets=ctx.tickets, project_root=ctx.project_root)
    await coord.materialize_ready_tickets()


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
    # non-empty diff for the existence check.
    body_tokens = sorted(ac_tokens) or ["bones", "tracer", "bullet", "spine"]
    body = (
        f"# {ticket.title}\n\n"
        f"# Mock dev commit — references AC tokens for the bones reviewer:\n"
        + " ".join(body_tokens)
        + "\n"
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
    # Bones success means resolving the ticket. The orchestrator's
    # post-review wiring lands in MVP (Track G3 two-cadence + Track F
    # cycle-completion), but bones needs SOMETHING to flip the ticket
    # to RESOLVED so the ticket_status assertion passes. Using the
    # critical-comment count as the gate mirrors the design's
    # "critical = block" rule (G10).
    has_critical = any(
        c.severity == "critical" for c in comments
    )
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
