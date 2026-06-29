"""Singleton orchestrator — tracks tickets, runs agents, dispatches bus events."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.code_metrics import ChangeMetrics
    from jig.coordinator import Coordinator
    from jig.events import EventEmitter
    from jig.models import PhaseConfig, WorkflowConfig
    from jig.prompt_registry import PromptRegistry
    from jig.reviewers.comment import ReviewerComment
    from jig.ticket import Ticket

from jig.runtime.contract import AgentRunResult, RunAgent
from jig.runtime.real import RealRunAgent
from jig.stall_detector import StallDetector
from jig.analytics.emitter import EventEmitter as AnalyticsEmitter
from jig.analytics.events import (
    AgentCompleted,
    AgentSpawned,
    TicketGraphImpact,
    TicketStateChanged,
)
from jig.analytics.store import AnalyticsStore
from jig.config import (
    DeadlockSection,
    OrchestratorSection,
    ProfileSection,
    load_config,
)
from jig.deadlock import sweep_blocking_entries
from jig.dev_env.orchestrator_hook import (
    DevProvisioningError,
    build_fixture_env,
    cleanup_for_agent,
    provision_for_agent,
)
from jig.logging_setup import _phase_var, _role_var, _ticket_id_var
from jig.persistence import load_role
from jig.project import Project, load_project
from jig.thread import Handoff, Note, SystemEvent
from jig.store import Message, MessageBus, MessageType
from jig.substrate.events import TicketCreated, TicketUpdated, decode_event
from jig.substrate.store_authority import StoreAuthority
from jig.store.check_results import CheckResultsStore
from jig.store.review_comments import ReviewCommentsStore
from jig.store.checkpoints import CheckpointStore
from jig.store.finding_acks import FindingAck, FindingAcksStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus
from jig.fix_loop_bundle import build_fix_loop_bundle, build_verify_bundle
from jig.reviewer_routing import (
    _most_recent_phase_with_role,
    _route_blocking_comments,
)

_logger = logging.getLogger(__name__)

# Wall-clock cadence between deadlock sweeps. Shorter than both
# thresholds so a sweep always runs within a useful delay of an
# entry crossing T1, but long enough to not burn cycles on a
# healthy project. Tests that want fast iteration create tight
# sweeps by calling ``sweep_blocking_entries`` directly.
DEADLOCK_SWEEP_INTERVAL_S = 60.0

# How often the orchestrator reloads tickets appended by other processes (the
# jig issue CLI / standalone MCP) and re-runs the ready-scan. The store is
# in-memory after load(), so without this a live orchestrator never sees an
# externally-created (then approved) issue. Off the dispatch hot path.
RECONCILE_INTERVAL_S = 30.0

# Statuses that mean a ticket is still in flight, so the post-run analyzer
# must NOT treat the project as complete. PROPOSED is non-terminal: a
# front-door issue awaiting operator approval is unfinished work, not a done
# project — omitting it would let a project of only-proposed tickets emit
# project_complete prematurely. Exception: ``review-notable`` proposed
# issues are an operator triage backlog by design (binary severity files
# them for every notable finding) and must never hold project_complete
# hostage — see _counts_toward_completion.
_NON_TERMINAL_ANALYZER_STATUSES = frozenset(
    {
        TicketStatus.PROPOSED,
        TicketStatus.OPEN,
        TicketStatus.IN_PROGRESS,
        TicketStatus.BLOCKED,
        TicketStatus.NEEDS_INFO,
        TicketStatus.MERGE_CONFLICT,
    }
)


def _counts_toward_completion(ticket) -> bool:
    """Whether a non-terminal ticket should keep the project 'incomplete'.

    Review-notable proposed issues are triage backlog, not pipeline work.
    """
    return not (
        ticket.status == TicketStatus.PROPOSED and "review-notable" in ticket.labels
    )


# Statuses the stuck-project watchdog treats as "work that should be
# moving". NEEDS_INFO and PROPOSED are excluded: both are legitimately
# waiting on the operator, which is quiet-but-alive, not stuck.
_STUCK_WATCH_STATUSES = frozenset(
    {
        TicketStatus.OPEN,
        TicketStatus.IN_PROGRESS,
        TicketStatus.BLOCKED,
        TicketStatus.MERGE_CONFLICT,
    }
)

# How long the stuck condition (nothing running, nothing ready, watchable
# work outstanding) must hold across reconcile ticks before project_stuck
# fires. Two ticks of RECONCILE_INTERVAL_S plus margin — long enough to
# ride out the gap between one ticket finishing and its dependent being
# scheduled.
STUCK_GRACE_SECONDS = 120.0

# Structured reason stamped on tickets failed because a dependency failed
# (transitive cascade). Consumers must check status (FAILED) before
# interpreting this field — BLOCKED tickets use block_reason too.
DEP_FAILED_REASON = "dependency-failed"


# review-severity-binary §5/6 — SA adjudication triggers (initial
# values, revisited against ReviewFindingPersisted eval data).
# A blocking finding that survived this many fix attempts escalates to
# the SA; at most MAX_SA_ESCALATIONS adjudications run per ticket
# review phase, after which persistent blockers fail the ticket
# directly (the SA has had its say).
SURVIVAL_THRESHOLD = 2
MAX_SA_ESCALATIONS = 2


@dataclass
class AdjudicationOutcome:
    """Result of one SA adjudication run (review-severity-binary §6)."""

    fail: bool = False
    dismissed_keys: set[str] = dataclass_field(default_factory=set)
    guidance: list[dict] = dataclass_field(default_factory=list)


def _persistence_key(comment: "ReviewerComment") -> str:
    """Coarse cross-round identity for a blocking finding.

    Deliberately coarser than ``signature_of`` (drops the line/contract
    discriminator): a fix changes the very lines a finding points at, so
    line-anchored identity misses on exactly the rounds that matter.
    Over-matching collapses same-type findings in one file — that errs
    toward counting persistence sooner, and (step 6) the SA adjudicates.
    """
    return f"{comment.reviewer}|{comment.type}|{comment.file or ''}"


def _kill_orphan_claude_processes(project_path: Path) -> int:
    """SIGTERM any ``claude`` CLI subprocess whose CWD is inside the
    project's worktrees directory. Returns the number of PIDs signalled.

    Uses lsof; falls back to a no-op on platforms where lsof is unavailable.
    Runs synchronously — callers must offload to a thread executor.
    """
    worktrees_root = project_path / ".jig" / "worktrees"
    if not worktrees_root.is_dir():
        return 0
    killed = 0
    try:
        for child in worktrees_root.iterdir():
            if not child.is_dir():
                continue
            # Use lsof without +D to avoid expensive recursive dir scans;
            # -d cwd with an exact path finds processes whose cwd == child.
            result = subprocess.run(
                ["lsof", "-d", "cwd", "-Fp", "--", str(child)],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if not line.startswith("p"):
                    continue
                try:
                    pid = int(line[1:])
                except ValueError:
                    continue
                cmd = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
                # Match only processes whose argv[0] basename is "claude"
                # so we don't accidentally SIGTERM unrelated tools that happen
                # to have "claude" elsewhere in their command line.
                cmd_basename = os.path.basename(cmd.split()[0]) if cmd.split() else ""
                if cmd_basename != "claude":
                    continue
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except OSError:
                    pass
    except FileNotFoundError:
        pass  # lsof not available
    return killed


def _summarize_critical_note(comments: list) -> str:
    """Compose a short Note body summarising a batch of critical comments.

    Used by the review-federation gate when one or more critical
    reviewer comments flip the ticket to FAILED. The Note carries the
    operator-facing summary (reviewer ids + count); the full comment
    bodies live in the ``ReviewCommentsStore``. Keep it scannable —
    triage UX reads this in a list.
    """
    if not comments:
        return "Review federation found no critical comments."
    by_reviewer: dict[str, int] = {}
    for c in comments:
        by_reviewer[c.reviewer] = by_reviewer.get(c.reviewer, 0) + 1
    parts = [f"{count} from {reviewer}" for reviewer, count in by_reviewer.items()]
    return (
        f"Review federation flagged {len(comments)} critical comment(s); "
        f"ticket marked FAILED with reason `reviewer-critical`. "
        f"Sources: {'; '.join(parts)}. Operator addresses the comments "
        f"(see ReviewCommentsStore) and re-runs the federation."
    )


def _notable_sig_label(signature: tuple) -> str:
    """Deterministic ``sig:<digest>`` label for a finding signature.

    Stamped onto notable-derived proposed issues so dedup survives
    daemon restarts: a notable re-posted in a later cycle maps to the
    same label and is skipped.
    """
    import hashlib

    digest = hashlib.sha1(repr(signature).encode("utf-8")).hexdigest()[:12]
    return f"sig:{digest}"


class DependencyMergeError(RuntimeError):
    """Raised when a dependency branch can't be merged into a ticket's
    worktree. The orchestrator converts this into a typed ticket
    failure rather than running the agent against an inconsistent
    tree.
    """

    def __init__(self, *, ticket_id: str, dep_id: str, dep_branch: str) -> None:
        self.ticket_id = ticket_id
        self.dep_id = dep_id
        self.dep_branch = dep_branch
        super().__init__(
            f"could not merge dependency branch {dep_branch!r} "
            f"(from ticket {dep_id!r}) into worktree for ticket "
            f"{ticket_id!r}"
        )


class Orchestrator:
    """Singleton orchestrator per project process.

    Runs three concurrent asyncio loops over the shared bus and stores:
      - service loop: external wake-ups on the "orchestrator" topic
      - per-ticket loops: one ``_run_ticket`` task per in-flight top-level ticket
      - dispatch loop: spawns fresh agents for unaddressed bus events
    """

    def __init__(
        self,
        project_path: Path,
        emitter: "EventEmitter | None" = None,
        prompt_registry: "PromptRegistry | None" = None,
        run_agent: "RunAgent | None" = None,
    ) -> None:
        self._project_path = project_path
        self._emitter = emitter
        self._prompt_registry = prompt_registry
        # Epic 3 MVP: all spawns route through the RunAgent seam (not agent.py
        # directly), so headless evals can inject a Fixture/Recorded RunAgent.
        # Production default wraps the real spawn+sandbox+MCP path.
        self._run_agent: RunAgent = run_agent or RealRunAgent(emitter=emitter)
        self._project: Project | None = None
        # Composition root (ADR-0001): owns + vends the typed domain stores.
        # ``self.tickets`` / ``self.threads`` / … below are authority-sourced
        # aliases set in ``startup()``.
        self.store: StoreAuthority | None = None
        self.tickets: TicketStore | None = None
        self.threads: ThreadStore | None = None
        self.checkpoints: CheckpointStore | None = None
        self.memory: MemoryStore | None = None
        self.bus: MessageBus | None = None
        self.check_results: CheckResultsStore | None = None
        # Review-routing step 8: held at instance scope to track lifecycle
        # alongside the other stores. The blocked-phase router still calls
        # load() before each query because reviewer_mcp writes through
        # separate store instances — see _route_blocked_phase.
        self.review_comments: ReviewCommentsStore | None = None
        # v2 analytics — append-only event capture for the consumer half
        # described in docs/v2.0/analytics/. Initialized in startup once the
        # store directory exists.
        self.analytics: AnalyticsStore | None = None
        self._analytics_emitter: AnalyticsEmitter | None = None

        self._running_tickets: dict[str, asyncio.Task] = {}
        # Serializes ready-scan scheduling. _handle_schedule checks membership
        # in _running_tickets then awaits several times before registering the
        # task; without this lock two concurrent schedulers (the dispatch loop
        # and the reconcile tick) could both pass the check and double-dispatch
        # the same ticket, leaking one task.
        self._schedule_lock = asyncio.Lock()
        self._live_subscribers: dict[tuple[str, str], asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._dispatch_task: asyncio.Task | None = None
        self._service_task: asyncio.Task | None = None
        self._deadlock_task: asyncio.Task | None = None
        self._stall_task: asyncio.Task | None = None
        self._analyzer_task: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._stall_detector: StallDetector = StallDetector()
        self._analyzer_last_terminal_ids: frozenset[str] = frozenset()
        # Stuck-project watchdog state (see _check_stuck): monotonic
        # timestamp of the first reconcile tick that observed the stuck
        # condition, and the ticket-id set already reported so the
        # event fires once per distinct dead-end.
        self._stuck_since: float | None = None
        self._stuck_emitted_for: frozenset[str] | None = None
        # review-severity-binary §4 — worktree HEAD at the end of each
        # review round, keyed (ticket_id, phase_name). Re-review rounds
        # use it as the diff base so reviewers see only the fix delta.
        # In-memory by design: a daemon restart falls back to the
        # full-diff base (degraded to today's behavior, never wrong).
        self._last_reviewed_commit: dict[tuple[str, str], str] = {}
        # review-severity-binary §5 — persistence counting (observe-only
        # at this step). Per (ticket_id, phase_name): survival count per
        # blocking-finding persistence key, plus the previous blocked
        # round's key set. A key increments only when it survives a fix
        # attempt (present in consecutive blocked rounds); absent keys
        # reset; fresh keys enter at zero. In-memory: a restart resets
        # counts, degrading to extra fix rounds, never a wrong failure.
        self._survival_counts: dict[tuple[str, str], dict[str, int]] = {}
        self._prev_blocking_keys: dict[tuple[str, str], set[str]] = {}
        # Phase 5 Task L thresholds — loaded from config at startup
        # so shutdown/emergency_reset can read them without a second
        # config parse.
        self._deadlock_cfg: DeadlockSection = DeadlockSection()
        # Orchestrator-level runtime knobs. ``run_review_federation``
        # gates ticket resolution on the federation pass per design;
        # default ``True`` so the gate ships on every project unless
        # the operator explicitly opts out via ``.jig/config.yaml``.
        # See ``OrchestratorSection`` for full semantics.
        self._orchestrator_cfg: OrchestratorSection = OrchestratorSection()
        # review-severity-binary §5/6 — profile config (sa_role) for SA
        # adjudication spawns. Defaults cover tests/hand-wired setups.
        self._profile_cfg: ProfileSection = ProfileSection()
        # Lazy-constructed Coordinator wired to the orchestrator's
        # stores. Built on first access via the ``coordinator``
        # property and reused for the orchestrator's lifetime so the
        # review-federation gate's notable→DEFERRED path doesn't pay
        # a per-call construction cost.
        self._coordinator: "Coordinator | None" = None
        self._running = False

    @property
    def coordinator(self) -> "Coordinator":
        """Lazy-construct a Coordinator wired to the orchestrator's stores.

        Used by the review-federation gate's notable→DEFERRED branch
        (``apply_severity_disposition``) so a notable comment can land
        in the deferred queue without the orchestrator threading a
        Coordinator through every dispatch call. Stays singleton for
        the orchestrator's lifetime — re-entries return the cached
        instance.

        Raises ``RuntimeError`` when stores aren't loaded yet — pre-
        startup access is a programming error rather than a silent
        no-op (the caller would otherwise queue defers into a half-
        baked Coordinator).
        """
        from jig.coordinator import Coordinator as _Coordinator

        if self.tickets is None:
            raise RuntimeError(
                "Orchestrator not started — call startup() before accessing coordinator"
            )
        if self._coordinator is None:
            self._coordinator = _Coordinator(
                tickets=self.tickets,
                project_root=self._project_path,
            )
        return self._coordinator

    @property
    def is_configured(self) -> bool:
        """True once stores are loaded and dispatch loops are running."""
        return self._project is not None and self._running

    async def startup(self) -> None:
        try:
            try:
                self._project = load_project(self._project_path)
            except FileNotFoundError:
                # Unconfigured mode: no .jig/config.yaml yet. The WebSocket
                # server still runs and serves /init so the TUI can bootstrap
                # a new project. Stores and dispatch loops are skipped until
                # reload() is called after init completes.
                _logger.info(
                    "orchestrator starting in unconfigured mode (no project at %s)",
                    self._project_path,
                )
                self._running = False
                return
            store_dir = self._project_path / ".jig" / "store"
            store_dir.mkdir(parents=True, exist_ok=True)
            # Composition root (ADR-0001): StoreAuthority owns construction +
            # loading of the domain stores; the attributes below are
            # authority-sourced aliases (same instances), so the URI write path
            # and runtime share one TicketStore + its callbacks. Bus + analytics
            # are substrate infra, constructed here.
            store = self.store = StoreAuthority(self._project_path)
            self.bus = MessageBus(store_dir / "messages.jsonl")
            self.analytics = AnalyticsStore(store_dir / "analytics.jsonl")
            await asyncio.gather(
                store.load(),
                self.bus.load(),
                self.analytics.load(),
            )
            self.tickets = store.tickets
            self.threads = store.threads
            self.checkpoints = store.checkpoints
            self.memory = store.memory
            self.check_results = store.check_results
            self.review_comments = store.review_comments
            self._analytics_emitter = AnalyticsEmitter(self.analytics)
            self.tickets.set_status_change_callback(self._on_ticket_status_change)
            from jig.ticket_events import wire_create_publisher

            wire_create_publisher(self.tickets, self.bus, sender="orchestrator")
            # Phase 5 Task L: load deadlock thresholds from
            # `.jig/config.yaml`. Missing config (fresh install,
            # tests) falls back to the shipped defaults rather
            # than failing startup.
            try:
                cfg = load_config(self._project_path)
                self._deadlock_cfg = cfg.deadlock
                self._orchestrator_cfg = cfg.orchestrator
                self._profile_cfg = cfg.profile
            except FileNotFoundError:
                self._deadlock_cfg = DeadlockSection()
                self._orchestrator_cfg = OrchestratorSection()
                self._profile_cfg = ProfileSection()
            except Exception:
                _logger.warning(
                    "could not load config (deadlock/orchestrator/profile); "
                    "using defaults",
                    exc_info=True,
                )
                self._deadlock_cfg = DeadlockSection()
                self._orchestrator_cfg = OrchestratorSection()
                self._profile_cfg = ProfileSection()
            self._running = True
            await self._resume_in_progress()
            await self._ensure_planning_ticket()
            await self._start_ready_tickets()
            await self._maybe_run_analyzer()
            self._dispatch_task = asyncio.create_task(self._run_dispatch_loop())
            self._service_task = asyncio.create_task(self._run_service_loop())
            self._deadlock_task = asyncio.create_task(self._run_deadlock_loop())
            self._stall_task = asyncio.create_task(self._run_stall_loop())
            self._reconcile_task = asyncio.create_task(self._run_reconcile_loop())
        except Exception:
            await self._emergency_reset()
            raise

    async def reload(self) -> None:
        """Re-run startup after a project has been initialized.

        Called by cmd_init after a successful /init so the daemon
        transitions from unconfigured to configured mode without restart.

        When already configured (e.g. --force re-init on an existing
        project), startup() is skipped but we still re-check for the
        planning ticket and start any ready tickets — init may have just
        created the planning ticket with no bus event to wake the service
        loop.
        """
        if self._project is not None and self._running:
            await self._ensure_planning_ticket()
            await self._start_ready_tickets()
            return
        self._analyzer_last_terminal_ids = frozenset()
        await self.startup()

    async def list_active_agents(self) -> list[dict]:
        """Return currently-running agents in ``agent_thinking`` payload shape.

        Consumed by ``ws_server._build_snapshot`` for the ``agents`` topic so
        the TUI's Activity sidebar can be restored from the snapshot on
        (re)subscribe without waiting for the next live heartbeat. Source of
        truth is ``StallDetector.in_flight_agents`` — already updated on
        every agent spawn/finish in ``_run_agent_with_analytics``.
        """
        now = time.monotonic()
        agents: list[dict] = []
        for agent_key, started in self._stall_detector.in_flight_agents.items():
            ticket_id, _, role = agent_key.partition(":")
            agents.append(
                {
                    "role": role,
                    "ticket_id": ticket_id,
                    "elapsed": int(now - started),
                    "active": True,
                }
            )
        return agents

    # ---- analytics --------------------------------------------------------

    def _on_ticket_status_change(
        self, ticket_id: str, from_state: str | None, to_state: str
    ) -> None:
        """Status-change callback wired into TicketStore for analytics."""
        if to_state == "needs_info":
            self._stall_detector.record_needs_info(ticket_id)
        else:
            self._stall_detector.clear_needs_info(ticket_id)
        if self._analytics_emitter is None:
            return
        self._analytics_emitter.emit_nowait(
            TicketStateChanged(
                ticket_id=ticket_id,
                from_state=from_state,
                to_state=to_state,
            )
        )

    async def _run_agent_with_analytics(self, ctx, *, spawned_by: str = "orchestrator"):
        """Wrap the RunAgent seam with AgentSpawned/AgentCompleted emission.

        Routes the spawn through ``self._run_agent`` (Epic 3 MVP) and returns its
        ``AgentRunResult`` (a field-superset of the legacy ``RunAgentResult``, so
        existing callers are unaffected). Bones
        scope: model is recorded as ``"default"`` since per-spawn model
        selection isn't yet plumbed through the orchestrator. Duration is
        wall-clock from the spawn-emit point.

        Track E MVP: provisions per-agent dev-env namespaces (Postgres
        schema, NATS subject prefix, etc.) before invoking ``run_agent``
        and cleans them up after. Best-effort — provisioning failures
        leave ``ctx.extra_env`` empty rather than failing the spawn;
        cleanup failures are logged and swallowed (mirrors the
        analytics-drain-on-shutdown pattern).
        """
        agent_id = f"{ctx.role}:{ctx.ticket.id[:8]}"
        emitter = self._analytics_emitter
        if emitter is not None:
            emitter.emit_nowait(
                AgentSpawned(
                    agent_id=agent_id,
                    role=ctx.role,
                    model="default",
                    ticket_id=ctx.ticket.id,
                    spawned_by=spawned_by,
                )
            )
            # Phase 4.11 — emit graph impact at spawn time so the
            # Quartermaster can surface complex-ticket patterns. Best-effort:
            # projects without architecture.yaml produce no event.
            try:
                from jig.graph.derive import build_graph, ticket_impact

                _graph = build_graph(self._project_path)
                _impact = ticket_impact(_graph, ctx.ticket.id)
                emitter.emit_nowait(
                    TicketGraphImpact(
                        ticket_id=ctx.ticket.id,
                        crossed_boundaries=_impact.crossed_boundaries,
                        touched_node_count=len(_impact.touched),
                        consumer_count=sum(len(v) for v in _impact.consumers.values()),
                        exercised_tracer_count=len(_impact.exercised_tracers),
                    )
                )
            except Exception:
                pass
        # Track E MVP — provision per-agent namespaces and stamp the
        # connection-string env-var map onto the context. Absence of a
        # manifest (the common case in bones / pre-SA projects) yields
        # an empty map; absence of services likewise.
        #
        # SF-1: when the manifest declares services and provisioning
        # fails, mark the ticket ``failed`` instead of running the agent
        # against default services with an empty env map. The thread
        # entry surfaces the cause so the operator sees what broke.
        try:
            env_map = await provision_for_agent(
                self._project_path,
                agent_id=agent_id,
                ticket_id=ctx.ticket.id,
            )
        except DevProvisioningError as exc:
            _logger.error(
                "dev-env provisioning failed for ticket %s: %s",
                ctx.ticket.id,
                exc,
            )
            await ctx.threads.post(
                SystemEvent(
                    ticket_id=ctx.ticket.id,
                    author="orchestrator",
                    event_type="provisioning_failed",
                    content=str(exc),
                )
            )
            await ctx.tickets.update(ctx.ticket.id, status=TicketStatus.FAILED)
            return AgentRunResult(
                status="failed",
                final_text=f"dev-env provisioning failed: {exc}",
            )
        # Block 2 — also stamp ``JIG_FIXTURE_MODE`` per
        # ``docs/v2.0/dev-environment/design.md`` §"External-API recorded
        # fixtures": SPIKE tickets default to ``record_new`` (the
        # spike's job is to grow the fixture corpus); everything else
        # defaults to ``replay_only``. Merged into the same extra_env
        # map so it doesn't clobber the per-service URLs.
        fixture_env = build_fixture_env(ctx.ticket)
        merged_env = {**env_map, **fixture_env}
        if merged_env:
            existing = dict(getattr(ctx, "extra_env", None) or {})
            existing.update(merged_env)
            try:
                ctx.extra_env = existing
            except AttributeError:
                # Test stubs may use slots / freeze; in that case the
                # injection is best-effort — the agent still runs.
                _logger.debug(
                    "could not stamp extra_env on ctx (frozen / slots?)",
                )
        # Block 2 — stamp the analytics emitter onto the context so
        # ``run_agent`` can forward it into ``create_agent_mcp_server``
        # for MCP-tool handler emission (ontology edits etc.). Best-
        # effort: test stubs that use slots / freeze ignore the assign.
        try:
            ctx.analytics_emitter = self._analytics_emitter
        except AttributeError:
            _logger.debug(
                "could not stamp analytics_emitter on ctx (frozen / slots?)",
            )
        agent_key = f"{ctx.ticket.id}:{ctx.role}"
        ctx.on_thinking = lambda: self._stall_detector.record_heartbeat(agent_key)
        self._stall_detector.record_agent_start(agent_key)
        start = time.monotonic()
        result_status = "failed"
        cost_usd: float | None = None
        tokens_in: int | None = None
        tokens_out: int | None = None
        try:
            result = await self._run_agent(ctx)
            result_status = self._map_result_status(result.status)
            cost_usd = result.total_cost_usd
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            return result
        finally:
            self._stall_detector.record_agent_done(agent_key)
            if emitter is not None:
                emitter.emit_nowait(
                    AgentCompleted(
                        agent_id=agent_id,
                        status=result_status,  # type: ignore[arg-type]
                        duration_ms=int((time.monotonic() - start) * 1000),
                        cost_estimate_usd=cost_usd,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                )
            # Track F Final — check for mid-work tier promotion +
            # record calibration sample. Best-effort: swallow
            # exceptions so analytics drift can't kill a dispatch.
            await self._maybe_promote_tier_after_blocked(
                ctx,
                agent_id=agent_id,
                result_status=result_status,
            )
            await self._record_calibration_sample(
                ctx,
                result_status=result_status,
                duration_ms=int((time.monotonic() - start) * 1000),
                cost_usd=cost_usd,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            # Track E MVP — best-effort cleanup. ``success`` is keyed
            # off the mapped status (anything besides "success" goes
            # through the failure path so the operator can inspect
            # archived namespaces). cleanup_for_agent itself swallows
            # downstream provisioner errors per the design's "Cleanup
            # discipline" failure-mode-1 mitigation.
            await cleanup_for_agent(
                self._project_path,
                agent_id=agent_id,
                ticket_id=ctx.ticket.id,
                success=result_status == "success",
            )

    async def _maybe_promote_tier_after_blocked(
        self,
        ctx,
        *,
        agent_id: str,
        result_status: str,
    ) -> None:
        """Track F Final — auto-promote the ticket's tier on BLOCKED + signal.

        Conservative semantics per the v2-plan F Final note: we wait
        for the run to finish (no mid-stream kill) and persist the new
        tier so the next dispatch picks it up at the higher tier.

        No-op when:
        - Status is not BLOCKED (mapped from ``needs_info``).
        - The ticket is no longer in the OPEN/IN_PROGRESS lane (a
          status callback may have flipped it elsewhere).
        - No escalation signals tripped.
        - The current tier has no rung above it on the promotion ladder.
        """
        if result_status != "blocked":
            return
        if self.tickets is None or self.analytics is None:
            return
        try:
            ticket = await self.tickets.get(ctx.ticket.id)
            if ticket is None:
                return
            if ticket.status not in (TicketStatus.OPEN, TicketStatus.IN_PROGRESS):
                return
            from jig.auto_escalation import check_escalation_signals
            from jig.pm.tier_promotion import (
                decide_promotion,
                promote_ticket_tier,
            )

            signals = await check_escalation_signals(ctx.ticket.id, self.analytics)
            current_tier = ticket.dev_tier or "standard"
            target = decide_promotion(signals, current_tier)
            if target is None:
                return
            await promote_ticket_tier(
                self.tickets,
                ctx.ticket.id,
                target,
                reason=signals[0].detail or signals[0].kind,
                signal=signals[0],
                emitter=self._analytics_emitter,
                agent_id=agent_id,
            )
        except Exception:
            _logger.warning(
                "tier-promotion check failed for ticket %s",
                ctx.ticket.id,
                exc_info=True,
            )

    async def _record_calibration_sample(
        self,
        ctx,
        *,
        result_status: str,
        duration_ms: int,
        cost_usd: float | None,
        tokens_in: int | None,
        tokens_out: int | None,
    ) -> None:
        """Track F Final — append one calibration sample after each agent run.

        Best-effort: swallows exceptions and logs. The sample carries
        the ticket's size + tier + layer plus the observed turn /
        tool-call / duration / cost numbers so ``current_envelopes``
        can compute median/p90 envelopes per S/M/L bucket.
        """
        if self.tickets is None or self.analytics is None:
            return
        try:
            ticket = await self.tickets.get(ctx.ticket.id)
            if ticket is None:
                return
            from jig.pm.calibration import (
                CalibrationStore,
                record_completion_sample,
            )

            store = CalibrationStore(self._project_path)
            await store.load()
            await record_completion_sample(
                ticket=ticket,
                status=result_status,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                store=store,
                analytics=self.analytics,
                emitter=self._analytics_emitter,
            )
        except Exception:
            _logger.warning(
                "calibration sample recording failed for ticket %s",
                ctx.ticket.id,
                exc_info=True,
            )

    @staticmethod
    def _map_result_status(s: str) -> str:
        """Map RunAgentResult.status to AgentCompleted.status Literal."""
        return {"needs_info": "blocked"}.get(s, s)

    async def _run_review_federation(self, ticket_id: str, ticket) -> None:
        """Run the review federation as a **gate** on ticket resolution.

        Per ``docs/v2.0/pm-workflow/design.md`` §"Severity tiers and
        disposition", the federation is not an observation hook — its
        outcome determines whether the ticket actually resolves:

        * ``critical`` comments → ticket FAILED with reason
          ``reviewer-critical``; a Note summarizing the criticals lands
          on the thread.
        * ``important`` comments → ticket BLOCKED with reason
          ``reviewer-important``; a ``Handoff(phase="sa-consult")`` is
          posted per important comment so the SA reviewer can engage
          via the orchestrator's existing dispatch path.
        * ``notable`` comments only (no critical / important) → ticket
          RESOLVED but deferred via the Coordinator (DEFERRED queue).
        * No blocking comments → ticket RESOLVED as today.

        This method is invoked from ``_on_ticket_completed`` while the
        ticket is in RESOLVED state (post-merge) and rolls the status
        back to FAILED / BLOCKED when blocking comments fire. The same
        observable contract applies whether you read the gate as
        "transition to RESOLVED only on federation pass" or "RESOLVED,
        then roll back on blocking comments".

        Crash policy: if ``dispatch_with_llm_spawn`` raises (transient
        SDK / network error during a reviewer agent spawn), retry
        once after a 2-second delay. If the retry also fails, mark
        the ticket FAILED with reason ``federation-error`` and emit a
        Note describing the failure — a misbehaving federation must
        never silently pass a ticket the operator expected gated.
        """
        from jig.reviewers import dispatch_with_llm_spawn
        from jig.reviewers.disposition import apply_severity_disposition

        worktree_path = self._project_path / ".jig" / "worktrees" / ticket_id
        worktree_arg = worktree_path if worktree_path.exists() else None
        base_ref = self._project.default_branch if self._project is not None else "main"

        # ---- run dispatch with single-retry policy --------------------
        try:
            by_reviewer = await dispatch_with_llm_spawn(
                ticket,
                self._project_path,
                self,
                worktree_path=worktree_arg,
                base_ref=base_ref,
            )
        except Exception:
            _logger.warning(
                "review federation raised for ticket %s; retrying once after 2s",
                ticket_id,
                exc_info=True,
            )
            try:
                await asyncio.sleep(2.0)
                by_reviewer = await dispatch_with_llm_spawn(
                    ticket,
                    self._project_path,
                    self,
                    worktree_path=worktree_arg,
                    base_ref=base_ref,
                )
            except Exception:
                _logger.error(
                    "review federation retry failed for ticket %s; "
                    "marking FAILED with federation-error",
                    ticket_id,
                    exc_info=True,
                )
                await self._fail_with_federation_error(ticket_id)
                return

        # ---- flatten comments + apply severity disposition ------------
        comments: list = []
        for value in by_reviewer.values():
            if isinstance(value, list):
                comments.extend(value)

        if not comments:
            # Clean federation pass — nothing more to do; the ticket
            # stays RESOLVED as the caller already set it.
            return

        if self.tickets is None or self.threads is None:
            # Defensive — federation can't run without stores; treat as
            # a no-op rather than tripping an AttributeError.
            return

        # Binary severity: notables never block and carry no ack
        # obligation — surface them as operator-gated proposed issues
        # regardless of what higher-severity findings do to the ticket.
        from jig.reviewers.comment import Severity as _Severity

        notables = [c for c in comments if c.severity == _Severity.NOTABLE.value]
        if notables:
            try:
                await self._file_notable_issues(ticket_id, notables)
            except Exception:
                _logger.warning(
                    "notable-issue conversion failed for ticket %s",
                    ticket_id,
                    exc_info=True,
                )

        result = await apply_severity_disposition(
            comments,
            ticket,
            self.tickets,
            threads=self.threads,
        )

        # ---- post-disposition status + reason fixup -------------------
        # ``apply_severity_disposition`` flips the ticket to FAILED for
        # criticals and posts the SA-consult Handoffs for importants,
        # but doesn't manage the BLOCKED-on-important transition or
        # the structured ``block_reason`` field — that's the
        # orchestrator's job since the disposition function is
        # ticket-store-agnostic by design.
        if result.blocked_by:
            await self.tickets.update(ticket_id, block_reason="reviewer-critical")
            await self.threads.post(
                Note(
                    ticket_id=ticket_id,
                    author="orchestrator",
                    text=_summarize_critical_note(result.blocked_by),
                )
            )
        elif result.consulted_sa:
            # Important comments → BLOCKED (re-using BLOCKED + a
            # structured reason rather than a new TicketStatus).
            await self._update_ticket_status(ticket_id, TicketStatus.BLOCKED)
            await self.tickets.update(ticket_id, block_reason="reviewer-important")
        # Notable-only path: ticket stays RESOLVED; the notables were
        # filed as proposed issues above.

    async def _run_review_phase_federation(
        self,
        ticket_id: str,
        ticket,
        worktree_path,
        phase: "PhaseConfig | None" = None,
        cycle: int = 0,
    ):
        """Run all review-federation agents in parallel and return a RunAgentResult.

        Called from the phase loop when ``phase.role == "review"`` and
        ``run_review_federation`` is enabled.  Replaces the single review
        agent with the full federation so all reviewers fire in the correct
        spec→test→dev→review→document order.

        ``phase`` (review-routing step 5): when provided, the phase's
        ``reviewers:`` list scopes which LLM reviewers fire. Empty list
        is meaningful — means "no LLM reviewers at this phase". ``None``
        falls back to legacy cadence-driven selection so callers without
        a phase context (the post-RESOLVE gate) keep working.

        Outcome mapping:
        - Any critical or important comment  → ``"blocked"`` (routes back
          to the nearest dev/writing phase via the existing retry path).
        - No blocking comments               → ``"success"`` (advances to
          the next phase, typically document).
        - Federation crash                   → ``"success"`` (fail-open;
          logged at WARNING so the operator can investigate without
          stalling the pipeline).
        """
        from jig.agent import RunAgentResult
        from jig.fix_loop_bundle import compute_reraised_acks
        from jig.reviewers import dispatch_with_llm_spawn
        from jig.reviewers.comment import Severity

        # fix-loop-context step 5: snapshot pre-federation state so we
        # can detect re-flagged signatures after dispatch completes.
        prior_comments_snapshot: list = []
        prior_acks_snapshot: list = []
        if self.review_comments is not None:
            await self.review_comments.load()
            prior_comments_snapshot = (
                await self.review_comments.for_ticket_chronological(ticket_id)
            )
            acks_path = self._project_path / ".jig" / "store" / "finding_acks.jsonl"
            acks_path.parent.mkdir(parents=True, exist_ok=True)
            _acks_store = FindingAcksStore(acks_path)
            await _acks_store.load()
            prior_acks_snapshot = await _acks_store.for_ticket(ticket_id)

        reviewers_list = phase.reviewers if phase is not None else None
        base_ref = self._project.default_branch if self._project is not None else "main"

        # review-severity-binary §4 — review invocation shaping.
        # First round (cycle 0): up to ``phase.review_passes`` sequential
        # passes; passes after the first are informed (they see this
        # cycle's findings so far and add coverage). Re-review rounds
        # (cycle > 0): single pass, diff based at the last-reviewed
        # commit so the reviewer sees only the fix delta. Missing
        # tracked commit (daemon restart) falls back to the full diff.
        delta_base: str | None = None
        phase_key = (ticket_id, phase.name) if phase is not None else None
        if cycle > 0 and phase_key is not None:
            delta_base = self._last_reviewed_commit.get(phase_key)
            if delta_base is not None:
                base_ref = delta_base
        passes = phase.review_passes if (phase is not None and cycle == 0) else 1

        all_comments: "list[ReviewerComment]" = []
        for pass_n in range(passes):
            informed_bundle: dict | None = None
            if pass_n > 0 and all_comments:
                informed_bundle = {
                    "findings": [
                        {
                            "file": c.file,
                            "line": c.line,
                            "severity": c.severity,
                            "prose": c.prose,
                        }
                        for c in all_comments
                    ]
                }
            try:
                by_reviewer = await dispatch_with_llm_spawn(
                    ticket,
                    self._project_path,
                    self,
                    worktree_path=worktree_path,
                    reviewers=reviewers_list,
                    cycle=cycle,
                    base_ref=base_ref,
                    phase_name=phase.name if phase is not None else None,
                    informed_findings=informed_bundle,
                    delta_base=delta_base,
                )
            except ValueError:
                # ValueError from dispatch_with_llm_spawn means a workflow
                # config error — unknown reviewer id. Don't swallow as a
                # transient crash; surface the misconfiguration immediately
                # so the operator fixes the YAML.
                raise
            except Exception:
                _logger.warning(
                    "review federation crashed for ticket %s; treating as clean pass",
                    ticket_id,
                    exc_info=True,
                )
                if all_comments:
                    # An earlier pass already posted findings; a later-pass
                    # crash must not discard them and wave a ticket with known
                    # blockers through. Stop multi-passing and let the gate
                    # below evaluate what we have (it records/clears
                    # persistence itself on the blocked/pass outcome).
                    break
                # Total crash (no findings yet) — treated as a clean pass, so
                # clear persistence too: a later re-entry must not count
                # pre-crash blockers as survivors and emit misleading
                # ReviewFindingPersisted events.
                self._clear_phase_persistence(phase_key)
                return RunAgentResult(
                    status="success",
                    final_text="Federation error — treated as clean pass (see logs).",
                )
            all_comments.extend(
                c for comments in by_reviewer.values() for c in comments
            )
            if passes > 1:
                _logger.info(
                    "review pass %d/%d for ticket %s: %d finding(s) so far",
                    pass_n + 1,
                    passes,
                    ticket_id,
                    len(all_comments),
                )

        # Record this round's reviewed commit so the next round (if the
        # gate blocks and a fix lands) reviews only the delta.
        if phase_key is not None:
            head = await self._worktree_head(worktree_path)
            if head is not None:
                self._last_reviewed_commit[phase_key] = head

        # fix-loop-context step 5: auto-reraised acks. For any new
        # comment that re-flags a previously addressed-but-not-resolved
        # signature, write an orchestrator-authored "reraised" row to
        # the FindingAcksStore so the audit trail captures the
        # recurrence even when the reviewer didn't explicitly call
        # mark_finding_resolved (or call anything at all).
        if all_comments and self.review_comments is not None:
            try:
                reraised = compute_reraised_acks(
                    prior_comments=prior_comments_snapshot,
                    new_comments=all_comments,
                    prior_acks=prior_acks_snapshot,
                    ticket_id=ticket_id,
                )
                if reraised:
                    acks_path = (
                        self._project_path / ".jig" / "store" / "finding_acks.jsonl"
                    )
                    acks_store = FindingAcksStore(acks_path)
                    await acks_store.load()
                    for ack in reraised:
                        await acks_store.append(ack)
            except Exception:
                # Non-fatal — the audit trail is best-effort; the
                # routing decision below still fires off the live
                # federation result.
                _logger.warning(
                    "fix-loop-context: failed to write reraised acks for ticket %s",
                    ticket_id,
                    exc_info=True,
                )

        # Binary severity: notables never block, never route, and carry
        # no ack obligation. Each distinct notable becomes an
        # operator-gated proposed issue instead. Best-effort — issue
        # filing must never affect the gate decision below.
        notables = [c for c in all_comments if c.severity == Severity.NOTABLE.value]
        if notables:
            try:
                await self._file_notable_issues(ticket_id, notables)
            except Exception:
                _logger.warning(
                    "notable-issue conversion failed for ticket %s",
                    ticket_id,
                    exc_info=True,
                )

        blocking = [
            c
            for c in all_comments
            if c.severity in (Severity.CRITICAL.value, Severity.IMPORTANT.value)
        ]
        # Drop hallucinated findings (file outside the issuing
        # reviewer's ``reads_glob``) BEFORE deciding whether the
        # review failed. Filtering only at the routing layer would
        # still flip this phase to ``blocked``, taking the ticket
        # down the retry path even if the only blockers were
        # hallucinations. Keeping the filter here means the review
        # passes cleanly when the survivor set is empty.
        if blocking:
            blocking = await self._filter_out_of_scope_comments(blocking)
        if blocking:
            # Binding SA dismissals (review-severity-binary §6): a key the
            # SA dismissed can never block this ticket again, even if the
            # reviewer re-posts it.
            blocking = await self._filter_dismissed_keys(ticket_id, blocking)

        if blocking:
            n_crit = sum(1 for c in blocking if c.severity == Severity.CRITICAL.value)
            n_imp = len(blocking) - n_crit
            _logger.info(
                "review federation blocked ticket %s: %d critical, %d important",
                ticket_id,
                n_crit,
                n_imp,
            )
            self._record_blocking_persistence(
                ticket_id=ticket_id,
                phase_key=phase_key,
                blocking=blocking,
                cycle=cycle,
            )
            return RunAgentResult(
                status="blocked",
                final_text=(
                    f"Review found {n_crit} critical and {n_imp} important issue(s). "
                    "Routing back to dev phase."
                ),
            )

        # Round converged — drop persistence state so a later re-entry to
        # this phase (or its reuse by another ticket id) starts clean.
        self._clear_phase_persistence(phase_key)

        _logger.info("review federation passed for ticket %s", ticket_id)
        return RunAgentResult(status="success", final_text="Review passed.")

    def _clear_phase_persistence(self, phase_key: "tuple[str, str] | None") -> None:
        """Drop the survival counters + prior-blocking-keys for a review
        phase, so a clean pass (or a crash treated as one) doesn't leave
        stale state for a later re-entry."""
        if phase_key is None:
            return
        self._survival_counts.pop(phase_key, None)
        self._prev_blocking_keys.pop(phase_key, None)

    async def _filter_dismissed_keys(self, ticket_id: str, blocking: list) -> list:
        """Drop blocking comments whose persistence key carries a binding
        SA dismissal (FindingAck kind="dismissed"). Acks are
        JSONL-persisted, so the binding survives daemon restarts even
        though the survival counters do not."""
        acks_path = self._project_path / ".jig" / "store" / "finding_acks.jsonl"
        if not acks_path.exists():
            return blocking
        acks_store = FindingAcksStore(acks_path)
        await acks_store.load()
        dismissed_keys = {
            a.persistence_key
            for a in await acks_store.for_ticket(ticket_id)
            if a.kind == "dismissed" and a.persistence_key
        }
        if not dismissed_keys:
            return blocking
        kept = [c for c in blocking if _persistence_key(c) not in dismissed_keys]
        dropped = len(blocking) - len(kept)
        if dropped:
            _logger.info(
                "filtered %d blocking finding(s) with binding SA dismissals "
                "on ticket %s",
                dropped,
                ticket_id,
            )
        return kept

    def _keys_past_survival_threshold(
        self, ticket_id: str, phase_name: str
    ) -> set[str]:
        """Persistence keys whose survival count reached the SA threshold."""
        counts = self._survival_counts.get((ticket_id, phase_name), {})
        return {k for k, v in counts.items() if v >= SURVIVAL_THRESHOLD}

    async def _run_sa_adjudication(
        self,
        *,
        ticket_id: str,
        ticket,
        phase_name: str,
        worktree: Path,
        escalated_keys: set[str],
        cycle: int,
        cap_trip: bool,
    ) -> "AdjudicationOutcome":
        """Spawn the profile's SA to adjudicate persistent blocking findings.

        Escalation is by persistence key; each key is presented as its
        latest RC-N occurrence with full history. Verdict handling:

        - ``dismissed`` → ``FindingAck(kind="dismissed", persistence_key=…)``
          persisted; the gate filter makes it binding.
        - ``uphold_guidance`` → survival count resets; guidance rides into
          the next fix bundle. A missing verdict for an escalated finding
          is treated the same with no guidance (conservative: never waves
          a finding through, never fails on SA sloppiness alone).
        - ``uphold_fail`` → the outcome fails the ticket.

        Fail-closed: a spawn error or an SA that returns zero verdicts
        fails the ticket (the pre-SA behavior at the cap), loudly.
        """
        from jig.finding_ids import compute_finding_ids, signature_of
        from jig.persistence import load_role
        from jig.runtime import AgentSpawnContext, SpawnReason

        fail_closed = AdjudicationOutcome(fail=True)

        if (
            self.review_comments is None
            or self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            _logger.error("SA adjudication: orchestrator not fully started")
            return fail_closed

        # When the round cap trips, every outstanding blocking key is on
        # the table, not just the persistent ones.
        if cap_trip:
            escalated_keys = escalated_keys | self._prev_blocking_keys.get(
                (ticket_id, phase_name), set()
            )
        if not escalated_keys:
            _logger.error("SA adjudication: no escalated keys for %s", ticket_id)
            return fail_closed

        await self.review_comments.load()
        all_comments = await self.review_comments.for_ticket_chronological(ticket_id)
        ids = compute_finding_ids(all_comments)
        acks_path = self._project_path / ".jig" / "store" / "finding_acks.jsonl"
        acks_path.parent.mkdir(parents=True, exist_ok=True)
        acks_store = FindingAcksStore(acks_path)
        await acks_store.load()
        all_acks = await acks_store.for_ticket(ticket_id)

        counts = self._survival_counts.get((ticket_id, phase_name), {})
        bundle_findings: list[dict] = []
        escalated_map: dict[str, str] = {}  # rc_n -> persistence key
        for key in sorted(escalated_keys):
            occurrences = [
                c
                for c in all_comments
                if c.severity in ("critical", "important")
                and _persistence_key(c) == key
            ]
            if not occurrences:
                continue
            latest = occurrences[-1]
            rc_n = ids.get(signature_of(latest))
            if rc_n is None:
                _logger.warning(
                    "SA adjudication: no RC-N for key %s on ticket %s — skipping",
                    key,
                    ticket_id,
                )
                continue
            escalated_map[rc_n] = key
            occ_fids = {
                ids.get(signature_of(c))
                for c in occurrences
                if ids.get(signature_of(c)) is not None
            }
            history = [
                {
                    "cycle": c.cycle,
                    "kind": "raised",
                    "author": c.reviewer,
                    "prose": c.prose,
                }
                for c in occurrences
            ] + [
                {
                    "cycle": a.cycle,
                    "kind": a.kind,
                    "author": a.author,
                    "prose": a.prose,
                }
                for a in all_acks
                if a.finding_id in occ_fids
            ]

            def _history_cycle(entry: dict[str, object]) -> int:
                # ``cycle`` is sourced from ReviewerComment.cycle / FindingAck.cycle,
                # both typed ``int`` — the isinstance is a typing narrowing, not a
                # runtime conversion. Assert the invariant rather than coercing a
                # non-int (which would silently mask a malformed history entry).
                cycle = entry["cycle"]
                assert isinstance(cycle, int), f"history cycle not int: {cycle!r}"
                return cycle

            history.sort(key=_history_cycle)
            bundle_findings.append(
                {
                    "finding_id": rc_n,
                    "file": latest.file,
                    "severity": latest.severity,
                    "reviewer": latest.reviewer,
                    "prose": latest.prose,
                    "survival_count": counts.get(key, 0),
                    "history": history,
                }
            )
        if not bundle_findings:
            _logger.error(
                "SA adjudication: escalated keys resolved to no findings on %s",
                ticket_id,
            )
            return fail_closed

        sa_role = self._profile_cfg.sa_role
        try:
            role_cfg = load_role(self._project_path, sa_role)
        except FileNotFoundError:
            _logger.error(
                "SA adjudication: role config %r not found — failing closed",
                sa_role,
            )
            return fail_closed

        collector: dict = {"escalated": escalated_map, "verdicts": {}}
        ctx = AgentSpawnContext(
            role=sa_role,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.SA_ADJUDICATION,
            ticket=ticket,
            parent=None,
            worktree_path=worktree,
            project=self._project,
            tickets=self.tickets,
            threads=self.threads,
            memory=self.memory,
            bus=self.bus,
            checkpoints=self.checkpoints,
            cycle=cycle,
            adjudication_bundle={"findings": bundle_findings},
            adjudication_collector=collector,
        )
        _logger.info(
            "SA adjudication for ticket %s: %d finding(s)%s",
            ticket_id,
            len(bundle_findings),
            " (round-cap trip)" if cap_trip else "",
        )
        try:
            await self._run_agent_with_analytics(ctx)
        except Exception:
            _logger.error(
                "SA adjudication spawn failed for ticket %s — failing closed",
                ticket_id,
                exc_info=True,
            )
            return fail_closed

        verdicts: dict = collector.get("verdicts", {})
        if not verdicts:
            _logger.error(
                "SA adjudication returned no verdicts for ticket %s — failing closed",
                ticket_id,
            )
            return fail_closed

        outcome = AdjudicationOutcome()
        phase_key = (ticket_id, phase_name)
        for rc_n, key in escalated_map.items():
            v = verdicts.get(rc_n)
            verdict = (v or {}).get("verdict", "uphold_guidance")
            rationale = (v or {}).get("rationale", "(no verdict recorded)")
            if verdict == "uphold_fail":
                outcome.fail = True
            elif verdict == "dismissed":
                await acks_store.append(
                    FindingAck(
                        ticket_id=ticket_id,
                        finding_id=rc_n,
                        kind="dismissed",
                        author=sa_role,
                        cycle=cycle,
                        prose=rationale,
                        persistence_key=key,
                    )
                )
                outcome.dismissed_keys.add(key)
                self._survival_counts.get(phase_key, {}).pop(key, None)
            else:  # uphold_guidance (explicit or missing verdict)
                guidance = (v or {}).get("guidance") or rationale
                outcome.guidance.append({"finding_id": rc_n, "guidance": guidance})
                # Survival resets; the granted round is the new baseline.
                counts = self._survival_counts.get(phase_key)
                if counts is not None and key in counts:
                    counts[key] = 0
        if self._analytics_emitter is not None:
            from jig.analytics.events import SAAdjudication

            self._analytics_emitter.emit_nowait(
                SAAdjudication(
                    ticket_id=ticket_id,
                    finding_ids=sorted(escalated_map),
                    verdicts=[
                        (verdicts.get(rc) or {}).get("verdict", "uphold_guidance")
                        for rc in sorted(escalated_map)
                    ],
                    cap_trip=cap_trip,
                    cycle=cycle,
                )
            )
        return outcome

    def _record_blocking_persistence(
        self,
        *,
        ticket_id: str,
        phase_key: tuple[str, str] | None,
        blocking: "list[ReviewerComment]",
        cycle: int,
    ) -> None:
        """Track which blocking findings survived a fix attempt.

        Observe-only (review-severity-binary §5): a finding's coarse
        persistence key increments when it appears in consecutive
        blocked rounds — the dev attempted a fix and the reviewer
        re-raised it. Fresh keys enter at zero; a key absent from this
        round resets to zero (flapping findings are bounded by the
        round cap instead). Counts feed ReviewFindingPersisted
        analytics; step 6 attaches the SA-escalation trigger.
        """
        if phase_key is None:
            return
        prev_keys = self._prev_blocking_keys.get(phase_key, set())
        counts = self._survival_counts.setdefault(phase_key, {})
        current_keys = {_persistence_key(c) for c in blocking}

        for key in list(counts):
            if key not in current_keys:
                counts[key] = 0
        for key in current_keys:
            if key in prev_keys:
                counts[key] = counts.get(key, 0) + 1
                _logger.info(
                    "blocking finding persisted (%d fix attempt(s)) on %s: %s",
                    counts[key],
                    ticket_id,
                    key,
                )
                if self._analytics_emitter is not None:
                    from jig.analytics.events import ReviewFindingPersisted

                    try:
                        self._analytics_emitter.emit_nowait(
                            ReviewFindingPersisted(
                                ticket_id=ticket_id,
                                persistence_key=key,
                                survival_count=counts[key],
                                cycle=cycle,
                            )
                        )
                    except Exception:
                        _logger.warning(
                            "analytics emit failed for ReviewFindingPersisted on %s",
                            ticket_id,
                            exc_info=True,
                        )
            else:
                counts.setdefault(key, 0)
        self._prev_blocking_keys[phase_key] = current_keys

    async def _fail_with_federation_error(self, ticket_id: str) -> None:
        """Mark a ticket FAILED + emit a federation-error Note.

        Called when ``dispatch_with_llm_spawn`` crashes both on the
        initial try and the single retry. Sets ``block_reason`` to
        ``federation-error`` so the operator UX can distinguish a
        gated-by-error ticket from a gated-by-finding ticket.
        """
        if self.tickets is None or self.threads is None:
            return
        await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
        await self.tickets.update(ticket_id, block_reason="federation-error")
        await self.threads.post(
            Note(
                ticket_id=ticket_id,
                author="orchestrator",
                text=(
                    "Review federation crashed twice (initial + 1 retry). "
                    "Ticket marked FAILED with reason `federation-error`. "
                    "Inspect orchestrator logs for the underlying exception "
                    "and re-run after addressing the root cause."
                ),
            )
        )

    async def spawn_review_agent_for_id(
        self,
        *,
        reviewer_id: str,
        ticket,
        role_file: str,
        project_root: Path,
        worktree_path: Path | None = None,
        cycle: int = 0,
        code_metrics: "ChangeMetrics | None" = None,
        informed_findings: dict | None = None,
        delta_base: str | None = None,
    ) -> None:
        """Spawn one LLM-driven federation reviewer (Block 3).

        Federation-execution helper consumed by
        ``jig.reviewers.dispatch.dispatch_with_llm_spawn``. Loads the
        reviewer role config, builds an ``AgentSpawnContext`` rooted at
        the ticket's worktree, and runs the agent through the same
        ``_run_agent_with_analytics`` path used for phase agents. The
        reviewer agent posts comments back via the
        ``reviewer_post_comment`` MCP tool; the dispatcher reads them
        from the ``ReviewCommentsStore`` after this call returns.

        Best-effort — a missing role config or worktree is logged and
        swallowed so a single misconfigured reviewer can't block the
        rest of the federation. Real-mode failure is operator-visible
        through the analytics ``AgentCompleted`` event.
        """
        from jig.persistence import load_role
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            _logger.warning(
                "spawn_review_agent_for_id: orchestrator not started; "
                "skipping spawn for %s on %s",
                reviewer_id,
                ticket.id,
            )
            return

        try:
            role_cfg = load_role(self._project_path, role_file)
        except FileNotFoundError:
            _logger.warning(
                "spawn_review_agent_for_id: role config %r not found; "
                "skipping reviewer %s",
                role_file,
                reviewer_id,
            )
            return

        if worktree_path is None:
            worktree_path = self._project_path / ".jig" / "worktrees" / ticket.id

        # fix-loop-context step 4: build the verify_bundle when prior
        # acks exist. Cycle-1 reviewers get None and run unchanged.
        verify_bundle = await self._build_verify_bundle_for_ticket(ticket.id)

        ctx = AgentSpawnContext(
            role=reviewer_id,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.REVIEWER_FEDERATION,
            ticket=ticket,
            parent=None,
            worktree_path=worktree_path,
            project=self._project,
            tickets=self.tickets,
            threads=self.threads,
            memory=self.memory,
            bus=self.bus,
            checkpoints=self.checkpoints,
            verify_bundle=verify_bundle,
            code_metrics=code_metrics,
            cycle=cycle,
            informed_findings=informed_findings,
            delta_base=delta_base,
            initial_bus_message={
                "kind": "review_federation_spawn",
                "ticket_id": ticket.id,
                "reviewer_id": reviewer_id,
                "project_root": str(project_root),
            },
        )
        try:
            await self._run_agent_with_analytics(ctx)
        except Exception as exc:
            # SF-2: a reviewer that fails to spawn must not look like
            # "reviewer found no issues". Post a durable thread entry
            # so the dispatcher / federation gate sees the failure and
            # the operator has an audit trail.
            _logger.warning(
                "spawn_review_agent_for_id: reviewer %s spawn failed for ticket %s",
                reviewer_id,
                ticket.id,
                exc_info=True,
            )
            try:
                await self.threads.post(
                    SystemEvent(
                        ticket_id=ticket.id,
                        author="orchestrator",
                        event_type="reviewer_spawn_failed",
                        content=(f"reviewer {reviewer_id!r} failed to run: {exc}"),
                    )
                )
            except Exception:
                _logger.exception(
                    "spawn_review_agent_for_id: failed to post "
                    "reviewer_spawn_failed thread entry for %s",
                    ticket.id,
                )
            raise

    async def _emergency_reset(self) -> None:
        self._running = False
        for task in (
            self._dispatch_task,
            self._service_task,
            self._deadlock_task,
            self._stall_task,
            self._analyzer_task,
            self._reconcile_task,
        ):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected — we just cancelled the task.
                    pass
                except Exception:
                    # Log real errors so they're not silently swallowed
                    # during cleanup. We still continue the reset.
                    _logger.warning(
                        "task raised during emergency reset",
                        exc_info=True,
                    )
        for task in self._running_tickets.values():
            task.cancel()
        self._running_tickets.clear()
        self._live_subscribers.clear()
        self._dispatch_task = None
        self._service_task = None
        self._deadlock_task = None
        self._stall_task = None
        self._analyzer_task = None
        self._reconcile_task = None
        self._project = None
        self.store = None
        self.tickets = None
        self.threads = None
        self.memory = None
        self.bus = None
        self.check_results = None
        self.checkpoints = None
        self.review_comments = None

    async def shutdown(self) -> None:
        self._running = False
        tasks_to_cancel: list[asyncio.Task] = []
        if self._dispatch_task is not None:
            tasks_to_cancel.append(self._dispatch_task)
        if self._service_task is not None:
            tasks_to_cancel.append(self._service_task)
        if self._deadlock_task is not None:
            tasks_to_cancel.append(self._deadlock_task)
        if self._stall_task is not None:
            tasks_to_cancel.append(self._stall_task)
        if self._analyzer_task is not None:
            tasks_to_cancel.append(self._analyzer_task)
        if self._reconcile_task is not None:
            tasks_to_cancel.append(self._reconcile_task)
        tasks_to_cancel.extend(self._running_tickets.values())
        tasks_to_cancel.extend(self._live_subscribers.values())
        tasks_to_cancel.extend(list(self._background_tasks))
        for task in tasks_to_cancel:
            task.cancel()
        for task in tasks_to_cancel:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                _logger.warning("task raised during shutdown", exc_info=True)
        self._running_tickets.clear()
        self._live_subscribers.clear()
        self._background_tasks.clear()
        self._dispatch_task = None
        self._service_task = None
        self._deadlock_task = None
        self._stall_task = None
        self._analyzer_task = None
        self._reconcile_task = None
        # Flush in-flight analytics writes so the tail of the event
        # stream isn't lost when the loop closes.
        if self._analytics_emitter is not None:
            try:
                await self._analytics_emitter.drain()
            except Exception:
                _logger.warning("analytics drain raised at shutdown", exc_info=True)

    async def _resume_in_progress(self) -> None:
        if self.tickets is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        in_progress = await self.tickets.find_in_progress_top_level()
        for ticket in in_progress:
            task = asyncio.create_task(self._run_ticket(ticket.id))
            self._running_tickets[ticket.id] = task
            task.add_done_callback(self._ticket_task_done)

    async def _run_service_loop(self) -> None:
        if self.bus is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        queue = await self.bus.subscribe("orchestrator")
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                await self._handle_service_message(msg)
        finally:
            await self.bus.unsubscribe("orchestrator", queue)

    async def _handle_service_message(self, msg: Message) -> None:
        """Dispatch one ``"orchestrator"``-topic message. Typed lifecycle events
        (``TicketCreated`` / ``TicketUpdated``) drive scheduling; an undecodable
        partial/legacy payload falls back to raw fields, and ``shutdown_request``
        stops the loop. A malformed payload must never crash the loop — a raw id
        is scheduled only when it is a non-empty *string* (e.g. an unhashable
        list id is ignored, not handed to the scheduler)."""
        event = decode_event(msg)
        payload = msg.payload or {}
        kind = event.kind if event else payload.get("kind")
        _logger.debug("service loop received: %s", kind)
        if isinstance(event, TicketCreated):
            _logger.info("ticket_created event: %s", event.ticket_id)
            await self._handle_schedule(event.ticket_id)
        elif isinstance(event, TicketUpdated):
            # Re-enqueue tickets reset to "open" (e.g. retry after failure).
            if event.status == TicketStatus.OPEN.value:
                await self._reschedule_reset_ticket(event.ticket_id)
        elif kind == "ticket_created":
            ticket_id = payload.get("ticket_id")
            if isinstance(ticket_id, str) and ticket_id:
                _logger.info("ticket_created event: %s", ticket_id)
                await self._handle_schedule(ticket_id)
        elif kind == "ticket_updated":
            ticket_id = payload.get("ticket_id")
            if (
                isinstance(ticket_id, str)
                and ticket_id
                and payload.get("status") == TicketStatus.OPEN.value
            ):
                await self._reschedule_reset_ticket(ticket_id)
        elif kind == "shutdown_request":
            self._running = False

    async def _reschedule_reset_ticket(self, ticket_id: str) -> None:
        """Re-enqueue a ticket that was reset to ``open`` (e.g. a retry after
        failure): forget any running state and schedule it afresh."""
        _logger.info("ticket %s reset to open — re-scheduling", ticket_id)
        self._running_tickets.pop(ticket_id, None)
        await self._handle_schedule(ticket_id)

    async def _run_deadlock_loop(self) -> None:
        """Periodically sweep open blocking thread entries for age-based
        auto-resolution (Phase 5 Task L).

        Runs every ``DEADLOCK_SWEEP_INTERVAL_S`` seconds. A single sweep
        failure (bad ticket record, transient IO error) is logged and
        swallowed so one bad apple can't wedge the whole loop — the
        next tick picks up where we left off. Both thresholds come
        from ``self._deadlock_cfg`` which was loaded once at
        ``startup()``; operators who want to change them mid-run
        restart the orchestrator.
        """
        if self.tickets is None or self.threads is None or self.bus is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        while self._running:
            try:
                await asyncio.sleep(DEADLOCK_SWEEP_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            if not self._running:
                return
            try:
                result = await sweep_blocking_entries(
                    tickets=self.tickets,
                    threads=self.threads,
                    bus=self.bus,
                    nudge_after_s=self._deadlock_cfg.nudge_after_s,
                    escalate_after_s=self._deadlock_cfg.escalate_after_s,
                )
                if result.nudged or result.escalated:
                    _logger.info(
                        "deadlock sweep: nudged=%d escalated=%d",
                        len(result.nudged),
                        len(result.escalated),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning("deadlock sweep raised; continuing", exc_info=True)

    async def _reconcile_external_tickets(self) -> None:
        """Reload tickets appended by other processes, then run the ready-scan.

        The store is in-memory after ``load()`` and the orchestrator never
        re-reads ``tickets.jsonl`` on its own, so a ticket created out-of-band
        by the ``jig issue`` CLI or the standalone MCP is invisible until this
        reload. Reloading rebuilds the in-memory map from the append-only JSONL
        (the orchestrator's own writes are already on disk, so the rebuild is
        idempotent). Approved (OPEN) issues then dispatch through the normal
        ``find_ready()`` path; PROPOSED ones stay put until an operator
        approves them.
        """
        if self.tickets is None:
            return
        await self.tickets.load()
        await self._sweep_failed_dependencies()
        await self._start_ready_tickets()
        await self._check_stuck()

    async def _check_stuck(self) -> None:
        """Stuck-project tripwire, evaluated once per reconcile tick.

        The failure cascade handles dead-ends it can see (failed
        dependencies); this catches the ones it can't — dependency
        cycles, future scheduling bugs — by detecting the symptom
        directly: nothing running, nothing ready, no operator-pending
        ticket (needs_info / proposed), yet watchable work outstanding.
        After the condition holds for ``STUCK_GRACE_SECONDS`` it emits
        one ``project_stuck`` event per distinct stuck ticket-set and
        logs at WARNING. It never mutates tickets.

        This must live on the periodic reconcile tick: the event-driven
        scheduler paths stop firing precisely when the project
        dead-ends, so a check there would never run.
        """
        if self.tickets is None:
            return
        all_tickets = await self.tickets.list_all()
        stuck_candidates = [t for t in all_tickets if t.status in _STUCK_WATCH_STATUSES]
        has_needs_info = any(t.status == TicketStatus.NEEDS_INFO for t in all_tickets)
        # A non-review-notable PROPOSED ticket is a front-door issue awaiting
        # operator approval — quiet-but-alive, not stuck (matches the docstring
        # and the _STUCK_WATCH_STATUSES comment). review-notable PROPOSED
        # issues are triage backlog (excluded by _counts_toward_completion) and
        # must NOT mask a genuine stuck cycle, so they don't count as alive.
        has_pending_proposed = any(
            t.status == TicketStatus.PROPOSED and _counts_toward_completion(t)
            for t in all_tickets
        )
        ready = await self.tickets.find_ready()
        alive = (
            bool(self._running_tickets)
            or bool(ready)
            or has_needs_info
            or has_pending_proposed
        )
        if alive or not stuck_candidates:
            # Reset the debounce AND the emitted-set: a project that recovers
            # and later goes stuck again with the same ticket-set must still
            # re-alert (otherwise the second occurrence is silently suppressed).
            self._stuck_since = None
            self._stuck_emitted_for = None
            return
        now = time.monotonic()
        if self._stuck_since is None:
            self._stuck_since = now
            return
        if (now - self._stuck_since) < STUCK_GRACE_SECONDS:
            return
        stuck_ids = frozenset(t.id for t in stuck_candidates)
        if stuck_ids == self._stuck_emitted_for:
            return
        self._stuck_emitted_for = stuck_ids
        detail = {
            t.id: {
                "status": t.status.value,
                "blocked_by": list(t.blocked_by),
            }
            for t in stuck_candidates
        }
        _logger.warning(
            "project stuck: %d ticket(s) cannot progress and nothing is running — %s",
            len(stuck_candidates),
            ", ".join(sorted(stuck_ids)),
        )
        if self._emitter is not None:
            from jig.events import JigEvent

            await self._emitter.emit(
                JigEvent(
                    type="project_stuck",
                    data={
                        "kind": "project_stuck",
                        "stuck_tickets": detail,
                    },
                )
            )
        if self._analytics_emitter is not None:
            from jig.analytics.events import ProjectStuck

            self._analytics_emitter.emit_nowait(
                ProjectStuck(stuck_ticket_ids=sorted(stuck_ids))
            )

    async def _run_reconcile_loop(self) -> None:
        """Periodically reconcile externally-appended tickets (see
        :meth:`_reconcile_external_tickets`).

        Runs every ``RECONCILE_INTERVAL_S`` seconds. A single failure is logged
        and swallowed so one bad tick can't wedge the loop — the next tick
        picks up where we left off.
        """
        if self.tickets is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        while self._running:
            try:
                await asyncio.sleep(RECONCILE_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            if not self._running:
                return
            try:
                await self._reconcile_external_tickets()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning("reconcile tick raised; continuing", exc_info=True)

    async def _run_stall_loop(self) -> None:
        """Background task: poll StallDetector and act on verdicts.

        Fires every ``poll_interval_seconds``. When a stall is detected:
        1. Logs a warning with the signal + detail.
        2. SIGTERMs orphan ``claude`` subprocesses in the project's
           worktrees (the most common cause of heartbeat-gap stalls).
        3. Observes a cooldown before firing again so one stall event
           doesn't cascade into repeated kills.
        """
        thresholds = self._stall_detector.thresholds
        last_action_at: float | None = None
        while self._running:
            try:
                await asyncio.sleep(thresholds.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            if not self._running:
                return
            verdict = self._stall_detector.check()
            if verdict is None:
                continue
            now = time.monotonic()
            if (
                last_action_at is not None
                and (now - last_action_at) < thresholds.cooldown_seconds
            ):
                continue
            last_action_at = now
            _logger.warning(
                "stall detected: signal=%s elapsed=%.0fs detail=%s",
                verdict.signal,
                verdict.seconds_since_last_event,
                verdict.detail,
            )
            if verdict.signal == "heartbeat_gap":
                loop = asyncio.get_running_loop()
                killed = await loop.run_in_executor(
                    None, _kill_orphan_claude_processes, self._project_path
                )
                if killed:
                    _logger.info(
                        "stall recovery: SIGTERMed %d orphan claude process(es)", killed
                    )

    async def _handle_schedule(self, ticket_id: str) -> None:
        """Schedule a ticket if its dependencies are satisfied.

        Only top-level work tickets (feature/bug/chore) go through the
        workflow pipeline. If the ticket has unresolved dependencies it
        stays open — ``_unblock_dependents`` will re-check when deps resolve.
        """
        if self.tickets is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        # Serialize the membership-check-through-registration so concurrent
        # ready-scans (dispatch loop + reconcile tick) can't both schedule the
        # same ticket. The whole body runs under the lock; the spawned
        # _run_ticket task runs outside it (create_task does not await).
        async with self._schedule_lock:
            if ticket_id in self._running_tickets:
                _logger.debug("ticket %s already running, skipping", ticket_id)
                return
            ticket = await self.tickets.get(ticket_id)
            if ticket is None:
                _logger.warning("ticket %s not found, cannot schedule", ticket_id)
                return
            # Thread-style tickets (QA threads, ad-hoc requests) are dispatched
            # from the message bus directly — they don't enter the workflow
            # pipeline. Phase 4 replaces this transitional marker with proper
            # typed thread entries on a parent ticket.
            if ticket.workflow == "thread":
                _logger.debug("skipping thread-style ticket %s", ticket_id)
                return
            # Check dependencies — all must be resolved before we start.
            if ticket.blocked_by:
                for dep_id in ticket.blocked_by:
                    dep = await self.tickets.get(dep_id)
                    if dep is not None and dep.status == TicketStatus.FAILED:
                        # A failed dependency can never resolve — this
                        # ticket is unreachable. Cascade-fail it now.
                        await self._cascade_fail_unreachable_ticket(ticket, dep_id)
                        await self._maybe_run_analyzer()
                        return
                    if dep is None or dep.status != TicketStatus.RESOLVED:
                        _logger.info(
                            "ticket %s blocked by %s (status=%s), deferring",
                            ticket_id,
                            dep_id,
                            dep.status.value if dep else "missing",
                        )
                        return
            _logger.info(
                "scheduling ticket %s (workflow=%s)", ticket_id, ticket.workflow
            )
            await self._update_ticket_status(ticket_id, TicketStatus.IN_PROGRESS)
            if self._emitter is not None:
                from jig.events import JigEvent

                await self._emitter.emit(
                    JigEvent(
                        type="ticket_dispatched",
                        data={"ticket_id": ticket_id, "title": ticket.title},
                    )
                )
            task = asyncio.create_task(self._run_ticket(ticket_id))
            self._running_tickets[ticket_id] = task
        task.add_done_callback(self._ticket_task_done)

    def _ticket_task_done(self, task: asyncio.Task) -> None:
        """Log unhandled exceptions from _run_ticket tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _logger.error("_run_ticket task failed: %s", exc, exc_info=exc)

    async def _run_ticket(self, ticket_id: str) -> None:
        """Walk the workflow phases for a ticket.

        Each phase runs an agent against the same ticket. Progress is
        tracked via ``phase_run`` comments on the ticket itself — no
        child tickets are created.
        """
        from jig.persistence import load_role, load_workflow
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            raise RuntimeError("Orchestrator not started — call startup() first")

        tid_token = _ticket_id_var.set(ticket_id)
        try:
            ticket = await self.tickets.get(ticket_id)
            if ticket is None:
                _logger.warning("ticket %s not found, aborting", ticket_id)
                return

            try:
                _logger.info(
                    "starting ticket %s: %s (workflow=%s)",
                    ticket_id,
                    ticket.title,
                    ticket.workflow,
                )
                from jig.persistence import resolve_workflow_name

                workflow_name = resolve_workflow_name(
                    self._project_path,
                    ticket.workflow,
                    ticket.work_type.value if ticket.work_type else None,
                    ticket.size.value if ticket.size else None,
                )
                workflow = load_workflow(self._project_path, workflow_name)
            except FileNotFoundError:
                _logger.error(
                    "workflow %r not found for ticket %s — run 'jig sync' to install missing defaults",
                    ticket.workflow,
                    ticket_id,
                )
                await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
                self._running_tickets.pop(ticket_id, None)
                return

            try:
                worktree = await self._ensure_worktree(ticket)
            except DependencyMergeError as exc:
                _logger.error(
                    "dep merge failed for ticket %s: %s — failing ticket",
                    ticket_id,
                    exc,
                )
                if self.threads is not None:
                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="harness",
                            event_type="dep_merge_failed",
                            content=(
                                f"Dependency branch {exc.dep_branch!r} "
                                f"(from ticket {exc.dep_id!r}) could not be "
                                f"merged into the worktree. Resolve manually "
                                f"and retry."
                            ),
                        )
                    )
                await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
                await self._on_ticket_failed(ticket_id, ticket)
                return
            except Exception as exc:
                # Generic worktree-setup failure (e.g. invalid base branch,
                # git not initialized, disk full). Without this catch the
                # exception escapes to the task done callback and the user
                # sees nothing in the TUI — the ticket appears stuck.
                _logger.error(
                    "worktree setup failed for ticket %s: %s — failing ticket",
                    ticket_id,
                    exc,
                    exc_info=True,
                )
                if self.threads is not None:
                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="harness",
                            event_type="provisioning_failed",
                            content=f"Worktree setup failed: {exc}",
                        )
                    )
                await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
                await self._on_ticket_failed(ticket_id, ticket)
                return
            _logger.info("worktree ready at %s", worktree)
            phase_idx = await self._current_phase_index(ticket_id, workflow)
            _logger.info("resuming from phase %d/%d", phase_idx, len(workflow.phases))

            # Daemon-restart guard: if the ticket is paused waiting on the
            # operator (needs_info), wait for the answer BEFORE re-running
            # the phase. Without this, restart re-dispatches the ticket and
            # the agent re-executes against an unresolved question — which
            # is what causes "the PM started working without me answering".
            if ticket.status == TicketStatus.NEEDS_INFO:
                _logger.info(
                    "ticket %s is needs_info on dispatch — re-prompting and waiting before phase %d",
                    ticket_id,
                    phase_idx,
                )
                await self._wait_for_resume(ticket_id)
                ticket = await self.tickets.get(ticket_id)
                if ticket is None:
                    return

            # Track how many times a phase has been retried after a blocked result.
            # Prevents infinite review→dev→review loops.
            max_fix_cycles = 3
            fix_counts: dict[int, int] = {}
            # review-severity-binary §6 — SA adjudication budget per
            # phase. After MAX_SA_ESCALATIONS, persistent blockers fail
            # the ticket directly: the SA has had its say.
            sa_escalations: dict[int, int] = {}
            # fix-loop-context (step 3): when ``_route_blocked_phase``
            # sends us back, build a bundle of the latest cycle's blocking
            # findings filtered to the chosen phase. The next iteration
            # picks this up at the spawn site, threads it into
            # ``AgentSpawnContext.fix_loop_bundle``, and clears it.
            pending_fix_loop_bundle: dict | None = None
            current_fix_cycle = 0

            while phase_idx < len(workflow.phases):
                phase = workflow.phases[phase_idx]
                phase_token = _phase_var.set(phase.name)
                role_token = _role_var.set(phase.role)
                phase_started_at = time.monotonic()
                # Sentinel so the phase_end finally block can report a
                # sane outcome if the agent raises before ``result`` is
                # bound below.
                result = None
                try:
                    _logger.info(
                        "phase %d/%d: %s (role=%s)",
                        phase_idx + 1,
                        len(workflow.phases),
                        phase.name,
                        phase.role,
                    )
                    if self.threads is not None:
                        await self.threads.post(
                            SystemEvent(
                                ticket_id=ticket_id,
                                author="orchestrator",
                                event_type="phase_start",
                                content=phase.name,
                                payload={
                                    "phase": phase.name,
                                    "role": phase.role,
                                    "spawn_reason": "phase_primary",
                                },
                            )
                        )
                    # Tell the TUI which phase is running
                    await self._emit_phase_event(
                        "phase_started",
                        ticket_id,
                        phase,
                        phase_idx,
                        len(workflow.phases),
                    )

                    # When the review phase is reached and review federation is
                    # enabled, run all reviewers in parallel instead of spawning
                    # a single review agent.  This keeps reviewers in the natural
                    # spec→test→dev→review→document flow and allows critical /
                    # important findings to route back to the dev phase via the
                    # existing blocked-retry path rather than getting stuck after
                    # document with no dispatch continuation.
                    if (
                        phase.role == "review"
                        and self._orchestrator_cfg.run_review_federation
                    ):
                        sub_key = (ticket_id, phase.role)
                        self._live_subscribers[sub_key] = asyncio.current_task()  # type: ignore[assignment]
                        try:
                            result = await self._run_review_phase_federation(
                                ticket_id,
                                ticket,
                                worktree,
                                phase=phase,
                                cycle=current_fix_cycle,
                            )
                        finally:
                            self._live_subscribers.pop(sub_key, None)
                    else:
                        role_cfg = load_role(self._project_path, phase.role)
                        # fix-loop-context: if we're entering this phase
                        # because a previous review block routed us back,
                        # the bundle is pre-built and we spawn with
                        # FIX_LOOP_RETRY. Consume + clear so the next
                        # non-routed spawn falls back to PHASE_PRIMARY.
                        if pending_fix_loop_bundle is not None:
                            spawn_reason_for_phase = SpawnReason.FIX_LOOP_RETRY
                            bundle_for_phase = pending_fix_loop_bundle
                            pending_fix_loop_bundle = None
                        else:
                            spawn_reason_for_phase = SpawnReason.PHASE_PRIMARY
                            bundle_for_phase = None
                        ctx = AgentSpawnContext(
                            role=phase.role,
                            role_cfg=role_cfg,
                            spawn_reason=spawn_reason_for_phase,
                            ticket=ticket,
                            parent=None,
                            worktree_path=worktree,
                            project=self._project,
                            tickets=self.tickets,
                            threads=self.threads,
                            memory=self.memory,
                            bus=self.bus,
                            checkpoints=self.checkpoints,
                            phase=phase,
                            fix_loop_bundle=bundle_for_phase,
                            cycle=current_fix_cycle,
                        )
                        # Write worktree provenance context (review-routing
                        # step 4). The prepare-commit-msg hook reads this file
                        # to append Phase/Agent trailers to every commit made
                        # during this phase. Overwriting per phase keeps the
                        # trailers in sync with what's actually executing.
                        # Import sits above the try block so a module-level
                        # error surfaces immediately (not silently caught as
                        # a provenance warning).
                        from jig.hooks.commit_msg_provenance import (
                            write_worktree_context,
                        )

                        try:
                            write_worktree_context(
                                worktree, phase=phase.name, agent=phase.role
                            )
                        except Exception:  # noqa: BLE001
                            _logger.warning(
                                "worktree.context write failed for %s phase %s",
                                ticket_id,
                                phase.name,
                                exc_info=True,
                            )
                        sub_key = (ticket_id, phase.role)
                        self._live_subscribers[sub_key] = asyncio.current_task()  # type: ignore[assignment]
                        try:
                            _logger.info(
                                "spawning agent for %s on ticket %s",
                                phase.role,
                                ticket_id,
                            )
                            result = await self._run_agent_with_analytics(ctx)
                            _logger.info(
                                "agent %s finished: %s", phase.role, result.status
                            )
                        except Exception:
                            _logger.exception(
                                "agent failed for phase %s ticket %s",
                                phase.name,
                                ticket_id,
                            )
                            await self._emit_phase_event(
                                "phase_complete",
                                ticket_id,
                                phase,
                                phase_idx,
                                len(workflow.phases),
                                result="failed",
                            )
                            await self._update_ticket_status(
                                ticket_id, TicketStatus.FAILED
                            )
                            await self._on_ticket_failed(ticket_id, ticket)
                            return
                        finally:
                            self._live_subscribers.pop(sub_key, None)

                    await self._emit_phase_event(
                        "phase_complete",
                        ticket_id,
                        phase,
                        phase_idx,
                        len(workflow.phases),
                        result=result.status,
                    )

                    if result.status == "success":
                        # Phase 5 Task O1c: if the agent posted a pending
                        # Handoff for this phase, run the automated check
                        # gate. On fail we bounce it (flip to rejected) so
                        # the ``_phase_handoff_rejected`` check below reroutes
                        # to the fix phase via the existing blocked retry
                        # path. Gate-pass leaves the handoff pending for
                        # evaluator resolution (O2a/O2b).
                        await self._run_handoff_gate_if_pending(
                            ticket_id, phase, workflow, worktree
                        )

                        # Phase 4 Task H gate: a phase cannot advance while the
                        # ticket has unresolved blocking thread entries (pending
                        # Handoff, blocking Question, unresolved Objection,
                        # open Escalation). We publish ``phase_blocked_by_thread``
                        # on the orchestrator topic for the TUI and wait for the
                        # entries to resolve. A rejected Handoff unblocks and
                        # then cycles through the existing retry logic.
                        if self.threads is not None:
                            blocking = await self.threads.has_unresolved_blocking(
                                ticket_id
                            )
                            while blocking:
                                await self._emit_phase_blocked_by_thread(
                                    ticket_id, phase, blocking
                                )
                                await self._wait_for_thread_unblock(ticket_id)
                                blocking = await self.threads.has_unresolved_blocking(
                                    ticket_id
                                )
                            if await self._phase_handoff_rejected(
                                ticket_id, phase.name
                            ):
                                result.status = "blocked"

                    # Record the phase_run AFTER gate + thread-blocking may
                    # demote ``result.status`` so the audit record reflects the
                    # final outcome (e.g. a post-gate bounce records "blocked",
                    # not the agent's self-reported "success"). The earlier
                    # `raise` paths above skip this intentionally — a crashed
                    # phase is recorded via ``_on_ticket_failed``, not here.
                    await self._write_phase_run_comment(ticket_id, phase, result)

                    if result.status == "success":
                        phase_idx += 1
                        # Immediately reset ticket to in_progress so the TUI doesn't
                        # flash "resolved" between phases. Do this BEFORE the commit
                        # safety net to minimize the status flicker window.
                        if phase_idx < len(workflow.phases):
                            await self._update_ticket_status(
                                ticket_id, TicketStatus.IN_PROGRESS
                            )
                        # Safety net: commit any uncommitted changes the agent
                        # left behind. SF-3: if the auto-commit fails the phase
                        # output isn't captured, so we fail the ticket instead
                        # of continuing onto the next phase against a stale
                        # tree. The thread entry posted by _auto_commit_worktree
                        # surfaces the cause to the operator.
                        committed = await self._auto_commit_worktree(
                            worktree, phase.name, ticket_id
                        )
                        if not committed:
                            await self._update_ticket_status(
                                ticket_id, TicketStatus.FAILED
                            )
                            await self._on_ticket_failed(ticket_id, ticket)
                            return
                        if phase_idx < len(workflow.phases):
                            ticket = await self.tickets.get(ticket_id)
                        continue

                    if result.status == "needs_info":
                        _logger.info(
                            "phase %s paused — waiting for user input on %s",
                            phase.name,
                            ticket_id,
                        )
                        await self._wait_for_resume(ticket_id)
                        _logger.info(
                            "ticket %s resumed — re-running phase %s",
                            ticket_id,
                            phase.name,
                        )
                        ticket = await self.tickets.get(ticket_id)
                        continue  # re-run same phase_idx

                    if result.status == "blocked":
                        fix_counts[phase_idx] = fix_counts.get(phase_idx, 0) + 1
                        cap_tripped = fix_counts[phase_idx] > max_fix_cycles

                        # review-severity-binary §6 — SA adjudication.
                        # Persistent findings (survived SURVIVAL_THRESHOLD
                        # fix attempts) or a round-cap trip escalate to the
                        # SA instead of failing directly. A reviewer can no
                        # longer unilaterally fail a ticket.
                        sa_guidance: list[dict] | None = None
                        sa_granted_round = False
                        escalated_keys = (
                            self._keys_past_survival_threshold(ticket_id, phase.name)
                            if phase.role == "review"
                            else set()
                        )
                        if (cap_tripped or escalated_keys) and phase.role == "review":
                            if sa_escalations.get(phase_idx, 0) < MAX_SA_ESCALATIONS:
                                sa_escalations[phase_idx] = (
                                    sa_escalations.get(phase_idx, 0) + 1
                                )
                                adjudication = await self._run_sa_adjudication(
                                    ticket_id=ticket_id,
                                    ticket=ticket,
                                    phase_name=phase.name,
                                    worktree=worktree,
                                    escalated_keys=escalated_keys,
                                    cycle=current_fix_cycle,
                                    cap_trip=cap_tripped,
                                )
                                if adjudication.fail:
                                    _logger.warning(
                                        "SA adjudication upheld-unresolvable on "
                                        "ticket %s — failing",
                                        ticket_id,
                                    )
                                    await self._update_ticket_status(
                                        ticket_id, TicketStatus.FAILED
                                    )
                                    await self._on_ticket_failed(ticket_id, ticket)
                                    return
                                if (
                                    adjudication.dismissed_keys
                                    and not adjudication.guidance
                                ):
                                    # Every escalated finding dismissed — the
                                    # gate's binding-dismissal filter now
                                    # excludes them; re-run the review phase
                                    # to re-evaluate the remaining findings.
                                    _logger.info(
                                        "SA dismissed all escalated finding(s) "
                                        "on ticket %s — re-running %s",
                                        ticket_id,
                                        phase.name,
                                    )
                                    # The re-run is system-driven (SA dismissal),
                                    # not a developer fix attempt: clear the prior
                                    # blocking-key set so _record_blocking_persistence
                                    # doesn't count the surviving findings as having
                                    # survived a fix, and bump the cycle so the audit
                                    # trail stays monotone.
                                    self._prev_blocking_keys[
                                        (ticket_id, phase.name)
                                    ] = set()
                                    current_fix_cycle += 1
                                    ticket = await self.tickets.get(ticket_id)
                                    continue
                                # Upheld with guidance: grant one routed fix
                                # round, even past the cap.
                                sa_guidance = adjudication.guidance
                                sa_granted_round = True

                        if cap_tripped and not sa_granted_round:
                            _logger.warning(
                                "phase %s blocked %d times — giving up on ticket %s",
                                phase.name,
                                fix_counts[phase_idx],
                                ticket_id,
                            )
                            await self._update_ticket_status(
                                ticket_id, TicketStatus.FAILED
                            )
                            await self._on_ticket_failed(ticket_id, ticket)
                            return

                        # Route blocking comments per the review-routing
                        # design — by reviewer-declared target_role,
                        # then by file → owning phase via the workflow's
                        # ``writes:`` globs, with a most-recent-dev
                        # fallback for unowned findings.
                        fix_idx = await self._route_blocked_phase(
                            workflow, phase_idx, ticket_id, worktree
                        )
                        if fix_idx is not None:
                            _logger.info(
                                "phase %s blocked — routing back to %s (attempt %d/%d)",
                                phase.name,
                                workflow.phases[fix_idx].name,
                                fix_counts[phase_idx],
                                max_fix_cycles,
                            )
                            # fix-loop-context: build the bundle of
                            # blocking findings + ack history filtered
                            # to the chosen target phase. Stashed in
                            # ``pending_fix_loop_bundle`` so the next
                            # spawn iteration picks it up.
                            try:
                                pending_fix_loop_bundle = (
                                    await self._build_fix_loop_bundle_for_phase(
                                        workflow=workflow,
                                        blocked_phase_idx=phase_idx,
                                        target_phase_idx=fix_idx,
                                        ticket_id=ticket_id,
                                        worktree=worktree,
                                    )
                                )
                            except Exception:
                                _logger.warning(
                                    "fix-loop-context: failed to build bundle "
                                    "for ticket %s — back-routed spawn will "
                                    "proceed without finding context",
                                    ticket_id,
                                    exc_info=True,
                                )
                                pending_fix_loop_bundle = None
                            if sa_guidance:
                                if pending_fix_loop_bundle is None:
                                    pending_fix_loop_bundle = {
                                        "findings": [],
                                        "overflow_count": 0,
                                    }
                                pending_fix_loop_bundle["sa_guidance"] = sa_guidance
                            current_fix_cycle += 1
                            await self._update_ticket_status(
                                ticket_id, TicketStatus.IN_PROGRESS
                            )
                            await self._auto_commit_worktree(
                                worktree, phase.name, ticket_id
                            )
                            phase_idx = fix_idx
                            ticket = await self.tickets.get(ticket_id)
                            continue

                        _logger.warning(
                            "phase %s blocked but no fix phase found — failing",
                            phase.name,
                        )

                    # Unrecoverable: fail the ticket.
                    await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
                    await self._on_ticket_failed(ticket_id, ticket)
                    return

                finally:
                    if self.threads is not None:
                        outcome = result.status if result is not None else "failed"
                        duration_ms = int((time.monotonic() - phase_started_at) * 1000)
                        await self.threads.post(
                            SystemEvent(
                                ticket_id=ticket_id,
                                author="orchestrator",
                                event_type="phase_end",
                                content=outcome,
                                payload={
                                    "phase": phase.name,
                                    "role": phase.role,
                                    "duration_ms": duration_ms,
                                    "outcome": outcome,
                                },
                            )
                        )
                    _phase_var.reset(phase_token)
                    _role_var.reset(role_token)
            await self._on_ticket_completed(ticket_id, ticket)
        finally:
            _ticket_id_var.reset(tid_token)

    async def _try_resolve_conflict(
        self,
        ticket_id: str,
        ticket: "Ticket",
        *,
        _max_attempts: int = 3,
        _base_delay: float = 2.0,
        _sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> bool:
        """Spawn the built-in conflict_resolver agent to fix conflict markers.

        Returns True if the agent completes without exception (caller should
        retry the merge). Returns False on any failure — missing role,
        spawn error, agent crash — so a resolver failure never turns a
        conflict into an orchestrator crash.

        Transient SDK failures are retried up to ``_max_attempts`` times with
        exponential backoff (``_base_delay * 2**i`` seconds between attempts).
        The ``_sleep`` parameter is injectable for tests that need instant
        execution.
        """
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self._project is None
            or self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
        ):
            return False

        try:
            role_cfg = load_role(self._project_path, "conflict_resolver")
        except FileNotFoundError:
            _logger.warning(
                "_try_resolve_conflict: conflict_resolver role not found; "
                "falling back to human resolution for %s",
                ticket_id,
            )
            return False

        sleep_fn: Callable[[float], Awaitable[None]] = (
            _sleep if _sleep is not None else asyncio.sleep
        )
        worktree_path = self._project_path / ".jig" / "worktrees" / ticket_id
        ctx = AgentSpawnContext(
            role="conflict_resolver",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.CONFLICT_RESOLVER,
            ticket=ticket,
            parent=None,
            worktree_path=worktree_path,
            project=self._project,
            tickets=self.tickets,
            threads=self.threads,
            memory=self.memory,
            bus=self.bus,
            checkpoints=self.checkpoints,
            initial_bus_message={
                "kind": "conflict_resolve_spawn",
                "ticket_id": ticket_id,
                "base_branch": self._project.default_branch,
            },
        )
        for attempt in range(_max_attempts):
            if not self._running:
                return False
            try:
                result = await self._run_agent_with_analytics(
                    ctx, spawned_by="conflict_resolver"
                )
                return result.status == "success"
            except Exception as exc:
                if attempt < _max_attempts - 1:
                    delay = _base_delay * (2**attempt)
                    _logger.warning(
                        "_try_resolve_conflict: attempt %d/%d failed for %s "
                        "(retry in %.1fs): %s",
                        attempt + 1,
                        _max_attempts,
                        ticket_id,
                        delay,
                        exc,
                    )
                    await sleep_fn(delay)
                    if not self._running:
                        return False
                else:
                    _logger.warning(
                        "_try_resolve_conflict: agent failed for %s after %d attempts",
                        ticket_id,
                        _max_attempts,
                        exc_info=True,
                    )
        return False

    async def _try_replan(
        self,
        ticket_id: str,
        ticket: "Ticket",
        conflicted_files: list[str],
        *,
        _max_attempts: int = 3,
        _base_delay: float = 2.0,
        _sleep: Callable[[float], Awaitable[None]] | None = None,
        _post_replan_cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Spawn a PM agent in REPLAN mode after a conflict is auto-resolved.

        Fire-and-forget — never raises. Tightens depends_on on pending tickets
        that are likely to touch the same files that just conflicted.

        Transient SDK failures are retried up to ``_max_attempts`` times with
        exponential backoff (``_base_delay * 2**i`` seconds between attempts).
        The ``_sleep`` parameter is injectable for tests that need instant
        execution.

        ``_post_replan_cleanup`` is an optional async callback invoked after all
        retry attempts finish (success or exhaustion). The caller should pass
        the ticket-worktree ``remove_worktree`` coroutine here when scheduling
        this task fire-and-forget — that guarantees the worktree stays alive
        across every retry attempt and is cleaned up by the background task
        rather than by the caller immediately after scheduling.
        """
        from jig.runtime import AgentSpawnContext, SpawnReason

        try:
            if (
                self._project is None
                or self.tickets is None
                or self.threads is None
                or self.memory is None
                or self.bus is None
            ):
                return

            try:
                role_cfg = load_role(self._project_path, "pm")
            except FileNotFoundError:
                _logger.warning(
                    "_try_replan: pm role not found; skipping replan for %s",
                    ticket_id,
                )
                return

            sleep_fn: Callable[[float], Awaitable[None]] = (
                _sleep if _sleep is not None else asyncio.sleep
            )
            worktree_path = self._project_path / ".jig" / "worktrees" / ticket_id
            ctx = AgentSpawnContext(
                role="pm",
                role_cfg=role_cfg,
                spawn_reason=SpawnReason.REPLAN,
                ticket=ticket,
                parent=None,
                worktree_path=worktree_path,
                project=self._project,
                tickets=self.tickets,
                threads=self.threads,
                memory=self.memory,
                bus=self.bus,
                checkpoints=self.checkpoints,
                initial_bus_message={
                    "kind": "replan_spawn",
                    "ticket_id": ticket_id,
                    "conflicted_files": conflicted_files,
                },
            )
            for attempt in range(_max_attempts):
                if not self._running:
                    return
                try:
                    await self._run_agent_with_analytics(ctx, spawned_by="replan")
                    return
                except Exception as exc:
                    if attempt < _max_attempts - 1:
                        delay = _base_delay * (2**attempt)
                        _logger.warning(
                            "_try_replan: attempt %d/%d failed for %s (retry in %.1fs): %s",
                            attempt + 1,
                            _max_attempts,
                            ticket_id,
                            delay,
                            exc,
                        )
                        await sleep_fn(delay)
                        if not self._running:
                            return
                    else:
                        _logger.warning(
                            "_try_replan: agent failed for %s after %d attempts",
                            ticket_id,
                            _max_attempts,
                            exc_info=True,
                        )
        finally:
            if _post_replan_cleanup is not None:
                try:
                    await _post_replan_cleanup()
                except BaseException:
                    # BaseException (not Exception) so a CancelledError raised
                    # by cleanup itself — e.g. a second cancellation, or
                    # remove_worktree awaiting a subprocess during loop
                    # teardown — cannot silently leave the worktree on disk.
                    _logger.warning(
                        "_try_replan: post-replan cleanup failed for %s",
                        ticket_id,
                        exc_info=True,
                    )

    async def _on_ticket_completed(self, ticket_id: str, ticket) -> None:
        """Post-completion: merge branch, set final status, emit event,
        clean up, pick up next ticket.

        Status transitions are keyed off the merge outcome:

        * clean merge → ``RESOLVED`` + ``ticket_completed``
        * typed merge conflict → ``MERGE_CONFLICT`` +
          ``ticket_merge_conflict``; branch and worktree are preserved
          for a human to resolve.
        * other merge errors → ``FAILED`` + ``ticket_failed``.
        """
        from jig.worktree import (
            MergeConflictError,
            merge_ticket,
            remove_worktree,
        )

        branch_name = f"jig/{ticket_id}"

        merge_result = ""
        merge_conflict = False
        merge_failed = False
        replan_task_scheduled = False
        if self._project is not None:
            strategy = self._project.merge_strategy
            try:
                merge_result = await merge_ticket(
                    self._project_path,
                    ticket_id,
                    self._project.default_branch,
                    strategy,
                )
                _logger.info("merge complete: %s", merge_result)
            except MergeConflictError as exc:
                merge_result = str(exc)
                conflicted_files = exc.conflicted_files
                _logger.warning(
                    "merge conflict for %s — attempting auto-resolution; "
                    "branch %s preserved",
                    ticket_id,
                    branch_name,
                )
                resolved_by_agent = await self._try_resolve_conflict(ticket_id, ticket)
                if resolved_by_agent:
                    try:
                        merge_result = await merge_ticket(
                            self._project_path,
                            ticket_id,
                            self._project.default_branch,
                            strategy,
                        )
                        _logger.info(
                            "conflict resolved by agent, merge retry succeeded: %s",
                            merge_result,
                        )

                        # Pass remove_worktree as the post-replan cleanup so the
                        # background task owns the worktree lifetime.  Without
                        # this, _on_ticket_completed removes the worktree before
                        # any retry backoff sleep completes, causing retries to
                        # run with a missing cwd/CLAUDE.md target.
                        async def _worktree_cleanup() -> None:
                            try:
                                await remove_worktree(
                                    self._project_path, ticket_id, keep_branch=True
                                )
                                _logger.info(
                                    "worktree removed for %s (branch %s preserved)",
                                    ticket_id,
                                    branch_name,
                                )
                            except Exception:
                                _logger.warning(
                                    "worktree cleanup failed for %s",
                                    ticket_id,
                                    exc_info=True,
                                )

                        _task = asyncio.create_task(
                            self._try_replan(
                                ticket_id,
                                ticket,
                                conflicted_files,
                                _post_replan_cleanup=_worktree_cleanup,
                            )
                        )
                        self._background_tasks.add(_task)
                        _task.add_done_callback(self._background_tasks.discard)
                        replan_task_scheduled = True
                    except MergeConflictError as retry_exc:
                        merge_conflict = True
                        merge_result = str(retry_exc)
                        _logger.warning(
                            "merge conflict persists after agent resolution for %s "
                            "— routing to MERGE_CONFLICT",
                            ticket_id,
                        )
                    except Exception:
                        merge_failed = True
                        merge_result = (
                            f"merge retry failed (branch {branch_name} preserved)"
                        )
                        _logger.warning(
                            "merge retry failed for %s after conflict resolution",
                            ticket_id,
                            exc_info=True,
                        )
                else:
                    merge_conflict = True
                    _logger.warning(
                        "conflict resolver gave up for %s — routing to MERGE_CONFLICT",
                        ticket_id,
                    )
            except Exception:
                merge_failed = True
                merge_result = f"merge failed (branch {branch_name} preserved)"
                _logger.warning("merge failed for %s", ticket_id, exc_info=True)

        if merge_conflict:
            await self._update_ticket_status(ticket_id, TicketStatus.MERGE_CONFLICT)
            if self._emitter is not None:
                from jig.events import JigEvent

                await self._emitter.emit(
                    JigEvent(
                        type="ticket_merge_conflict",
                        data={
                            "kind": "ticket_merge_conflict",
                            "ticket_id": ticket_id,
                            "title": ticket.title,
                            "branch": branch_name,
                            "merge": merge_result,
                        },
                    )
                )
            # Preserve worktree + branch so a human can resolve the
            # conflict manually — intentionally skipping remove_worktree.
            self._running_tickets.pop(ticket_id, None)
            await self._start_ready_tickets()
            return

        if merge_failed:
            await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
            await self._on_ticket_failed(ticket_id, ticket)
            return

        await self._update_ticket_status(ticket_id, TicketStatus.RESOLVED)
        _logger.info("ticket %s resolved — branch %s", ticket_id, branch_name)

        # Review federation now runs as the "review" workflow phase
        # (see _run_review_phase_federation), so reviewers fire in the
        # correct spec→test→dev→review→document order and run in
        # parallel.  No post-merge re-run needed here.

        if self._emitter is not None:
            from jig.events import JigEvent

            await self._emitter.emit(
                JigEvent(
                    type="ticket_completed",
                    data={
                        "kind": "ticket_completed",
                        "ticket_id": ticket_id,
                        "title": ticket.title,
                        "branch": branch_name,
                        "merge": merge_result,
                    },
                )
            )

        self._running_tickets.pop(ticket_id, None)

        # Skip worktree removal when a replan background task was scheduled:
        # that task holds a reference to a _worktree_cleanup callback and will
        # remove the worktree after all retry attempts complete.  Removing the
        # worktree here would race against retry backoff sleeps, leaving retries
        # with a missing cwd/CLAUDE.md target.
        if not replan_task_scheduled:
            try:
                await remove_worktree(self._project_path, ticket_id, keep_branch=True)
                _logger.info(
                    "worktree removed for %s (branch %s preserved)",
                    ticket_id,
                    branch_name,
                )
            except Exception:
                _logger.warning(
                    "worktree cleanup failed for %s", ticket_id, exc_info=True
                )

        await self._maybe_spawn_per_merge_canonicalize(ticket_id, ticket)
        await self._unblock_dependents(ticket_id, ticket)
        await self._start_ready_tickets()
        await self._maybe_run_analyzer()

    async def _maybe_spawn_per_merge_canonicalize(self, ticket_id: str, ticket) -> None:
        """If canonicalize_mode=per_merge, create a canonicalize ticket after each merge."""
        if self._orchestrator_cfg.canonicalize_mode != "per_merge":
            return
        from jig.ticket import Size, Ticket, TicketStatus, WorkType

        if ticket.work_type == WorkType.CANONICALIZE:
            return

        if self.tickets is None:
            return

        canon_ticket = Ticket(
            title=f"Canonicalize: post-merge {ticket_id}",
            description="",
            work_type=WorkType.CANONICALIZE,
            workflow="canonicalize",
            size=Size.S,
            status=TicketStatus.OPEN,
            parent_id=ticket_id,
            labels=["per-merge"],
            created_by="orchestrator",
        )
        new_id = await self.tickets.create(canon_ticket)
        _logger.info(
            "per-merge canonicalize ticket %s created for %s", new_id, ticket_id
        )
        await self._handle_schedule(new_id)

    async def _on_ticket_failed(self, ticket_id: str, ticket) -> None:
        """Post-failure: emit event, cascade to dependents, clean up,
        pick up next ticket."""
        _logger.info("ticket %s failed", ticket_id)
        await self._emit_ticket_failed(ticket_id, ticket.title)
        self._running_tickets.pop(ticket_id, None)
        # Drop review-persistence state for this ticket: the orchestrator
        # may reset a failed ticket to open and retry it in the same daemon,
        # and stale _survival_counts/_prev_blocking_keys would make the fresh
        # run's first blocked review look like a persisted finding (job 565).
        for key in {k for k in self._survival_counts if k[0] == ticket_id} | {
            k for k in self._prev_blocking_keys if k[0] == ticket_id
        }:
            self._clear_phase_persistence(key)
        await self._cascade_fail_dependents(ticket_id)
        await self._start_ready_tickets()
        await self._maybe_run_analyzer()

    async def _emit_ticket_failed(self, ticket_id: str, title: str) -> None:
        if self._emitter is None:
            return
        from jig.events import JigEvent

        await self._emitter.emit(
            JigEvent(
                type="ticket_failed",
                data={
                    "kind": "ticket_failed",
                    "ticket_id": ticket_id,
                    "title": title,
                },
            )
        )

    async def _resolve_root_failure(self, dep_id: str) -> str:
        """Walk up the cascade chain from a failed dependency to the original
        (non-cascade) failure, for blame attribution. A DEP_FAILED_REASON
        ticket was itself failed by one of its failed dependencies; follow that
        chain to the root. Returns ``dep_id`` if it is already a root failure
        or the chain can't be resolved (cycle-guarded)."""
        if self.tickets is None:
            return dep_id
        current = dep_id
        seen: set[str] = set()
        while current not in seen:
            seen.add(current)
            t = await self.tickets.get(current)
            if t is None or t.block_reason != DEP_FAILED_REASON:
                return current
            nxt = None
            for b in t.blocked_by:
                dep = await self.tickets.get(b)
                if dep is not None and dep.status == TicketStatus.FAILED:
                    nxt = b
                    break
            if nxt is None:
                return current
            current = nxt
        return current

    async def _cascade_fail_unreachable_ticket(
        self, ticket, failed_dep_id: str
    ) -> None:
        """Fail one OPEN ticket made unreachable by a FAILED dependency,
        then cascade to its own dependents. Shared by the schedule-time
        check (_handle_schedule) and the reconcile sweep
        (_sweep_failed_dependencies)."""
        _logger.info(
            "ticket %s blocked by failed dependency %s — cascade-failing",
            ticket.id,
            failed_dep_id,
        )
        await self.tickets.update(ticket.id, block_reason=DEP_FAILED_REASON)
        await self._update_ticket_status(ticket.id, TicketStatus.FAILED)
        await self._emit_ticket_failed(ticket.id, ticket.title)
        # failed_dep_id may itself be a cascade-failed intermediate; walk up to
        # the original root so multi-hop late tickets attribute blame correctly.
        root = await self._resolve_root_failure(failed_dep_id)
        if self._analytics_emitter is not None:
            from jig.analytics.events import TicketCascadeFailed

            self._analytics_emitter.emit_nowait(
                TicketCascadeFailed(
                    ticket_id=ticket.id,
                    root_failure_id=root,
                    failed_dependency_id=failed_dep_id,
                )
            )
        await self._cascade_fail_dependents(ticket.id, root_failure_id=root)

    async def _sweep_failed_dependencies(self) -> None:
        """Cascade-fail OPEN tickets whose dependency has already FAILED.

        ``find_ready()`` excludes OPEN tickets with unresolved deps, so an
        externally-created or late-approved ticket blocked by an
        already-failed dependency never reaches ``_handle_schedule``'s
        failed-dep branch and would sit OPEN forever. The reconcile tick
        sweeps for them directly so the project still reaches all-terminal.
        """
        if self.tickets is None:
            return
        failed_any = False
        for stale in await self.tickets.list_all():
            # Re-fetch: an earlier iteration's cascade may have already
            # failed this ticket within the same sweep.
            t = await self.tickets.get(stale.id)
            if t is None or t.status != TicketStatus.OPEN:
                continue
            if t.id in self._running_tickets:
                continue
            for dep_id in t.blocked_by:
                dep = await self.tickets.get(dep_id)
                if dep is not None and dep.status == TicketStatus.FAILED:
                    await self._cascade_fail_unreachable_ticket(t, dep_id)
                    failed_any = True
                    break
        if failed_any:
            await self._maybe_run_analyzer()

    async def _cascade_fail_dependents(
        self, root_id: str, *, root_failure_id: str | None = None
    ) -> None:
        """Transitively fail open dependents of a failed ticket.

        A ticket is only scheduled when ALL its dependencies are
        resolved, so once any dependency is FAILED the dependent can
        never run — leaving it open strands the project short of
        all-terminal forever and project_complete never fires (the
        silent dead-end from the 2026-06-12 hn-cli eval). Dependents
        cannot be in flight (they were never scheduled), so this walk
        does not race running agents.

        Only OPEN dependents cascade — mirrors _unblock_dependents on
        the resolve path. Each cascaded ticket gets status FAILED with
        ``block_reason=DEP_FAILED_REASON`` and its own ticket_failed
        event, and is walked in turn.
        """
        if self.tickets is None:
            return
        # root_failure_id is the originally-failed ticket for blame
        # attribution; root_id is just the walk seed. They differ when the
        # seed is an intermediate (e.g. a swept late ticket) rather than the
        # true root failure.
        blame = root_failure_id or root_id
        queue: list[str] = [root_id]
        seen: set[str] = {root_id}
        while queue:
            current = await self.tickets.get(queue.pop(0))
            if current is None:
                continue
            for dep_id in current.blocks:
                if dep_id in seen:
                    continue
                seen.add(dep_id)
                dependent = await self.tickets.get(dep_id)
                if dependent is None or dependent.status != TicketStatus.OPEN:
                    continue
                if dep_id in self._running_tickets:
                    # Defensive — an OPEN ticket can't be running, but if
                    # state ever disagrees, never fail live work.
                    continue
                _logger.info(
                    "ticket %s unreachable — dependency %s failed; cascading",
                    dep_id,
                    current.id,
                )
                await self.tickets.update(dep_id, block_reason=DEP_FAILED_REASON)
                await self._update_ticket_status(dep_id, TicketStatus.FAILED)
                await self._emit_ticket_failed(dep_id, dependent.title)
                if self._analytics_emitter is not None:
                    from jig.analytics.events import TicketCascadeFailed

                    self._analytics_emitter.emit_nowait(
                        TicketCascadeFailed(
                            ticket_id=dep_id,
                            root_failure_id=blame,
                            failed_dependency_id=current.id,
                        )
                    )
                queue.append(dep_id)

    async def _unblock_dependents(self, completed_id: str, ticket) -> None:
        """After a ticket resolves, check its `blocks` list and schedule any
        tickets whose dependencies are now fully satisfied."""
        if self.tickets is None:
            return
        for blocked_id in ticket.blocks:
            blocked = await self.tickets.get(blocked_id)
            if blocked is None or blocked.status != TicketStatus.OPEN:
                continue
            _logger.info(
                "ticket %s resolved — checking if %s is now unblocked",
                completed_id,
                blocked_id,
            )
            await self._handle_schedule(blocked_id)

    async def _ensure_planning_ticket(self) -> None:
        """Create a planning ticket if the project is initialized but has none."""
        if self.tickets is None:
            return
        arch = self._project_path / ".jig" / "spec" / "architecture.yaml"
        if not arch.is_file():
            return  # project not initialized
        existing = await self.tickets.get("planning")
        if existing is not None:
            return  # already created
        # Check whether any non-init tickets exist (i.e. planning already happened)
        from jig.ticket import WorkType as _WorkType

        init_types = {_WorkType.BRIEF, _WorkType.ARCHITECTURE, _WorkType.PLANNING}
        all_tickets = await self.tickets.list_all()
        dev_tickets = [t for t in all_tickets if t.work_type not in init_types]
        if dev_tickets:
            return  # planning already produced tickets

        from jig.ticket import Ticket

        spec_path = self._project_path / ".jig" / "spec" / "project.structured.yaml"
        await self.tickets.create(
            Ticket(
                id="planning",
                work_type=_WorkType.PLANNING,
                title="Project planning",
                description=(
                    "Break down the project spec into implementation tickets.\n\n"
                    f"Spec: {spec_path}"
                ),
                workflow="project",
                created_by="orchestrator",
            )
        )
        _logger.info("auto-created planning ticket")

    async def _maybe_run_analyzer(self) -> None:
        """Fire the post-run analyzer when all tickets reach terminal state.

        Terminal = every ticket is resolved, closed, or failed (nothing still
        open / in-progress / needs-info / blocked / merge-conflict). Tracks
        the set of terminal ticket IDs so it re-fires if new tickets are added
        and subsequently resolved in the same daemon session.
        """
        if self.tickets is None:
            return
        if self._running_tickets:
            return
        all_tickets = await self.tickets.list_all()
        if not all_tickets:
            return
        if any(
            t.status in _NON_TERMINAL_ANALYZER_STATUSES and _counts_toward_completion(t)
            for t in all_tickets
        ):
            return
        terminal_ids = frozenset(t.id for t in all_tickets)
        if terminal_ids == self._analyzer_last_terminal_ids:
            return
        self._analyzer_last_terminal_ids = terminal_ids

        resolved = sum(
            1
            for t in all_tickets
            if t.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        )
        failed = sum(1 for t in all_tickets if t.status == TicketStatus.FAILED)
        if self._emitter is not None:
            from jig.events import JigEvent

            await self._emitter.emit(
                JigEvent(
                    type="project_complete",
                    data={
                        "kind": "project_complete",
                        "tickets_resolved": resolved,
                        "tickets_failed": failed,
                        "tickets_total": len(all_tickets),
                    },
                )
            )
        self._analyzer_task = asyncio.create_task(self._run_analyzer_bg())

    async def _run_analyzer_bg(self) -> None:
        """Run the post-run analyzer in a thread and emit analysis_complete."""
        from datetime import datetime, timezone

        try:
            from jig.evals.watcher.analyzer import analyze
        except ImportError:
            _logger.warning(
                "analyzer not available (jig.evals import failed)", exc_info=True
            )
            return

        project_name = self._project_path.resolve().name
        run_id = (
            f"{project_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        out_dir = self._project_path / ".jig" / "analysis" / run_id
        jig_repo = Path(__file__).resolve().parents[1]  # jig pkg → repo root

        loop = asyncio.get_running_loop()
        try:
            metrics = await loop.run_in_executor(
                None,
                lambda: analyze(
                    project_path=self._project_path,
                    run_id=run_id,
                    out_dir=out_dir,
                    jig_repo=jig_repo,
                    project_name=project_name,
                    use_llm=True,
                ),
            )
            _logger.info(
                "analysis complete: outcome=%s duration=%.0fs out=%s",
                metrics.outcome,
                metrics.duration_s,
                out_dir,
            )
            if self._emitter is not None:
                from jig.events import JigEvent

                await self._emitter.emit(
                    JigEvent(
                        type="analysis_complete",
                        data={
                            "kind": "analysis_complete",
                            "outcome": metrics.outcome,
                            "duration_s": metrics.duration_s,
                            "tickets_resolved": metrics.tickets.resolved,
                            "tickets_total": metrics.tickets.total,
                            "out_dir": str(out_dir),
                        },
                    )
                )
        except Exception:
            _logger.warning("post-run analyzer failed", exc_info=True)

    async def _start_ready_tickets(self) -> None:
        """Find ALL open tickets with satisfied dependencies and start them.

        Respects ``project.max_parallel`` when set — stops dispatching once
        the running-ticket count reaches the cap. Capped tickets stay open
        and are picked up on the next call (triggered by any ticket completion).
        """
        if self.tickets is None:
            return
        ready = await self.tickets.find_ready()
        if not ready:
            _logger.info("no ready tickets in queue")
            return
        for t in ready:
            if (
                self._project is not None
                and self._project.max_parallel is not None
                and len(self._running_tickets) >= self._project.max_parallel
            ):
                _logger.debug(
                    "max_parallel=%d reached; deferring %s",
                    self._project.max_parallel,
                    t.id,
                )
                break
            if t.id not in self._running_tickets:
                _logger.info("picking up ready ticket: %s — %s", t.id, t.title)
                await self._handle_schedule(t.id)

    async def _ensure_worktree(self, ticket) -> Path:
        """Ensure a worktree exists for ``ticket`` and return its path.

        After creation, merges dependency branches into the worktree so the
        agent sees all prerequisite code — even if those branches haven't
        been merged into main yet.
        """
        from jig.hooks.commit_msg_provenance import install_commit_msg_hook
        from jig.worktree import (
            create_worktree,
            install_per_commit_hook_or_warn,
            merge_dep_into_worktree,
        )

        if self._project is None:
            raise RuntimeError("Orchestrator not started")
        worktree_path = self._project_path / ".jig" / "worktrees" / ticket.id
        if worktree_path.exists():
            return worktree_path
        worktree_path = await create_worktree(
            project_path=self._project_path,
            ticket_id=ticket.id,
            base_branch=self._project.default_branch,
        )
        # Install the commit-msg provenance hook so every commit in this
        # worktree carries `Phase:` / `Agent:` trailers (review-routing
        # step 4). Hook is purely additive; if install fails we log and
        # continue — agents still work, routing degrades to the
        # unowned-finding fallback for multi-glob matches.
        try:
            install_commit_msg_hook(worktree_path)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "commit-msg provenance hook install failed for ticket %s",
                ticket.id,
                exc_info=True,
            )
        # SF-I1: install the per-commit reviewer hook out-of-line so a
        # failure surfaces on the ticket thread rather than a stderr
        # log line nobody reads. Hook install is informational —
        # don't fail the worktree on it, but do tell the operator.
        warning = install_per_commit_hook_or_warn(worktree_path, ticket.id)
        if warning is not None and self.threads is not None:
            try:
                await self.threads.post(
                    SystemEvent(
                        ticket_id=ticket.id,
                        author="orchestrator",
                        event_type="per_commit_hook_install_failed",
                        content=warning,
                    )
                )
            except Exception:
                _logger.exception(
                    "could not record per_commit_hook_install_failed for %s",
                    ticket.id,
                )
        # Merge dependency branches so the agent starts with their
        # code. A failure here means the worktree is missing its
        # prereqs — running the agent on an inconsistent tree would
        # produce a plausible-looking but wrong output. Surface the
        # error to the caller, who fails the ticket with a typed
        # ``dep_merge_failed`` SystemEvent.
        for dep_id in ticket.blocked_by:
            dep_branch = f"jig/{dep_id}"
            try:
                await merge_dep_into_worktree(worktree_path, dep_branch)
                _logger.info(
                    "merged dep branch %s into worktree for %s", dep_branch, ticket.id
                )
            except RuntimeError as exc:
                _logger.error(
                    "dep branch %s could not be merged into worktree for "
                    "%s — failing ticket (branch may not exist or has "
                    "conflicts)",
                    dep_branch,
                    ticket.id,
                )
                raise DependencyMergeError(
                    ticket_id=ticket.id,
                    dep_id=dep_id,
                    dep_branch=dep_branch,
                ) from exc
        return worktree_path

    async def _wait_for_resume(self, ticket_id: str) -> None:
        """Block until the ticket transitions out of needs_info status.

        When a TUI channel is available (emitter + prompt_registry), surfaces
        the blocking question as a prompt and awaits the operator's reply.
        Otherwise falls back to silent bus polling.
        """
        if self._emitter is not None and self._prompt_registry is not None:
            await self._prompt_for_needs_info(ticket_id)
            return
        await self._silent_wait_for_resume(ticket_id)

    async def _prompt_for_needs_info(self, ticket_id: str) -> None:
        """Emit a TUI prompt for the blocking question on a needs_info ticket.

        Finds the most recent unresolved blocking question, round-trips
        through the PromptRegistry, posts an Answer, closes the question,
        and transitions the ticket back to IN_PROGRESS.
        """
        from jig.events import JigEvent
        from jig.thread import Answer, Question

        blocking = await self.threads.has_unresolved_blocking(ticket_id)
        questions = [e for e in blocking if isinstance(e, Question)]
        if not questions:
            # No actionable question — fall back to silent polling so the
            # orchestrator doesn't hang if something else will unblock it.
            await self._silent_wait_for_resume(ticket_id)
            return

        question = questions[-1]
        prompt_id, future = self._prompt_registry.register()
        # Agent-asked questions are open-ended prose ("How does X work?",
        # "Should we use A or B?"). Don't synthesize y/n options — that
        # confuses the operator about what each choice means and forces a
        # binary answer onto a free-text question. The TUI will show a
        # "type your answer below" hint instead.
        await self._emitter.emit(
            JigEvent(
                type="prompt_request",
                data={
                    "prompt_id": prompt_id,
                    "prompt_type": "question_answer",
                    "ticket_id": ticket_id,
                    "asker": question.author or "agent",
                    "question_text": question.question,
                    "question": question.question,
                },
            )
        )
        _logger.info(
            "needs_info prompt emitted for ticket %s (prompt_id=%s)",
            ticket_id,
            prompt_id,
        )

        reply = await future
        _logger.info(
            "needs_info reply received for ticket %s: %r", ticket_id, reply[:80]
        )

        await self.threads.post(
            Answer(
                ticket_id=ticket_id,
                author="operator",
                question_id=question.id,
                text=reply,
            )
        )
        await self.threads.update(question.id, {"resolved_by": "operator"})
        await self._update_ticket_status(ticket_id, TicketStatus.IN_PROGRESS)

    async def _silent_wait_for_resume(self, ticket_id: str) -> None:
        """Polling fallback: wait for ticket to leave needs_info without prompting."""
        topic = f"tickets.{ticket_id}"
        queue = await self.bus.subscribe_agent(
            topic=topic,
            agent_id=f"orchestrator:wait:{ticket_id}",
        )
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                    payload = msg.payload or {}
                    if (
                        payload.get("kind") == "ticket_updated"
                        and payload.get("ticket_id") == ticket_id
                        and payload.get("status") != TicketStatus.NEEDS_INFO.value
                    ):
                        return
                except asyncio.TimeoutError:
                    current = await self.tickets.get(ticket_id)
                    if current and current.status != TicketStatus.NEEDS_INFO:
                        return
        finally:
            try:
                await self.bus.unsubscribe(topic, queue)
            except Exception:
                pass

    async def _emit_phase_blocked_by_thread(
        self,
        ticket_id: str,
        phase,
        blocking: list,
    ) -> None:
        """Publish a ``phase_blocked_by_thread`` event on the orchestrator
        topic. Phase 4 Task H: the TUI renders the blocker so a human can
        act (accept a Handoff, resolve an Objection, answer a Question).
        """
        await self.bus.publish(
            Message(
                sender="orchestrator",
                to="broadcast",
                type=MessageType.CONTEXT_UPDATE,
                payload={
                    "kind": "phase_blocked_by_thread",
                    "ticket_id": ticket_id,
                    "phase_name": phase.name,
                    "phase_role": phase.role,
                    "blocking": [
                        {
                            "entry_id": getattr(e, "id", None),
                            "kind": e.kind,
                            "author": e.author,
                        }
                        for e in blocking
                    ],
                },
                topic="orchestrator",
            )
        )

    async def _wait_for_thread_unblock(self, ticket_id: str) -> None:
        """Block until the ticket's blocking thread entries drain.

        Subscribes to the ticket topic for any thread/handoff resolution
        event, then re-checks via ``has_unresolved_blocking``. Poll fallback
        handles message-before-subscribe races.
        """
        topic = f"tickets.{ticket_id}"
        queue = await self.bus.subscribe_agent(
            topic=topic,
            agent_id=f"orchestrator:thread-wait:{ticket_id}",
        )
        try:
            while self._running:
                try:
                    await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                blocking = await self.threads.has_unresolved_blocking(ticket_id)
                if not blocking:
                    return
        finally:
            try:
                await self.bus.unsubscribe(topic, queue)
            except Exception:
                pass

    async def _run_handoff_gate_if_pending(
        self, ticket_id: str, phase, workflow, worktree_path: Path
    ) -> None:
        """Run the automated check gate on this phase's pending handoff.

        If the agent posted a ``Handoff`` whose ``acceptance_state`` is
        still ``pending``, load the project's check catalog and call
        ``run_handoff_gate``. Outcomes:

        * Gate fail → ``bounce_handoff`` flips the handoff to
          ``rejected`` so the caller's ``_phase_handoff_rejected``
          check reroutes to the fix phase via the existing blocked
          retry path.
        * Gate pass AND phase evaluator resolves to
          ``automated_only`` → ``accept_handoff_automated`` flips the
          handoff to ``accepted`` (harness identity). Task O2a.
        * Gate pass AND phase evaluator is role-kind (or multi with
          role members) → spawn the evaluator agent(s) via
          ``_spawn_evaluator`` so they can accept/reject the pending
          handoff. Task O2b.
        * Gate pass AND phase evaluator is ``specific_human`` (or a
          multi whose members are all human) → leave the handoff
          pending for a human accept. No spawn.

        No pending handoff → no-op. The phase either doesn't use the
        handoff primitive or the agent returned success without posting
        one; either way there's nothing to gate.
        """
        from jig.checks import load_check_catalog
        from jig.evaluator_resolver import resolve_evaluator
        from jig.handoff_gate import (
            accept_handoff_automated,
            bounce_handoff,
            run_handoff_gate,
        )

        if (
            self.threads is None
            or self.bus is None
            or self.check_results is None
            or self.tickets is None
        ):
            return

        entries = await self.threads.for_ticket(ticket_id)
        pending_hid: str | None = None
        for e in entries:
            if (
                e.kind == "handoff"
                and getattr(e, "phase", None) == phase.name
                and getattr(e, "acceptance_state", None) == "pending"
            ):
                pending_hid = e.id
        if pending_hid is None:
            return

        catalog = load_check_catalog(self._project_path)
        # Tell scripted checks (notably the diff-scoped pytest helpers
        # at ``jig.check_helpers.pytest_diff``) what to diff against.
        # We pass the LOCAL branch name (no ``origin/`` prefix) because
        # worktrees are created off the project's local default branch
        # and share its git object store — the local ref always
        # resolves. A bare ``origin/<branch>`` would silently produce
        # an empty diff (helper sees no new tests, gate passes
        # vacuously) on hosts that haven't fetched the remote, or on
        # projects with no remote configured at all. Chained tickets
        # (branched off a prior ticket's branch) get a wider diff than
        # strictly necessary — the gate runs more tests than minimally
        # required but doesn't produce wrong verdicts. The helper
        # validates the ref and exits non-zero if it doesn't resolve.
        default_branch = (
            self._project.default_branch if self._project is not None else "main"
        )
        extra_env = {"JIG_TICKET_BASE": default_branch}
        verdict = await run_handoff_gate(
            handoff_id=pending_hid,
            tickets=self.tickets,
            threads=self.threads,
            results=self.check_results,
            catalog=catalog,
            workflow=workflow,
            worktree_path=worktree_path,
            project_path=self._project_path,
            bus=self.bus,
            extra_env=extra_env,
        )
        if not verdict.passing:
            _logger.info(
                "handoff %s bounced by check gate: %d failing, %d missing",
                pending_hid,
                len(verdict.failing),
                len(verdict.missing),
            )
            await bounce_handoff(
                handoff_id=pending_hid,
                threads=self.threads,
                bus=self.bus,
                verdict=verdict,
            )
            return

        # Gate passed. If the phase's evaluator spec resolves to
        # ``automated_only``, harness-accept the handoff. Otherwise
        # leave it pending for the evaluator agent (Task O2b) or a
        # human accept.
        if phase.evaluator is None:
            return
        history = [e for e in entries if isinstance(e, Handoff)]
        resolved = resolve_evaluator(
            spec=phase.evaluator,
            workflow=workflow,
            phase_name=phase.name,
            handoff_history=history,
        )
        if resolved is None:
            return

        if resolved.kind == "automated":
            _logger.info(
                "handoff %s auto-accepted (phase %r automated_only, gate passed)",
                pending_hid,
                phase.name,
            )
            await accept_handoff_automated(
                handoff_id=pending_hid,
                threads=self.threads,
                bus=self.bus,
                checkpoints=self.checkpoints,
            )
            return

        # O2b — spawn evaluator agent(s) for role-kind evaluators.
        # ``multi`` is walked so role members spawn and human members
        # are skipped; the handoff stays pending until each role
        # member acts (and any humans accept via the UI).
        role_actors: list[str] = []
        if resolved.kind == "role":
            role_actors = list(resolved.actors)
        elif resolved.kind == "multi":
            for member in resolved.members:
                if member.kind == "role":
                    role_actors.extend(member.actors)

        if not role_actors:
            # ``human``-only or empty multi → leave pending; a human
            # evaluator resolves the handoff via the TUI.
            return

        for role in role_actors:
            _logger.info(
                "spawning evaluator %r for handoff %s (phase %r)",
                role,
                pending_hid,
                phase.name,
            )
            await self._spawn_evaluator(ticket_id, role, pending_hid)

    async def _phase_handoff_rejected(self, ticket_id: str, phase_name: str) -> bool:
        """Return True if the most recent Handoff for ``phase_name`` on
        this ticket has ``acceptance_state == "rejected"``. A rejected
        Handoff means the evaluator sent the phase back — the caller
        reuses the existing ``blocked`` retry branch.
        """
        if self.threads is None:
            return False
        entries = await self.threads.for_ticket(ticket_id)
        latest = None
        for e in entries:
            if e.kind == "handoff" and e.phase == phase_name:
                latest = e
        return latest is not None and latest.acceptance_state == "rejected"

    async def _auto_commit_worktree(
        self, worktree: Path, phase_name: str, ticket_id: str
    ) -> bool:
        """Commit any uncommitted changes left by an agent after a phase.

        Returns True on success (or when there was nothing to commit),
        False when the commit itself failed. SF-3: callers must inspect
        the result before advancing the phase — a failed auto-commit
        means the phase output isn't captured, so progressing would
        leave subsequent phases reading from a stale tree.
        """
        from jig.worktree import BoundaryViolationError, commit_worktree

        try:
            result = await commit_worktree(
                worktree, f"chore({phase_name}): auto-commit after phase"
            )
            sha = result.sha
        except BoundaryViolationError as exc:
            # Surface the specific violations (not just str(exc)'s count) so a
            # dev agent reading the thread knows which imports to remove without
            # a second commit attempt.
            _logger.warning(
                "auto-commit boundary violations after %s: %s",
                phase_name,
                "; ".join(exc.violations),
            )
            if self.threads is not None:
                try:
                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="orchestrator",
                            event_type="auto_commit_failed",
                            content=(
                                f"auto-commit after {phase_name!r} failed: "
                                f"{len(exc.violations)} module-boundary "
                                f"violation(s): {'; '.join(exc.violations)}"
                            ),
                        )
                    )
                except Exception:
                    _logger.exception("failed to post auto_commit_failed thread entry")
            return False
        except Exception as exc:
            _logger.warning(
                "auto-commit failed after %s: %s",
                phase_name,
                exc,
                exc_info=True,
            )
            if self.threads is not None:
                try:
                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="orchestrator",
                            event_type="auto_commit_failed",
                            content=(f"auto-commit after {phase_name!r} failed: {exc}"),
                        )
                    )
                except Exception:
                    _logger.exception("failed to post auto_commit_failed thread entry")
            return False

        if sha:
            _logger.info(
                "auto-committed leftover changes after %s: %s",
                phase_name,
                sha,
            )
            if self.threads is not None:
                await self.threads.post(
                    SystemEvent(
                        ticket_id=ticket_id,
                        author="orchestrator",
                        event_type="commit",
                        content=f"auto-committed leftover changes: {sha[:7]}",
                        commit_sha=sha,
                    )
                )

        # Module-boundary degradation (semgrep missing / errored): enforcement
        # was skipped, so the auto-commit must not read as a clean boundary
        # pass — surface it on the thread + log, same contract as
        # handle_commit_progress.
        if result.boundary_warnings:
            warn_text = "; ".join(result.boundary_warnings)
            _logger.warning("auto-commit after %s: %s", phase_name, warn_text)
            if self.threads is not None:
                try:
                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="orchestrator",
                            event_type="boundary_check_degraded",
                            content=warn_text,
                        )
                    )
                except Exception:
                    _logger.exception(
                        "failed to post boundary_check_degraded thread entry"
                    )
        return True

    async def _emit_phase_event(
        self,
        event_type: str,
        ticket_id: str,
        phase,
        phase_idx: int,
        total_phases: int,
        *,
        result: str | None = None,
    ) -> None:
        """Emit a phase_started or phase_complete event directly to the TUI."""
        if self._emitter is None:
            return
        from jig.events import JigEvent

        data: dict = {
            "kind": event_type,
            "ticket_id": ticket_id,
            "phase_name": phase.name,
            "phase_role": phase.role,
            "phase_index": phase_idx,
            "total_phases": total_phases,
        }
        if result is not None:
            data["result"] = result
        try:
            await self._emitter.emit(JigEvent(type=event_type, data=data))
        except Exception:
            _logger.warning("emitter.emit raised for %s", event_type, exc_info=True)

    async def _update_ticket_status(self, ticket_id: str, status: TicketStatus) -> None:
        """Update ticket status AND publish a bus event so the TUI sees it."""
        await self.tickets.update_status(ticket_id, status)
        await self.bus.publish(
            TicketUpdated(ticket_id=ticket_id, status=status.value).to_message()
        )

    async def _build_verify_bundle_for_ticket(self, ticket_id: str) -> dict | None:
        """Build the 'Previous Cycle Findings' bundle for a reviewer spawn.

        Returns ``None`` on cycle 1 (no prior acks for this ticket) so
        first-cycle reviewers run unchanged. On cycle 2+, returns the
        full per-finding state (open / addressed / resolved) so the
        reviewer can verify each addressed claim before looking for
        new issues.
        """
        if self.review_comments is None:
            return None
        await self.review_comments.load()
        all_comments = await self.review_comments.for_ticket_chronological(ticket_id)
        if not all_comments:
            return None

        acks_path = self._project_path / ".jig" / "store" / "finding_acks.jsonl"
        acks_path.parent.mkdir(parents=True, exist_ok=True)
        acks_store = FindingAcksStore(acks_path)
        await acks_store.load()
        all_acks = await acks_store.for_ticket(ticket_id)

        return build_verify_bundle(all_comments=all_comments, all_acks=all_acks)

    async def _build_fix_loop_bundle_for_phase(
        self,
        *,
        workflow: "WorkflowConfig",
        blocked_phase_idx: int,
        target_phase_idx: int,
        ticket_id: str,
        worktree: Path,
    ) -> dict | None:
        """Load comments + acks from disk and delegate to
        ``jig.fix_loop_bundle.build_fix_loop_bundle``.

        Returns ``None`` instead of an empty bundle when no comments
        exist for the ticket — the caller treats that as "no bundle
        worth surfacing" and spawns with PHASE_PRIMARY.
        """
        if self.review_comments is None:
            return None
        # Reload to pick up MCP-written comments — same pattern as
        # _route_blocked_phase. The acks store is freshly constructed
        # on every call because the MCP write path may have written
        # through a separate instance.
        await self.review_comments.load()
        all_comments = await self.review_comments.for_ticket_chronological(ticket_id)
        if not all_comments:
            return None

        acks_store_path = self._project_path / ".jig" / "store" / "finding_acks.jsonl"
        acks_store_path.parent.mkdir(parents=True, exist_ok=True)
        acks_store = FindingAcksStore(acks_store_path)
        await acks_store.load()
        all_acks = await acks_store.for_ticket(ticket_id)

        bundle = await build_fix_loop_bundle(
            workflow=workflow,
            blocked_phase_idx=blocked_phase_idx,
            target_phase_idx=target_phase_idx,
            all_comments=all_comments,
            all_acks=all_acks,
            worktree_path=worktree,
        )
        if not bundle["findings"]:
            return None
        return bundle

    async def _worktree_head(self, worktree_path: Path | None) -> str | None:
        """Resolve a worktree's HEAD commit; None on any failure.

        Best-effort — a missing commit only means the next re-review
        falls back to the full-diff base instead of the delta.
        """
        from jig.worktree import _run_git

        try:
            return await _run_git(Path(worktree_path), "rev-parse", "HEAD")
        except Exception:
            _logger.warning(
                "could not resolve worktree HEAD at %s", worktree_path, exc_info=True
            )
            return None

    async def _file_notable_issues(
        self,
        ticket_id: str,
        notables: "list[ReviewerComment]",
    ) -> int:
        """Convert this cycle's notable findings into proposed issues.

        Notables never block (binary severity) — instead each distinct
        notable becomes a ``proposed`` ticket the operator triages via
        the issue front door (``jig issue approve`` before it can ever
        dispatch). Dedup is by finding signature, encoded as a
        ``sig:<digest>`` label and matched against existing
        ``review-notable`` issues parented to the source ticket — so a
        notable re-posted in a later cycle (or after a daemon restart)
        does not file a second issue. Returns the number filed.
        """
        from jig.finding_ids import signature_of
        from jig.ticket import Ticket, TicketStatus, WorkType

        if self.tickets is None or not notables:
            return 0

        # Drop hallucinated notables (file outside the issuing reviewer's
        # reads_glob) before filing — the same guard blocking findings
        # get. Without it, a scoped reviewer that imagines a finding on a
        # file it can't see would create a real operator-triaged issue.
        notables = await self._filter_out_of_scope_comments(notables)
        if not notables:
            return 0

        existing_sigs: set[str] = set()
        for t in await self.tickets.list_all():
            if t.parent_id == ticket_id and "review-notable" in t.labels:
                existing_sigs.update(
                    label for label in t.labels if label.startswith("sig:")
                )

        filed = 0
        for comment in notables:
            sig_label = _notable_sig_label(signature_of(comment))
            if sig_label in existing_sigs:
                continue
            existing_sigs.add(sig_label)
            anchor = comment.file or comment.contract_uri or ""
            title = f"[review] {comment.prose}"
            if len(title) > 100:
                title = title[:97] + "..."
            location = f"{comment.file}:{comment.line}" if comment.file else "n/a"
            description = (
                f"Non-blocking finding from {comment.reviewer} on ticket "
                f"{ticket_id} ({comment.type}, severity notable).\n\n"
                f"Location: {location}\n\n{comment.prose}\n\n"
                "## Acceptance criteria\n"
                f"- The concern described above ({location}) is addressed "
                "and the change passes review.\n"
            )
            issue_id = await self.tickets.create(
                Ticket(
                    work_type=WorkType.REFACTOR,
                    title=title,
                    description=description,
                    status=TicketStatus.PROPOSED,
                    parent_id=ticket_id,
                    labels=["review-notable", sig_label],
                    created_by="review-federation",
                )
            )
            filed += 1
            _logger.info(
                "notable from %s filed as proposed issue %s (%s)",
                comment.reviewer,
                issue_id,
                anchor or comment.type,
            )
            if self._analytics_emitter is not None:
                from jig.analytics.events import NotableIssueFiled

                self._analytics_emitter.emit_nowait(
                    NotableIssueFiled(
                        ticket_id=ticket_id,
                        issue_id=issue_id,
                        reviewer_id=comment.reviewer,
                        comment_type=str(comment.type),
                        file=comment.file,
                    )
                )
        return filed

    async def _route_blocked_phase(
        self,
        workflow: "WorkflowConfig",
        blocked_phase_idx: int,
        ticket_id: str,
        worktree: Path,
    ) -> int | None:
        """Pick the phase to re-run after a block, using per-finding
        routing (review-routing step 8).

        Three sources of "phase blocked" feed this code path:

        1. Review federation found critical/important comments.
        2. Required automated check failed (handoff bounced).
        3. Phase agent returned ``blocked`` directly.

        #1 produces ``ReviewerComment`` records and routes
        per-finding. For #2 and #3 we fall back to the legacy
        "most-recent dev phase" rule that ``_find_fix_phase``
        implemented — those callers have no per-finding metadata to
        route on. Notables never block (binary severity), so they
        never reach this code.

        When reviewer comments are present, the latest cycle is run
        through ``_route_blocking_comments`` (review-routing step 7).
        Either way the chosen target + reason is posted as a thread
        ``fix_loop_route`` Note so operators see why a particular
        phase was selected for retry. Returns ``None`` when no route
        can be found (no dev phase exists in the workflow).
        """
        if self.review_comments is None:
            # Orchestrator not fully started yet — no comments to route
            # on. Fall through to the dev fallback so the caller still
            # gets a deterministic answer.
            all_comments: list[ReviewerComment] = []
        else:
            # Reload from disk: reviewer_mcp.handle_reviewer_post_comment
            # writes through a fresh ReviewCommentsStore instance, so the
            # orchestrator's cached _docs does not see post-startup writes.
            # See PR #51 review thread.
            await self.review_comments.load()
            all_comments = await self.review_comments.for_ticket_chronological(
                ticket_id
            )
        # Most recent cycle = the one that just blocked. The
        # FixLoopTracker auto-increments cycle per record, so max()
        # identifies the latest.
        blocking: list[ReviewerComment] = []
        if all_comments:
            latest_cycle = max(c.cycle for c in all_comments)
            blocking = [
                c
                for c in all_comments
                if c.cycle == latest_cycle and c.severity in ("critical", "important")
            ]

        # Filter out findings whose file is outside the issuing
        # reviewer's ``reads_glob``. These are LLM-hallucinated
        # comments — the reviewer literally couldn't have seen the
        # file via ``reviewer_read_file``. They must NOT bounce the
        # ticket: dropping here (rather than only in ``_route_one``
        # later) means the survivor set determines whether we even
        # take the blocking branch. If every blocking comment is
        # hallucinated, we fall through to the check-failure-fallback
        # path the same as if there were no blocking comments at
        # all — instead of routing returning ``None`` and the
        # caller failing the ticket on findings it should have
        # ignored.
        if blocking:
            blocking = await self._filter_out_of_scope_comments(blocking)

        if blocking:
            route = await _route_blocking_comments(
                workflow,
                blocked_phase_idx,
                blocking,
                worktree,
                project_path=self._project_path,
            )
        else:
            # Check-failure / agent-blocked-without-comments path —
            # legacy most-recent-dev fallback. Also covers the case
            # where every blocking comment was out-of-scope and got
            # filtered above. (Notables never block under binary
            # severity, so there is no notable-only block to route.)
            fix_idx = _most_recent_phase_with_role(workflow, blocked_phase_idx, "dev")
            route = (fix_idx, "check-failure-fallback") if fix_idx is not None else None

        if route is None:
            return None
        fix_idx, reason = route
        target_phase = workflow.phases[fix_idx]
        blocked_phase = workflow.phases[blocked_phase_idx]
        if self.threads is not None:
            try:
                await self.threads.post(
                    SystemEvent(
                        ticket_id=ticket_id,
                        author="orchestrator",
                        event_type="fix_loop_route",
                        content=(
                            f"Routing blocked phase {blocked_phase.name!r} back "
                            f"to {target_phase.name!r} (reason: {reason})"
                        ),
                    )
                )
            except Exception:
                _logger.warning(
                    "_route_blocked_phase: failed to post fix_loop_route note "
                    "for ticket %s",
                    ticket_id,
                    exc_info=True,
                )
        return fix_idx

    async def _filter_out_of_scope_comments(
        self, comments: "list[ReviewerComment]"
    ) -> "list[ReviewerComment]":
        """Drop reviewer comments whose ``file`` is outside the
        issuing reviewer's ``reads_glob``.

        Defence-in-depth filter applied at two call sites:

        1. ``_run_review_phase_federation`` — runs BEFORE the
           ``status="blocked"`` decision. When the survivor set is
           empty, the review **passes**: no blockers means nothing
           to bounce on, the next phase proceeds normally.

        2. ``_route_blocked_phase`` — runs again on the survivor
           set after the review reported ``blocked``. Catches the
           residual case (e.g. follow-up cycle's filter changed,
           role config edits between phase end and routing). When
           empty here, the orchestrator falls through to the
           check-failure-fallback path (route to most-recent dev)
           — same as if there were no blocking comments at all.

        Either way the ticket does NOT fail on a federation
        consisting entirely of out-of-scope (hallucinated)
        findings.

        Returns the subset of ``comments`` that survive the scope
        check. Comments from reviewers whose role config we can't
        load, or that don't declare ``reads_glob``, pass through
        unchanged (matches legacy unscoped behaviour).
        """
        from jig.persistence import load_role
        from jig.scope import path_in_scope

        survivors: list[ReviewerComment] = []
        for c in comments:
            if c.file is None:
                # Diff-wide findings have no file to scope-check.
                survivors.append(c)
                continue
            try:
                cfg = load_role(self._project_path, c.reviewer)
            except FileNotFoundError:
                # Unknown reviewer role — don't second-guess.
                survivors.append(c)
                continue
            if not cfg.reads_glob:
                survivors.append(c)
                continue
            if path_in_scope(c.file, include=cfg.reads_glob, exclude=cfg.reads_exclude):
                survivors.append(c)
            else:
                _logger.warning(
                    "_filter_out_of_scope_comments: dropping "
                    "blocking comment from %s on %s (reads_glob=%s, "
                    "exclude=%s) — hallucinated finding on a file "
                    "the reviewer could not have read.",
                    c.reviewer,
                    c.file,
                    cfg.reads_glob,
                    cfg.reads_exclude,
                )
        return survivors

    async def _current_phase_index(self, ticket_id: str, workflow) -> int:
        """Return the index of the first phase that has not yet succeeded.

        Delegates to ``jig.phase.current_phase_index`` — that helper is
        shared with the hooks runner so pre-push can answer the same
        question without an orchestrator instance.
        """
        from jig.phase import current_phase_index

        if self.threads is None:
            raise RuntimeError("Orchestrator not started")
        return await current_phase_index(self.threads, ticket_id, workflow)

    async def _write_phase_run_comment(self, ticket_id: str, phase, result) -> None:
        if self.threads is None:
            raise RuntimeError("Orchestrator not started")

        phase_result: str = (
            result.status
            if result.status in {"success", "failed", "blocked", "needs_info"}
            else "failed"
        )

        await self.threads.post(
            SystemEvent(
                ticket_id=ticket_id,
                author="orchestrator",
                event_type="phase_run",
                content=f"phase {phase.name}: {result.status}",
                phase_result=phase_result,  # type: ignore[arg-type]
                phase_branch=f"jig/{ticket_id}",
            )
        )

    async def _run_dispatch_loop(self) -> None:
        """Fan-out listener: spawn QA responders for unaddressed bus events.

        Subscribes to ALL bus publishes via ``add_websocket_listener``,
        queues them locally, then filters for ticket-topic events
        addressed to a non-user role that does not already have a live
        subscriber. A filtered event triggers ``_spawn_qa_responder``.
        """
        if self.bus is None:
            raise RuntimeError("Orchestrator not started — call startup() first")

        queue: asyncio.Queue = asyncio.Queue()

        async def _forward(message) -> None:
            await queue.put(message)

        await self.bus.add_websocket_listener(_forward)

        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            # Mirror to emitter for TUI consumption (before target filter so all messages reach it).
            # Skip _internal events (e.g. agent-side resolved that the orchestrator will override).
            payload = msg.payload or {}
            if self._emitter is not None and not payload.get("_internal"):
                from jig.events import JigEvent

                kind = payload.get("kind", "event")
                try:
                    await self._emitter.emit(JigEvent(type=kind, data=payload))
                except Exception:
                    _logger.warning("emitter.emit raised; continuing", exc_info=True)

            target = self._resolve_target(msg)
            if target is None:
                continue
            ticket_id, role = target
            if role == "user":
                continue
            # If this ticket is currently running through the workflow pipeline,
            # the pipeline owns it — don't spawn QA responders.
            if ticket_id in self._running_tickets:
                continue
            if (ticket_id, role) in self._live_subscribers:
                continue
            await self._spawn_qa_responder(ticket_id, role, msg)

    def _resolve_target(self, msg) -> tuple[str, str] | None:
        """Extract (ticket_id, role) from a ticket-topic directed message."""
        if not msg.topic.startswith("tickets."):
            return None
        ticket_id = msg.topic.removeprefix("tickets.")
        if not msg.to or msg.to == "broadcast":
            return None
        return (ticket_id, msg.to)

    async def _spawn_qa_responder(
        self, ticket_id: str, role: str, initial_event
    ) -> None:
        from jig.persistence import load_role
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            raise RuntimeError("Orchestrator not started — call startup() first")

        # Reserve the slot synchronously before any await so that the
        # dispatch loop's duplicate-spawn check sees it immediately.  Use the
        # currently-running task as a cheap placeholder; it is replaced with
        # the real run_agent task once setup succeeds.
        self._live_subscribers[(ticket_id, role)] = asyncio.current_task()  # type: ignore[assignment]

        try:
            ticket = await self.tickets.get(ticket_id)
            if ticket is None:
                self._live_subscribers.pop((ticket_id, role), None)
                return
            parent = None
            if ticket.parent_id:
                parent = await self.tickets.get(ticket.parent_id)
            role_cfg = load_role(self._project_path, role)
            worktree = await self._ensure_worktree(parent or ticket)
        except FileNotFoundError:
            _logger.warning(
                "unknown agent role %r — check agent used a valid role name", role
            )
            self._live_subscribers.pop((ticket_id, role), None)
            return
        except Exception:
            _logger.exception("failed to spawn qa responder for %s/%s", ticket_id, role)
            self._live_subscribers.pop((ticket_id, role), None)
            return

        ctx = AgentSpawnContext(
            role=role,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.QA_RESPONDER,
            ticket=ticket,
            parent=parent,
            worktree_path=worktree,
            project=self._project,
            tickets=self.tickets,
            threads=self.threads,
            memory=self.memory,
            bus=self.bus,
            checkpoints=self.checkpoints,
            initial_bus_message=initial_event.payload if initial_event else None,
        )
        task = asyncio.create_task(self._run_agent_with_analytics(ctx))
        self._live_subscribers[(ticket_id, role)] = task

        # Log any exception raised inside the run_agent task.
        def _cleanup(t: asyncio.Task) -> None:
            self._live_subscribers.pop((ticket_id, role), None)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                _logger.warning(
                    "qa responder for %s/%s raised",
                    ticket_id,
                    role,
                    exc_info=exc,
                )

        task.add_done_callback(_cleanup)

    async def _spawn_evaluator(
        self, ticket_id: str, role: str, handoff_id: str
    ) -> None:
        """Spawn an evaluator agent to review a pending handoff.

        Called by ``_run_handoff_gate_if_pending`` on gate-pass when
        the phase's evaluator spec resolves to a role-kind identity
        (``specific_role`` / ``previous_phase_role``) — including role
        members of a ``multi`` spec. The evaluator's job is to inspect
        the handoff's outputs and accept or reject it via the
        ``thread_accept_handoff`` / ``thread_reject_handoff`` MCP
        tools.

        Runs as a background task; the caller returns immediately and
        the per-ticket loop's ``has_unresolved_blocking`` wait does the
        actual blocking until the evaluator resolves the handoff.

        No-op if an agent for ``(ticket_id, role)`` is already
        subscribed — two phases with the same evaluator role on the
        same ticket share the subscriber slot.
        """
        from jig.persistence import load_role
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            return

        if (ticket_id, role) in self._live_subscribers:
            return

        self._live_subscribers[(ticket_id, role)] = asyncio.current_task()  # type: ignore[assignment]

        try:
            ticket = await self.tickets.get(ticket_id)
            if ticket is None:
                self._live_subscribers.pop((ticket_id, role), None)
                return
            parent = None
            if ticket.parent_id:
                parent = await self.tickets.get(ticket.parent_id)
            role_cfg = load_role(self._project_path, role)
            worktree = await self._ensure_worktree(parent or ticket)
        except FileNotFoundError:
            _logger.warning(
                "unknown evaluator role %r for ticket %s — check "
                "phase.evaluator config",
                role,
                ticket_id,
            )
            self._live_subscribers.pop((ticket_id, role), None)
            return
        except Exception:
            _logger.exception("failed to spawn evaluator for %s/%s", ticket_id, role)
            self._live_subscribers.pop((ticket_id, role), None)
            return

        # Phase 5 Task C/E/J — assemble the evaluator bundle so the
        # prompt builder can render structured check results alongside
        # the handoff. The bundle always carries the handoff id; the
        # check-results list is best-effort — if the handoff entry or
        # the check_results store is unavailable we still spawn with a
        # bare bundle, and the prompt builder degrades gracefully.
        check_results_payload: list[dict] = []
        try:
            pending_handoff = await self.threads.get(handoff_id)
            if (
                pending_handoff is not None
                and pending_handoff.kind == "handoff"
                and self.check_results is not None
            ):
                batch = await self.check_results.latest_batch(
                    ticket_id, pending_handoff.phase
                )
                check_results_payload = [
                    {
                        "check_name": r.check_name,
                        "verdict": r.verdict,
                        "severity": r.severity,
                        "output": r.output,
                    }
                    for r in batch
                ]
        except Exception:
            _logger.exception(
                "failed to assemble check-results bundle for evaluator "
                "spawn on ticket %s handoff %s; spawning with empty batch",
                ticket_id,
                handoff_id,
            )

        ctx = AgentSpawnContext(
            role=role,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.EVALUATOR,
            ticket=ticket,
            parent=parent,
            worktree_path=worktree,
            project=self._project,
            tickets=self.tickets,
            threads=self.threads,
            memory=self.memory,
            bus=self.bus,
            checkpoints=self.checkpoints,
            initial_bus_message={
                "kind": "thread_handoff_evaluator_spawn",
                "ticket_id": ticket_id,
                "handoff_id": handoff_id,
                "check_results": check_results_payload,
            },
        )
        task = asyncio.create_task(self._run_agent_with_analytics(ctx))
        self._live_subscribers[(ticket_id, role)] = task

        def _cleanup(t: asyncio.Task) -> None:
            self._live_subscribers.pop((ticket_id, role), None)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                _logger.warning(
                    "evaluator for %s/%s raised",
                    ticket_id,
                    role,
                    exc_info=exc,
                )

        task.add_done_callback(_cleanup)
