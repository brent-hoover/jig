"""Typed analytics event models for jig.

Per ``docs/analytics/problem.md``: capture decisions can't be
retrofitted, so this schema is locked from v1 onward. Additive
changes only; any breaking change requires bumping
``EVENT_VERSION`` and writing a migration on the consumer side.

Privacy is enforced at schema-design time: no event type carries
prose, prompts, responses, secrets, or PII. Content references
external artifacts by URI; tool arguments by digest. If you're
adding a new event type and feel tempted to add a ``content``,
``prompt``, ``response``, or ``body`` field, the right move is
to store the content in another artifact (thread, mcp log, file)
and reference it from the event.

Event taxonomy mirrors the categories in
``docs/analytics/problem.md``:

* Ticket state transitions
* Agent lifecycle (spawn, completion)
* Tool calls (MCP invocations)
* Context fetches (auto-injected, pulled, always-injected)
* Review events (per-comment posted and resolved)
* Escalations (routed up, resolved)
* Contract events (amended, violation detected)
* Risk events (status changes)
* Plan events (build plan revisions, layer status changes)
* Operator actions (overrides, gate confirmations)

Pattern follows ``jig/thread.py``: each event subclass narrows
``kind`` to a ``Literal`` discriminator; ``AnalyticsEvent`` is
the discriminated union; ``parse_event`` is the entry point for
loading raw dicts from the persistence layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from jig.store.models import StoreModel


# Bump only on breaking schema changes. Additive changes (new
# optional fields, new event types) do NOT bump this.
EVENT_VERSION = 1


# ---- shared base ----------------------------------------------------------


class _EventBase(StoreModel):
    """Shared envelope for every analytics event.

    Concrete subtypes narrow ``kind`` to a ``Literal`` so the
    discriminated union below can route. Not a union member
    itself.

    Two correlation fields:

    * ``correlation_id`` groups events that belong to the same
      logical operation (a ticket dispatch through agent spawn,
      tool calls, completion).
    * ``parent_event_id`` points at the immediate cause when
      events are causally nested (e.g. a ToolCalled fires under
      an AgentSpawned).
    """

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    event_version: int = EVENT_VERSION
    correlation_id: str | None = None
    parent_event_id: str | None = None

    # True when emitted from a synthetic-operator simulator run (per
    # docs/synthetic-operator/design.md). Consumer queries filter to
    # ``simulator=False`` by default; simulator events live in their own
    # logical corpus so they don't pollute real-project analytics.
    # The EventEmitter sets this based on the JIG_SIMULATOR env var or
    # an explicit ``simulator_mode`` constructor argument.
    simulator: bool = False


# ---- Ticket state transitions ---------------------------------------------


class TicketStateChanged(_EventBase):
    """A ticket transitioned between lifecycle states."""

    kind: Literal["ticket_state_changed"] = "ticket_state_changed"
    ticket_id: str
    from_state: str | None = None  # None on initial creation
    to_state: str
    reason: str | None = None  # short structured reason; e.g. "agent_completed"


# ---- Agent lifecycle ------------------------------------------------------


class AgentSpawned(_EventBase):
    """An agent process was started for a unit of work."""

    kind: Literal["agent_spawned"] = "agent_spawned"
    agent_id: str
    role: str  # po | sa | planner_pm | coordinator_pm | dev | reviewer | retro | ...
    tier: Literal["standard", "senior", "sa"] | None = None
    model: str
    ticket_id: str | None = None  # null for non-ticket-scoped agents (e.g. PO)
    spawned_by: str  # "orchestrator" | <agent_id> | "operator"


class AgentCompleted(_EventBase):
    """An agent finished its run."""

    kind: Literal["agent_completed"] = "agent_completed"
    agent_id: str
    status: Literal["success", "failed", "blocked", "timeout", "killed"]
    duration_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate_usd: float | None = None
    failure_category: str | None = None  # short structured category if failed


# ---- Tool calls -----------------------------------------------------------


class ToolCalled(_EventBase):
    """An agent invoked an MCP tool.

    Args stored as digest (sha256 of canonical JSON) plus byte size.
    Full content lives in mcp logs / thread entries; the digest is
    enough to dedupe identical calls and detect args-pattern repeats.
    """

    kind: Literal["tool_called"] = "tool_called"
    agent_id: str
    tool_name: str
    args_digest: str  # sha256 hex of canonical args JSON
    args_size_bytes: int
    duration_ms: int
    status: Literal["success", "error"]
    result_size_bytes: int | None = None
    error_category: str | None = None


# ---- Context fetches ------------------------------------------------------


class ContextFetched(_EventBase):
    """An agent's context was loaded from somewhere.

    Distinguishes auto-injection (orchestrator pre-loaded) from
    pull (agent fetched mid-work) from always-injected (universal,
    e.g. cross-cutting policies).
    """

    kind: Literal["context_fetched"] = "context_fetched"
    agent_id: str
    fetch_kind: Literal["always_injected", "auto_injected", "pulled"]
    source_uri: str
    size_bytes: int
    reason: str | None = None  # for pulled: agent's stated reason category


# ---- Review events --------------------------------------------------------


class ReviewCommentPosted(_EventBase):
    """A reviewer agent posted a structured comment.

    See ``docs/pm-workflow/design.md`` for the comment schema. The
    event captures the structured handles; the full prose / suggested
    diff lives in the comment record itself, addressable by
    ``comment_id``.
    """

    kind: Literal["review_comment_posted"] = "review_comment_posted"
    reviewer_id: str
    reviewer_role: str  # contract_compliance | cross_cutting_policy | ...
    ticket_id: str
    comment_id: str
    comment_type: str  # contract_violation | pattern_divergence | ...
    severity: Literal["critical", "important", "notable"]
    confidence: float  # 1.0 for mechanical; <1.0 for judgment
    contract_uri: str | None = None
    file: str | None = None
    line: int | None = None
    suggested_diff_provided: bool = False


class ReviewCommentResolved(_EventBase):
    """A review comment reached a terminal state."""

    kind: Literal["review_comment_resolved"] = "review_comment_resolved"
    comment_id: str
    resolution: Literal[
        "fixed",
        "auto_applied",
        "deferred",
        "overridden_by_operator",
        "withdrawn_by_reviewer",
    ]


class PerCommitCheckFailed(_EventBase):
    """A per-commit mechanical reviewer flagged a contract violation.

    Distinct from ``ReviewCommentPosted`` because per-commit checks
    run at a different cadence (every commit, not end-of-ticket) and
    only ever fire mechanical reviewers (contract-compliance,
    cross-cutting-policy, spec-compliance against integration AC).
    Per-commit failure rate is a tier-calibration signal independent
    of end-of-ticket review noise.

    Per-commit *passes* don't emit events — only failures, to keep
    the volume tractable.
    """

    kind: Literal["per_commit_check_failed"] = "per_commit_check_failed"
    ticket_id: str
    agent_id: str
    commit_sha: str
    reviewer_role: Literal[
        "contract_compliance",
        "cross_cutting_policy",
        "spec_compliance",
    ]
    violation_category: str  # short structured tag (ownership, schema_mismatch, missing_emit, ...)
    contract_uri: str | None = None  # for contract-compliance violations
    severity: Literal["critical", "important", "notable"]
    auto_applied: bool  # True if a confidence-1.0 fix landed automatically


class BugDiscoveredPostMerge(_EventBase):
    """A bug surfaced in already-merged code, after the federated reviewer passed.

    Fires when a tracer-bullet integration test, dependent ticket, or
    operator flag reveals a bug in code the reviewer federation
    approved. The pointer back to the originating ticket and the
    reviewer set that ran tells us what review missed.

    Captured from v2 day one specifically because the
    adversarial-pairing design (deferred to v2.x — see
    ``docs/agent-leverage/problem.md``) needs this corpus to know
    which failure modes the skeptic should target. Without this
    event, we can't measure escape rate; designing the skeptic
    speculatively without it risks building for failure modes that
    don't matter.
    """

    kind: Literal["bug_discovered_post_merge"] = "bug_discovered_post_merge"
    originating_ticket_id: str
    originating_commit_sha: str | None = None
    discovered_by: Literal[
        "tracer_bullet_integration",
        "dependent_ticket",
        "operator_flag",
        "post_merge_test",
        "production_use",
    ]
    discovered_at_ticket_id: str | None = None  # the ticket that surfaced it, if applicable
    failure_category: Literal[
        "structural",  # better contracts would have caught (SA design problem)
        "semantic",  # tests pass + contracts hold but output is wrong (skeptic candidate)
        "novel",  # genuinely surprising; nothing reasonable would catch
    ]
    reviewer_set_at_merge: list[str]  # which reviewers ran on the originating ticket
    severity: Literal["critical", "important", "notable"]


class BoundedFixLoopExhausted(_EventBase):
    """A ticket hit the 3-cycle review→fix cap and escalated.

    Fires when bounded-fix-loops cap is reached; carries the
    structured trace of what kept getting flagged and why the dev
    agent couldn't address it. Captured from v2 day one for the same
    reason as BugDiscoveredPostMerge — the adversarial-pairing
    design needs the corpus to characterize where review-loop
    convergence breaks down.
    """

    kind: Literal["bounded_fix_loop_exhausted"] = "bounded_fix_loop_exhausted"
    ticket_id: str
    cycles_attempted: int  # always >= 3 at trigger; field captured for future cap changes
    recurring_comment_categories: list[str]  # which comment types kept flagging
    reviewer_roles_involved: list[str]  # which reviewer agents kept finding things
    dev_agent_stated_reason: str | None = None  # short structured reason category
    escalation_outcome: Literal[
        "operator_overrode",  # operator declared "this is fine"
        "contract_amended",  # SA changed the contract
        "ticket_resplit",  # Planner PM resplit
        "still_open",  # operator action pending
    ]


# ---- Escalations ----------------------------------------------------------


class EscalationRouted(_EventBase):
    """A dev or reviewer agent escalated up the chain."""

    kind: Literal["escalation_routed"] = "escalation_routed"
    from_agent: str
    to_target: Literal["sa", "operator", "planner_pm"]
    reason_category: str  # contract_gap | risk_materialized | scope_question | ...
    ticket_id: str | None = None


class AutoEscalationTriggered(_EventBase):
    """The Coordinator PM force-escalated a dev agent on an auto-threshold trip.

    Distinct from ``EscalationRouted`` because the dev agent didn't ask
    to be escalated — the Coordinator's auto-thresholds tripped and
    pulled them. Captures which threshold tripped and the metric value
    so calibration can detect false-escalation patterns and tune the
    thresholds. See ``docs/pm-workflow/design.md`` "Auto-escalation
    thresholds" section.
    """

    kind: Literal["auto_escalation_triggered"] = "auto_escalation_triggered"
    ticket_id: str
    agent_id: str
    from_tier: Literal["standard", "senior", "sa"]
    to_tier: Literal["senior", "sa", "operator"]  # operator when already at sa tier
    trip_signal: Literal[
        "repeated_same_failure",  # 3+ consecutive PerCommitCheckFailed on same contract URI
        "tool_call_flailing",  # success rate < 50% over last 10 calls
        "no_commit_drift",  # no commit in last 30 turns
        "out_of_budget",  # turns > 2x tier-expected envelope for the ticket S/M/L
        "forced_reflection_no_progress",  # agent reported "no progress" twice in a row
    ]
    trip_metric_value: float | None = None  # e.g. success_rate=0.4 for tool_call_flailing
    turns_at_trip: int


class EscalationResolved(_EventBase):
    """An escalation reached resolution."""

    kind: Literal["escalation_resolved"] = "escalation_resolved"
    escalation_event_id: str  # references the EscalationRouted event id
    resolution_kind: Literal[
        "contract_amended",
        "operator_decided",
        "ticket_resplit",
        "withdrawn",
    ]
    duration_ms: int


# ---- Contract events ------------------------------------------------------


class ContractAmended(_EventBase):
    """An SA contract was modified."""

    kind: Literal["contract_amended"] = "contract_amended"
    contract_uri: str
    from_revision: int
    to_revision: int
    source: Literal[
        "sa_initial_pass",
        "sa_delta_from_po",
        "sa_delta_from_spike",
        "sa_delta_from_gap",
        "operator_manual",
    ]
    operator_confirmed: bool
    breaking_change: bool


class ContractViolationDetected(_EventBase):
    """A reviewer agent detected a violation of a contract on a PR."""

    kind: Literal["contract_violation_detected"] = "contract_violation_detected"
    contract_uri: str
    contract_revision: int
    ticket_id: str
    detector: str  # reviewer agent id
    violation_category: str  # ownership | schema_mismatch | missing_emit | ...


# ---- Risk events ----------------------------------------------------------


_RiskStatus = Literal[
    "open",
    "spike_proposed",
    "spike_in_progress",
    "mitigated",
    "accepted",
    "confirmed_impossible",
]


class RiskStatusChanged(_EventBase):
    """A risk in the architecture register changed status."""

    kind: Literal["risk_status_changed"] = "risk_status_changed"
    risk_id: str
    from_status: _RiskStatus | None = None  # None on initial logging
    to_status: _RiskStatus
    spike_ticket_id: str | None = None
    operator_confirmed: bool


# ---- Plan events ----------------------------------------------------------


class PlanRevised(_EventBase):
    """The build plan was updated."""

    kind: Literal["plan_revised"] = "plan_revised"
    from_revision: int | None = None
    to_revision: int
    trigger: Literal[
        "initial",
        "po_delta",
        "sa_delta",
        "ticket_gap",
        "risk_materialized",
        "operator_initiated",
    ]
    summary_category: str  # short structured tag, not prose


class LayerStatusChanged(_EventBase):
    """A bones / mvp / final layer of an epic changed status."""

    kind: Literal["layer_status_changed"] = "layer_status_changed"
    epic_id: str
    layer: Literal["bones", "mvp", "final"]
    from_status: Literal["not_started", "in_progress", "done"] | None = None
    to_status: Literal["not_started", "in_progress", "done"]


class BonesPromotedIncomplete(_EventBase):
    """Operator promoted MVP work on epics while at least one epic's bones is still running.

    Strict bones-first ordering is the default; this event fires when
    the operator overrides via ``/plan unblock`` (with or without an
    SA ``cascade_risk_low: true`` flag suggesting it's safe). Captured
    so the consequences are visible later if the still-running bones
    forces a contract change that affects already-built MVP work.
    See ``docs/pm-workflow/design.md`` "Bones-first ordering" section.
    """

    kind: Literal["bones_promoted_incomplete"] = "bones_promoted_incomplete"
    promoted_epic_ids: list[str]  # the epics whose MVP is being unblocked
    still_running_bones_epic_ids: list[str]  # the epics whose bones is still in flight
    sa_marked_cascade_risk_low: list[str]  # subset of still-running flagged by SA as low risk
    operator_rationale_category: str | None = None  # short tag if operator provided one


class EstimationCalibrationUpdated(_EventBase):
    """The analytics layer recomputed per-tier per-S/M/L estimation bands.

    Fires periodically (every N completed tickets per project) so
    Planner PM can read the latest calibration when sizing new tickets.
    Bands derived from observed turn / tool-call distributions on
    completed tickets; tokens used for cost forecasting only.
    See ``docs/pm-workflow/design.md`` "Estimation calibration" section.
    """

    kind: Literal["estimation_calibration_updated"] = "estimation_calibration_updated"
    sample_size: int  # how many completed tickets fed this recalibration
    bands: dict[str, dict[str, dict[str, list[float]]]]
    # Shape: {tier: {S/M/L: {turns: [low, high], tool_calls: [low, high]}}}
    # Captured as nested dict because the band structure is uniform across tiers.


# ---- Operator actions -----------------------------------------------------


class OperatorOverride(_EventBase):
    """Operator overrode an agent decision.

    Highest-signal category — every override is a vote about agent
    judgment and a candidate input to the (deferred) heuristics
    layer. Capture richly.
    """

    kind: Literal["operator_override"] = "operator_override"
    override_category: str  # tier_promoted | contract_rejected | ...
    target_uri: str  # what was overridden
    from_value: str | None = None
    to_value: str | None = None
    reason_category: str | None = None  # short tag if operator provided one


class OperatorGateConfirmed(_EventBase):
    """Operator confirmed (or rejected) a workflow gate."""

    kind: Literal["operator_gate_confirmed"] = "operator_gate_confirmed"
    gate: str  # po_done | sa_done | plan_confirmed | bones_done | ...
    confirmed: bool


# ---- Dev environment events -----------------------------------------------
#
# Per docs/dev-environment/design.md: the orchestrator's agent spawn
# lifecycle gains RESOLVE → PROVISION → HEALTH-CHECK → CLEANUP steps.
# Each provisioned namespace (per service per agent) emits one event
# at provision time and one at cleanup time. Orphan detection emits
# its own event when the periodic sweep finds resources without an
# active agent.


class DevEnvironmentProvisioned(_EventBase):
    """A service namespace was provisioned for an agent at spawn time.

    Fires once per service per agent — an agent that uses Postgres +
    NATS + Redis will emit three of these events at spawn.
    """

    kind: Literal["dev_environment_provisioned"] = "dev_environment_provisioned"
    agent_id: str
    ticket_id: str
    service_id: str  # the data_store id from architecture.yaml
    strategy: Literal["shared_with_namespace", "per_agent_ephemeral", "operator_supplied"]
    namespace: str  # the actual namespace name (schema, prefix, etc.)
    setup_duration_ms: int  # time from create-namespace start to health-check pass


class DevEnvironmentProvisioningFailed(_EventBase):
    """Provisioning a service namespace failed at spawn time.

    Either the namespace creation failed, or the post-create
    health-check failed. Either way the agent never starts; this is
    the operator-visible failure mode.
    """

    kind: Literal["dev_environment_provisioning_failed"] = "dev_environment_provisioning_failed"
    agent_id: str
    ticket_id: str
    service_id: str
    strategy: Literal["shared_with_namespace", "per_agent_ephemeral", "operator_supplied"]
    failure_phase: Literal["create", "health_check", "seed", "connection"]
    error_category: str  # short structured category; full error in agent logs


class DevEnvironmentCleaned(_EventBase):
    """A service namespace was cleaned up at agent completion.

    Disposition reflects the cleanup_on_success / cleanup_on_failure
    rule from the dev_provisioning block. Archive disposition keeps
    the namespace for operator inspection; drop is destructive.
    """

    kind: Literal["dev_environment_cleaned"] = "dev_environment_cleaned"
    agent_id: str
    ticket_id: str
    service_id: str
    namespace: str
    disposition: Literal["dropped", "archived", "kept"]
    archive_path: str | None = None  # for archived namespaces


class DevEnvironmentOrphanDetected(_EventBase):
    """The periodic orphan sweep found a namespace with no active agent.

    Either an agent crashed before cleanup fired, or cleanup itself
    failed silently. Emit-and-let-operator-decide; the sweep does NOT
    auto-clean orphans (destructive default = ask).
    """

    kind: Literal["dev_environment_orphan_detected"] = "dev_environment_orphan_detected"
    service_id: str
    namespace: str
    age_seconds: int
    last_associated_ticket_id: str | None = None  # if recoverable from naming convention


# ---- Visual Design events -------------------------------------------------
#
# Per docs/visual-design/design.md: VD authors wireframes (one SVG per
# screen) and optionally a design system. Per-screen + per-system events
# capture VD work; visual_compliance reviewer comments fire as ordinary
# review events.


class WireframeAdded(_EventBase):
    """A new wireframe SVG was authored for a screen."""

    kind: Literal["wireframe_added"] = "wireframe_added"
    screen_id: str
    suite_id: str | None = None
    journey_ids: list[str] = []
    capability_ids: list[str] = []
    revision: int  # starts at 1; incremented by WireframeRevised


class WireframeRevised(_EventBase):
    """An existing wireframe was updated."""

    kind: Literal["wireframe_revised"] = "wireframe_revised"
    screen_id: str
    from_revision: int
    to_revision: int
    trigger: Literal[
        "operator_feedback",
        "po_journey_change",
        "dev_gap_report",
        "operator_manual",
    ]


class WireframeApproved(_EventBase):
    """Operator approved a wireframe at a particular revision."""

    kind: Literal["wireframe_approved"] = "wireframe_approved"
    screen_id: str
    revision: int


class DesignSystemImported(_EventBase):
    """The design system (tokens / components / brand) was imported.

    Source identifies where it came from — Anthropic-provided design
    tooling, operator-supplied export, or VD defaults.
    """

    kind: Literal["design_system_imported"] = "design_system_imported"
    source: Literal["claude_design", "operator_supplied", "default"]
    token_count: int
    component_count: int
    revision: int


class VisualComplianceFailed(_EventBase):
    """Visual reviewer flagged divergence between implementation and wireframe.

    Emitted in addition to the generic ReviewCommentPosted so analytics
    can slice visual divergence specifically. The corresponding
    structured comment (with severity, suggested fix, etc.) is captured
    via the review event stream.
    """

    kind: Literal["visual_compliance_failed"] = "visual_compliance_failed"
    ticket_id: str
    screen_id: str
    divergence_category: Literal[
        "layout",
        "component_misuse",
        "token_violation",
        "state_coverage_gap",
        "accessibility",
    ]
    severity: Literal["critical", "important", "notable"]


# ---- discriminated union --------------------------------------------------


AnalyticsEvent = Annotated[
    Union[
        TicketStateChanged,
        AgentSpawned,
        AgentCompleted,
        ToolCalled,
        ContextFetched,
        ReviewCommentPosted,
        ReviewCommentResolved,
        PerCommitCheckFailed,
        BugDiscoveredPostMerge,
        BoundedFixLoopExhausted,
        EscalationRouted,
        AutoEscalationTriggered,
        EscalationResolved,
        ContractAmended,
        ContractViolationDetected,
        RiskStatusChanged,
        PlanRevised,
        LayerStatusChanged,
        BonesPromotedIncomplete,
        EstimationCalibrationUpdated,
        OperatorOverride,
        OperatorGateConfirmed,
        DevEnvironmentProvisioned,
        DevEnvironmentProvisioningFailed,
        DevEnvironmentCleaned,
        DevEnvironmentOrphanDetected,
        WireframeAdded,
        WireframeRevised,
        WireframeApproved,
        DesignSystemImported,
        VisualComplianceFailed,
    ],
    Field(discriminator="kind"),
]


# Pydantic needs a concrete wrapper for discriminated-union
# validation from raw dicts at the top level (the persistence
# layer passes a dict to ``model_validate``). Mirrors the pattern
# in ``jig/thread.py``.
class _AnalyticsEventWrapper(BaseModel):
    event: AnalyticsEvent


def parse_event(raw: dict) -> AnalyticsEvent:
    """Parse a raw JSONL record into a typed AnalyticsEvent.

    Raises ``pydantic.ValidationError`` on schema mismatch — the
    persistence layer is the only caller and will propagate the
    error so we hear about corrupt rows.
    """
    return _AnalyticsEventWrapper.model_validate({"event": raw}).event
