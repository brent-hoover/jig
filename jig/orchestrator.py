"""Singleton orchestrator — tracks tickets, runs agents, dispatches bus events."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.coordinator import Coordinator
    from jig.events import EventEmitter
    from jig.prompt_registry import PromptRegistry
    from jig.ticket import Ticket

from jig.agent import run_agent
from jig.stall_detector import StallDetector
from jig.analytics.emitter import EventEmitter as AnalyticsEmitter
from jig.analytics.events import AgentCompleted, AgentSpawned, TicketGraphImpact, TicketStateChanged
from jig.analytics.store import AnalyticsStore
from jig.config import DeadlockSection, OrchestratorSection, load_config
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
from jig.store.check_results import CheckResultsStore
from jig.store.checkpoints import CheckpointStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus

_logger = logging.getLogger(__name__)

# Wall-clock cadence between deadlock sweeps. Shorter than both
# thresholds so a sweep always runs within a useful delay of an
# entry crossing T1, but long enough to not burn cycles on a
# healthy project. Tests that want fast iteration create tight
# sweeps by calling ``sweep_blocking_entries`` directly.
DEADLOCK_SWEEP_INTERVAL_S = 60.0


def _kill_orphan_claude_processes(project_path: Path) -> int:
    """SIGTERM any ``claude`` CLI subprocess whose CWD is inside the
    project's worktrees directory. Returns the number of PIDs signalled.

    macOS-only (uses lsof). Falls back to a no-op on platforms without it.
    """
    worktrees_root = project_path / ".jig" / "worktrees"
    if not worktrees_root.is_dir():
        return 0
    killed = 0
    try:
        for child in worktrees_root.iterdir():
            if not child.is_dir():
                continue
            result = subprocess.run(
                ["lsof", "-d", "cwd", "-Fp", "+D", str(child)],
                capture_output=True, text=True, check=False,
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
                    capture_output=True, text=True, check=False,
                ).stdout.strip()
                if "claude" not in cmd:
                    continue
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except ProcessLookupError:
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
    ) -> None:
        self._project_path = project_path
        self._emitter = emitter
        self._prompt_registry = prompt_registry
        self._project: Project | None = None
        self.tickets: TicketStore | None = None
        self.threads: ThreadStore | None = None
        self.checkpoints: CheckpointStore | None = None
        self.memory: MemoryStore | None = None
        self.bus: MessageBus | None = None
        self.check_results: CheckResultsStore | None = None
        # v2 analytics — append-only event capture for the consumer half
        # described in docs/v2.0/analytics/. Initialized in startup once the
        # store directory exists.
        self.analytics: AnalyticsStore | None = None
        self._analytics_emitter: AnalyticsEmitter | None = None

        self._running_tickets: dict[str, asyncio.Task] = {}
        self._live_subscribers: dict[tuple[str, str], asyncio.Task] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._service_task: asyncio.Task | None = None
        self._deadlock_task: asyncio.Task | None = None
        self._stall_task: asyncio.Task | None = None
        self._stall_detector: StallDetector = StallDetector()
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
                "Orchestrator not started — call startup() before "
                "accessing coordinator"
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
            self.tickets = TicketStore(store_dir / "tickets.jsonl")
            self.threads = ThreadStore(store_dir / "comments.jsonl")
            # CheckpointStore is a separate channel per doc 09.
            self.checkpoints = CheckpointStore(store_dir / "checkpoints.jsonl")
            self.memory = MemoryStore(store_dir)
            self.bus = MessageBus(store_dir / "messages.jsonl")
            self.check_results = CheckResultsStore(store_dir / "check_results.jsonl")
            self.analytics = AnalyticsStore(store_dir / "analytics.jsonl")
            await asyncio.gather(
                self.tickets.load(),
                self.threads.load(),
                self.checkpoints.load(),
                self.memory.load(),
                self.bus.load(),
                self.check_results.load(),
                self.analytics.load(),
            )
            self._analytics_emitter = AnalyticsEmitter(self.analytics)
            self.tickets.set_status_change_callback(self._on_ticket_status_change)
            # Phase 5 Task L: load deadlock thresholds from
            # `.jig/config.yaml`. Missing config (fresh install,
            # tests) falls back to the shipped defaults rather
            # than failing startup.
            try:
                cfg = load_config(self._project_path)
                self._deadlock_cfg = cfg.deadlock
                self._orchestrator_cfg = cfg.orchestrator
            except FileNotFoundError:
                self._deadlock_cfg = DeadlockSection()
                self._orchestrator_cfg = OrchestratorSection()
            except Exception:
                _logger.warning(
                    "could not load deadlock config; using defaults",
                    exc_info=True,
                )
                self._deadlock_cfg = DeadlockSection()
                self._orchestrator_cfg = OrchestratorSection()
            self._running = True
            await self._resume_in_progress()
            await self._ensure_planning_ticket()
            await self._start_ready_tickets()
            self._dispatch_task = asyncio.create_task(self._run_dispatch_loop())
            self._service_task = asyncio.create_task(self._run_service_loop())
            self._deadlock_task = asyncio.create_task(self._run_deadlock_loop())
            self._stall_task = asyncio.create_task(self._run_stall_loop())
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
        await self.startup()

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

    async def _run_agent_with_analytics(
        self, ctx, *, spawned_by: str = "orchestrator"
    ):
        """Wrap run_agent with AgentSpawned/AgentCompleted emission.

        Returns the same ``RunAgentResult`` ``run_agent`` would. Bones
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
                        consumer_count=sum(
                            len(v) for v in _impact.consumers.values()
                        ),
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
            await ctx.tickets.update(
                ctx.ticket.id, status=TicketStatus.FAILED
            )
            from jig.agent import RunAgentResult

            return RunAgentResult(
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
        ctx.on_thinking = lambda: self._stall_detector.record_heartbeat(ctx.role)
        self._stall_detector.record_agent_start(ctx.role)
        start = time.monotonic()
        result_status = "failed"
        cost_usd: float | None = None
        tokens_in: int | None = None
        tokens_out: int | None = None
        try:
            result = await run_agent(ctx, emitter=self._emitter)
            result_status = self._map_result_status(result.status)
            cost_usd = result.total_cost_usd
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            return result
        finally:
            self._stall_detector.record_agent_done(ctx.role)
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
                ctx, agent_id=agent_id, result_status=result_status,
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
        self, ctx, *, agent_id: str, result_status: str,
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

            signals = await check_escalation_signals(
                ctx.ticket.id, self.analytics
            )
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

    async def _run_review_federation(
        self, ticket_id: str, ticket
    ) -> None:
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

        # ---- run dispatch with single-retry policy --------------------
        try:
            by_reviewer = await dispatch_with_llm_spawn(
                ticket,
                self._project_path,
                "end_of_ticket",
                self,
                worktree_path=worktree_arg,
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
                    "end_of_ticket",
                    self,
                    worktree_path=worktree_arg,
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

        # The notable→DEFERRED branch only fires when the ticket is
        # actually resolving; criticals (→FAILED) and importants
        # (→BLOCKED) take precedence and the notables on the same
        # ticket should NOT land in the deferred queue. Pass a
        # coordinator only when no higher-severity comments are
        # present so ``apply_severity_disposition`` skips the defer
        # call cleanly without re-implementing the precedence rule.
        from jig.reviewers.comment import Severity as _Severity

        has_higher_severity = any(
            c.severity in (_Severity.CRITICAL.value, _Severity.IMPORTANT.value)
            for c in comments
        )
        coord_arg: object | None = (
            None if has_higher_severity else self.coordinator
        )

        result = await apply_severity_disposition(
            comments,
            ticket,
            self.tickets,
            coord_arg,
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
            await self.tickets.update(
                ticket_id, block_reason="reviewer-important"
            )
        # Notable-only path: ticket stays RESOLVED, the Coordinator
        # already deferred it inside apply_severity_disposition.

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
            worktree_path = (
                self._project_path / ".jig" / "worktrees" / ticket.id
            )

        ctx = AgentSpawnContext(
            role=reviewer_id,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.QA_RESPONDER,
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
                "spawn_review_agent_for_id: reviewer %s spawn failed for "
                "ticket %s",
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
                        content=(
                            f"reviewer {reviewer_id!r} failed to run: {exc}"
                        ),
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
        for task in (self._dispatch_task, self._service_task, self._deadlock_task, self._stall_task):
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
        self._project = None
        self.tickets = None
        self.threads = None
        self.memory = None
        self.bus = None
        self.check_results = None
        self.checkpoints = None

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
        tasks_to_cancel.extend(self._running_tickets.values())
        tasks_to_cancel.extend(self._live_subscribers.values())
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
        self._dispatch_task = None
        self._service_task = None
        self._deadlock_task = None
        self._stall_task = None
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
                payload = msg.payload or {}
                kind = payload.get("kind")
                _logger.debug("service loop received: %s", kind)
                if kind == "ticket_created":
                    ticket_id = payload.get("ticket_id")
                    if ticket_id:
                        _logger.info("ticket_created event: %s", ticket_id)
                        await self._handle_schedule(ticket_id)
                elif kind == "ticket_updated":
                    ticket_id = payload.get("ticket_id")
                    new_status = payload.get("status")
                    # Re-enqueue tickets reset to "open" (e.g. retry after failure)
                    if ticket_id and new_status == TicketStatus.OPEN.value:
                        _logger.info(
                            "ticket %s reset to open — re-scheduling", ticket_id
                        )
                        self._running_tickets.pop(ticket_id, None)
                        await self._handle_schedule(ticket_id)
                elif kind == "shutdown_request":
                    self._running = False
        finally:
            await self.bus.unsubscribe("orchestrator", queue)

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
            if last_action_at is not None and (now - last_action_at) < thresholds.cooldown_seconds:
                continue
            last_action_at = now
            _logger.warning(
                "stall detected: signal=%s elapsed=%.0fs detail=%s",
                verdict.signal,
                verdict.seconds_since_last_event,
                verdict.detail,
            )
            killed = _kill_orphan_claude_processes(self._project_path)
            if killed:
                _logger.info("stall recovery: SIGTERMed %d orphan claude process(es)", killed)

    async def _handle_schedule(self, ticket_id: str) -> None:
        """Schedule a ticket if its dependencies are satisfied.

        Only top-level work tickets (feature/bug/chore) go through the
        workflow pipeline. If the ticket has unresolved dependencies it
        stays open — ``_unblock_dependents`` will re-check when deps resolve.
        """
        if self.tickets is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
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
                if dep is None or dep.status != TicketStatus.RESOLVED:
                    _logger.info(
                        "ticket %s blocked by %s (status=%s), deferring",
                        ticket_id,
                        dep_id,
                        dep.status.value if dep else "missing",
                    )
                    return
        _logger.info("scheduling ticket %s (workflow=%s)", ticket_id, ticket.workflow)
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
                workflow = load_workflow(self._project_path, ticket.workflow)
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
                    role_cfg = load_role(self._project_path, phase.role)

                    # Tell the TUI which phase is running
                    await self._emit_phase_event(
                        "phase_started", ticket_id, phase, phase_idx, len(workflow.phases)
                    )

                    ctx = AgentSpawnContext(
                        role=phase.role,
                        role_cfg=role_cfg,
                        spawn_reason=SpawnReason.PHASE_PRIMARY,
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
                    )
                    sub_key = (ticket_id, phase.role)
                    self._live_subscribers[sub_key] = asyncio.current_task()  # type: ignore[assignment]
                    try:
                        _logger.info(
                            "spawning agent for %s on ticket %s", phase.role, ticket_id
                        )
                        result = await self._run_agent_with_analytics(ctx)
                        _logger.info("agent %s finished: %s", phase.role, result.status)
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
                        await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
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
                            blocking = await self.threads.has_unresolved_blocking(ticket_id)
                            while blocking:
                                await self._emit_phase_blocked_by_thread(
                                    ticket_id, phase, blocking
                                )
                                await self._wait_for_thread_unblock(ticket_id)
                                blocking = await self.threads.has_unresolved_blocking(ticket_id)
                            if await self._phase_handoff_rejected(ticket_id, phase.name):
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
                            "ticket %s resumed — re-running phase %s", ticket_id, phase.name
                        )
                        ticket = await self.tickets.get(ticket_id)
                        continue  # re-run same phase_idx

                    if result.status == "blocked":
                        fix_counts[phase_idx] = fix_counts.get(phase_idx, 0) + 1
                        if fix_counts[phase_idx] > max_fix_cycles:
                            _logger.warning(
                                "phase %s blocked %d times — giving up on ticket %s",
                                phase.name,
                                fix_counts[phase_idx],
                                ticket_id,
                            )
                            await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
                            await self._on_ticket_failed(ticket_id, ticket)
                            return

                        # Find the most recent dev/writing phase before this one to fix issues.
                        fix_idx = self._find_fix_phase(workflow, phase_idx)
                        if fix_idx is not None:
                            _logger.info(
                                "phase %s blocked — routing back to %s (attempt %d/%d)",
                                phase.name,
                                workflow.phases[fix_idx].name,
                                fix_counts[phase_idx],
                                max_fix_cycles,
                            )
                            await self._update_ticket_status(
                                ticket_id, TicketStatus.IN_PROGRESS
                            )
                            await self._auto_commit_worktree(worktree, phase.name, ticket_id)
                            phase_idx = fix_idx
                            ticket = await self.tickets.get(ticket_id)
                            continue

                        _logger.warning(
                            "phase %s blocked but no fix phase found — failing", phase.name
                        )

                    # Unrecoverable: fail the ticket.
                    await self._update_ticket_status(ticket_id, TicketStatus.FAILED)
                    await self._on_ticket_failed(ticket_id, ticket)
                    return

                finally:
                    if self.threads is not None:
                        outcome = result.status if result is not None else "failed"
                        duration_ms = int(
                            (time.monotonic() - phase_started_at) * 1000
                        )
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

    async def _try_resolve_conflict(self, ticket_id: str, ticket: "Ticket") -> bool:
        """Spawn the built-in conflict_resolver agent to fix conflict markers.

        Returns True if the agent completes without exception (caller should
        retry the merge). Returns False on any failure — missing role,
        spawn error, agent crash — so a resolver failure never turns a
        conflict into an orchestrator crash.
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
        try:
            result = await self._run_agent_with_analytics(ctx, spawned_by="conflict_resolver")
            return result.status == "success"
        except Exception:
            _logger.warning(
                "_try_resolve_conflict: agent failed for %s",
                ticket_id,
                exc_info=True,
            )
            return False

    async def _try_replan(
        self,
        ticket_id: str,
        ticket: "Ticket",
        conflicted_files: list[str],
    ) -> None:
        """Spawn a PM agent in REPLAN mode after a conflict is auto-resolved.

        Fire-and-forget — never raises. Tightens depends_on on pending tickets
        that are likely to touch the same files that just conflicted.
        """
        from jig.runtime import AgentSpawnContext, SpawnReason

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
        try:
            await self._run_agent_with_analytics(ctx, spawned_by="replan")
        except Exception:
            _logger.warning(
                "_try_replan: agent failed for %s",
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
                        asyncio.create_task(
                            self._try_replan(ticket_id, ticket, conflicted_files)
                        )
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
                        merge_result = f"merge retry failed (branch {branch_name} preserved)"
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

        # Review-federation gate — runs as a **gate** on resolution per
        # ``docs/v2.0/pm-workflow/design.md`` §"Severity tiers and disposition".
        # When enabled, ``_run_review_federation`` may roll the ticket
        # back to FAILED (critical comments / federation crash) or
        # BLOCKED (important comments). Notable comments on a clean
        # ticket leave the status RESOLVED but defer via the
        # Coordinator. Flag-off preserves the legacy passive behavior
        # — federation never runs.
        if self._orchestrator_cfg.run_review_federation:
            await self._run_review_federation(ticket_id, ticket)

        # Re-load the ticket so we route the post-resolve cleanup off
        # the gate's actual outcome rather than the pre-gate optimism.
        if self.tickets is not None:
            post_gate = await self.tickets.get(ticket_id)
            if post_gate is not None and post_gate.status in (
                TicketStatus.FAILED,
                TicketStatus.BLOCKED,
            ):
                # Gate fired — defer to the failure path so the
                # ticket_failed event lands and cleanup runs the
                # blocked/failed branch. Worktree + branch are
                # preserved so the operator can address the comments
                # and re-run the federation.
                if post_gate.status == TicketStatus.FAILED:
                    await self._on_ticket_failed(ticket_id, ticket)
                else:
                    # BLOCKED — same observable cleanup; no merge
                    # rollback (the dev branch is already merged).
                    self._running_tickets.pop(ticket_id, None)
                    await self._start_ready_tickets()
                return

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

        try:
            await remove_worktree(self._project_path, ticket_id, keep_branch=True)
            _logger.info(
                "worktree removed for %s (branch %s preserved)", ticket_id, branch_name
            )
        except Exception:
            _logger.warning("worktree cleanup failed for %s", ticket_id, exc_info=True)

        await self._unblock_dependents(ticket_id, ticket)
        await self._start_ready_tickets()

    async def _on_ticket_failed(self, ticket_id: str, ticket) -> None:
        """Post-failure: emit event, clean up, pick up next ticket."""
        _logger.info("ticket %s failed", ticket_id)

        if self._emitter is not None:
            from jig.events import JigEvent

            await self._emitter.emit(
                JigEvent(
                    type="ticket_failed",
                    data={
                        "kind": "ticket_failed",
                        "ticket_id": ticket_id,
                        "title": ticket.title,
                    },
                )
            )

        self._running_tickets.pop(ticket_id, None)
        await self._start_ready_tickets()

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
        arch = self._project_path / "docs" / "architecture.yaml"
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
        spec_path = self._project_path / "docs" / "project.structured.yaml"
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
        _logger.info("needs_info prompt emitted for ticket %s (prompt_id=%s)", ticket_id, prompt_id)

        reply = await future
        _logger.info("needs_info reply received for ticket %s: %r", ticket_id, reply[:80])

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
        from jig.worktree import commit_worktree

        try:
            sha = await commit_worktree(
                worktree, f"chore({phase_name}): auto-commit after phase"
            )
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
                            content=(
                                f"auto-commit after {phase_name!r} failed: "
                                f"{exc}"
                            ),
                        )
                    )
                except Exception:
                    _logger.exception(
                        "failed to post auto_commit_failed thread entry"
                    )
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
            Message(
                sender="orchestrator",
                to="broadcast",
                type=MessageType.CONTEXT_UPDATE,
                payload={
                    "kind": "ticket_updated",
                    "ticket_id": ticket_id,
                    "status": status.value,
                },
                topic=f"tickets.{ticket_id}",
            )
        )

    @staticmethod
    def _find_fix_phase(workflow, blocked_phase_idx: int) -> int | None:
        """Find the phase to re-run when a phase returns blocked.

        Scans backward from ``blocked_phase_idx`` for a phase whose role
        has write access (dev agent). Returns the index or None.
        """
        _write_roles = {"dev"}
        for idx in range(blocked_phase_idx - 1, -1, -1):
            if workflow.phases[idx].role in _write_roles:
                return idx
        return None

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
