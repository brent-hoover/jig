---
title: Analytics — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-01
---

# Analytics — Problem Statement

## Context

Jig is a multi-agent orchestrator that produces a lot of operational signal as it works: agent spawns, tool calls,
review comments, escalations, contract amendments, ticket state transitions, gate confirmations, operator overrides.
Today most of these are emitted as bus events but with inconsistent structure and granularity, and there's no deliberate
design for what gets captured, in what shape, with what fields.

This is a problem with an asymmetric cost profile: **capture decisions can't be retrofitted.** Every event we don't emit
in v2 is a permanent gap in the historical record. Consumers (dashboards, calibration loops, retrospective
summarization, the eventual heuristics layer) can be built later from data already captured; they cannot be built from
data we forgot to record.

## Problem

Analytics infrastructure has two halves with very different urgency:

**The capture half** is urgent and v2 scope. Without it, every later analysis is bottlenecked on retrofitting telemetry
into running systems and waiting months for new data. Schema choices we make now compound for the life of every project
running on jig.

**The consumer half** (dashboards, alerts, ML-driven recommendations, predictive estimation) is not urgent. It can wait
until we have a corpus to study. Designing consumers now risks building for analyses we imagine matter rather than ones
we discover do.

The trap is conflating these halves and either:
- Designing rich consumers now → wasted effort if the analyses we imagined don't pan out, or
- Deferring all of it → losing irreplaceable historical data.

The right move is asymmetric: capture richly now, consume sparingly now, expand consumers as the corpus matures.

## Constraints

- **Append-only event stream.** Same shape as the existing `.jig/store/events.jsonl` (and bus). No mutation of past
  events; corrections come as new events that supersede.
- **Async writes.** Capture must not block agent operations. Latency on the hot path is non-negotiable.
- **Fine-grained for everything.** Every operation worth observing emits a structured event — including ones that feel
  routine. (See "Granularity" below for the rationale.)
- **Schema discipline as a first-class API surface.** Every feature that emits events declares its event schema in code,
  validated through Pydantic, versioned. Adding a new event type or changing an existing one is a deliberate change, not
  an afterthought.
- **Privacy-aware capture.** Fine-grained does NOT mean capture-everything-verbatim. Structured metadata is rich; full
  prompt/response content is linked from elsewhere. Secrets and PII never enter the event stream.

## Granularity — locked: fine for everything

The choice between coarse (ticket-level only) and fine (per-comment, per-tool-call, per-context-fetch) is locked toward
fine. Rationale: **you never know when data from a routine operation is critical to understanding a downstream one.** A
ticket that fails at review may be explained by a context-fetch that returned an unexpected contract revision two hours
earlier; that connection is impossible to make if context-fetches weren't recorded.

The cost of fine-grained capture is storage volume and schema-discipline burden. Storage is cheap; schema discipline is
real engineering work but pays off in tenet-4 alignment (structured events read as contracts, not narrative). The cost
of coarse capture is permanent loss of resolution.

Fine here means: capture the metadata around every operation *structurally*. It does NOT mean embed the full content of
every prompt and response. Content links to other artifacts (tickets, comments, contracts) by URI; the event captures
the *structured handles* on what happened.

## What's worth capturing — categories

| Category | Granularity | Examples |
|---|---|---|
| **State transitions** | Per-transition | Ticket created / estimated / dispatched / reviewed / merged. Plan layer status changes. Gate confirmations. |
| **Agent lifecycle** | Per-spawn + per-completion | Role, model, tier, ticket/context, tokens in/out, duration, status, parent agent (if any). |
| **Tool calls** | Per-call | Agent, tool name, arguments digest, result status, duration. (Arguments stored as digest/hash; full content linked.) |
| **Context fetches** | Per-fetch | Agent, what was fetched (URI), why (auto-injected vs pull), result size. |
| **Review events** | Per-comment (end-of-ticket) + per-commit-failure + per-post-merge-bug + per-fix-loop-exhaustion | End-of-ticket: reviewer agent, ticket, file/line, type, severity, contract_uri, confidence, accepted/rejected, auto-applied. Per-commit: only emitted on failure (pass would dominate volume) — ticket, agent, commit_sha, reviewer_role (mechanical only: contract_compliance / cross_cutting_policy / spec_compliance), violation_category, severity, auto_applied flag. Two cadences are tracked separately because per-commit-failure rate is a tier-calibration signal independent of end-of-ticket noise. Plus two corpus-building events captured from v2 day one for the deferred adversarial-pairing design (`docs/agent-leverage/`): `BugDiscoveredPostMerge` (originating ticket, reviewer set at merge, failure category: structural / semantic / novel) and `BoundedFixLoopExhausted` (cycles attempted, recurring comment categories, reviewer roles involved, escalation outcome). |
| **Escalations** | Per-escalation + per-auto-trigger | Voluntary: from-agent, to-target, reason category, ticket, resolution. Auto-triggered (Coordinator force-escalation): trip signal (repeated_same_failure / tool_call_flailing / no_commit_drift / out_of_budget / forced_reflection_no_progress), trip metric value, turns at trip, from/to tier. Auto-triggers tracked separately so calibration can detect false-escalation patterns. |
| **Contract events** | Per-amendment + per-violation-detected | Contract URI, old/new revision, source (ticket/spike/SA-delta), operator confirmed. Violations: contract URI, ticket, severity, resolution. |
| **Risk events** | Per-status-change | Risk id, transition (logged → spike-proposed → mitigated/accepted/impossible), spike ticket if any, operator confirmed. |
| **Plan events** | Per-revision + per-layer-transition + per-bones-promotion + per-calibration-update | Build plan revision (revision number, trigger, summary). Layer status changes (epic / layer / from / to). Bones-promoted-incomplete (operator override of strict bones-first; promoted epics + still-running bones + SA `cascade_risk_low` flagged subset). Estimation calibration updated (sample size, per-tier per-S/M/L band recomputation). See `docs/pm-workflow/design.md`. |
| **Operator actions** | Per-action | Override (with from→to), manual edit, gate confirmation/rejection, retro initiation. The most signal-rich category — every override is a vote about agent judgment. |
| **Dev environment events** | Per-service-per-agent + per-orphan | Provisioned (service, namespace, strategy, setup duration), provisioning failed (failure phase, error category), cleaned (disposition: dropped/archived/kept), orphan detected (age, last associated ticket). See `docs/dev-environment/`. |
| **Visual design events** | Per-wireframe + per-design-system + per-visual-compliance-flag | Wireframe added/revised/approved (screen id, suite, journey/capability ids, revision); design system imported (source: claude_design / operator_supplied / default; token + component counts); visual_compliance failed (divergence category: layout / component_misuse / token_violation / state_coverage_gap / accessibility, severity). See `docs/visual-design/`. |

## What we'd consume this for (eventually — NOT v2)

- **Tier calibration** — under-tiered (always escalate) vs over-tiered (never flag) reviewers. Same for dev tiers.
- **Bones-first validation** — does it actually reduce later integration churn? Need before/after captured to know.
- **Cost-per-tier reporting** — validate that tiering saves money. If 80% of tickets ended up SA-tier, the design
  failed.
- **Re-plan trigger surfacing** — Coordinator PM detecting patterns ("tickets in module X always escalate"). Some
  v2-relevant; richer analysis is later.
- **Operator-override telemetry** — every override is gold for tuning. SA over-tiered? Reviewer too noisy? Contract too
  strict?
- **Estimation calibration** — historical S/M/L vs actual cycles → tighter estimates over time.
- **Retrospective input** — milestone retros consume metrics rather than reading raw history.
- **Heuristics mining** — recurring reviewer rationales become candidate heuristics (see `docs/learnings/`).
- **Cross-project tool improvement** — patterns across projects suggest changes to jig defaults, role prompts,
  templates.

## What's in scope for v2

**Required:**

- Event schema covering every category above, declared in Pydantic, versioned (`event_version: 1`).
- Append-only persistent stream — likely an extension of `.jig/store/events.jsonl` with discriminated event types.
- Async write path with backpressure handling so agent operations are never blocked by analytics capture.
- Privacy filter: secrets and PII never written; content references stored as URIs to other artifacts (tickets,
  comments, contracts), not embedded.
- Every existing and new feature in v2 declares the events it emits. Adding events without schema entries is a
  development-time error.

**Lightweight consumer (only what Coordinator PM already needs):**

- Pattern detection for stalled tickets (already in PM workflow doc).
- Re-plan triggers based on escalation frequency, contract amendment frequency, bones-non-convergence.

**Out of v2:**

- Dashboards / TUI views beyond what's already designed.
- Cost reports.
- Predictive estimation models.
- Cross-project aggregation.
- Anything machine-learning-based.

## Privacy and retention — design considerations

**Privacy:**
- No prompt content, response content, or tool argument content stored verbatim in the event stream. Use URI references
  to artifacts that already store those (with their own privacy controls).
- No secrets / credentials / PII. Hard rule. Filter at the emit boundary.
- Per-project events stay in `.jig/`. No automatic off-machine transmission. Cross-project aggregation, when designed,
  is opt-in only.

**Retention:**
- Append-only with rotation. Events don't get deleted; older logs get rotated and compressed.
- Never auto-purge — historical data is the asset. If storage becomes a real concern (it won't in v2), design a
  cold-storage tier.

**Schema evolution:**
- `event_version` on every event from day one.
- Schema changes are additive when possible (new optional fields). Breaking changes require version bump and migration
  logic in consumers.

## Risks

- **Schema discipline slips.** New features land without declaring their events; the corpus develops blind spots.
  Mitigation: treat the event schema as a code-review blocking concern, same as type signatures.
- **Privacy filter has gaps.** Sensitive data sneaks into the stream. Mitigation: explicit allowlist of fields per event
  type, not denylist; periodic audit.
- **Storage growth becomes painful.** Fine-grained for everything could produce GB-per-project on large projects.
  Mitigation: rotation + compression as baseline; cold-storage tier when needed; never auto-purge.
- **Consumers built later can't interpret old events.** Schema evolution discipline (additive changes; version bumps;
  migration logic) prevents this. Active enforcement needed.
- **Operator overrides not captured richly enough.** This is the highest-signal category and the most likely to be
  under-instrumented. Mitigation: every TUI action that represents an operator decision emits an event with enough
  context to reconstruct what was overridden and why.

## When to revisit (consumer half)

Trigger: **after 1-2 medium-sized projects shipped and a clear question that the existing data could answer but no tool
exists to answer it.** Specific likely triggers:
- "Are we tiering correctly?" (need cost + escalation cross-tabulated by tier)
- "Is bones-first paying off?" (need integration-churn metrics across pre/post)
- "Where is the operator spending their override budget?" (need override telemetry sliced by category)

Each of those becomes a candidate consumer design with real data behind it.

## Implementation status (v2 in progress)

**Landed (`jig/analytics/`):**

- `events.py` — discriminated union `AnalyticsEvent` covering all 10 categories above. 16 concrete event types: ticket
  state changes, agent lifecycle (spawn / complete), tool calls, context fetches, review comments (posted / resolved),
  escalations (routed / resolved), contract events (amended / violation detected), risk status changes, plan revisions,
  layer status changes, operator overrides, gate confirmations. Privacy enforced at schema-design time (no event has
  fields capable of carrying prose, prompts, or content).
- `store.py` — `AnalyticsStore` wrapping `Collection` directly (mirrors `ThreadStore` pattern); persists to
  `.jig/store/events.jsonl`; indexed by `kind`; provides `all`, `by_kind`, `by_correlation` reads.
- `emitter.py` — `EventEmitter` with both `await emit()` and fire-and-forget `emit_nowait()`; tracks pending writes with
  a `drain()` for shutdown so the tail of the stream isn't lost on loop close. Capture failures log but never propagate.
- Round-trip tested: schema serializes through Pydantic alias path, reloads through discriminator, persists and reads
  back through the store, both emit modes work.

**Still owed for v2:**

- **Wiring emission throughout the codebase** — bus message emit sites in `orchestrator.py`, `init_workflow.py`,
  `check_runner.py`, `handoff_gate.py`, `init_mcp.py`, `ws_server.py`, etc. need to also emit typed analytics events.
  Some can replace bus messages; some need both.
- **Daemon-side wiring** — orchestrator holds the `EventEmitter`; agents and MCP servers reach it through
  `AgentSpawnContext`-equivalent.
- **Tool-call interception** — `agent.py` needs to emit `ToolCalled` events for every MCP invocation. Currently not
  emitted at all.
- **Context-fetch interception** — context loading in `prompt_builder.py` and the agent runtime needs to emit
  `ContextFetched`. Currently not emitted at all.
- **Operator action interception** — every TUI action representing an operator decision needs to emit `OperatorOverride`
  or `OperatorGateConfirmed`. Highest-signal category; most likely to be under-instrumented.
- **Pytest coverage** — smoke test passed; proper test suite with concurrent emit, drain semantics, schema validation
  errors on bad input.
- **Daemon shutdown integration** — `await emitter.drain()` needs to be in the daemon shutdown path.

**Cross-cutting decisions made during implementation:**

- Event taxonomy uses **flat `kind` discriminators** with underscore-separated names (`ticket_state_changed`,
  `agent_spawned`), mirroring the thread.py union pattern rather than introducing a new convention.
- Used `AnalyticsStore` wrapping `Collection` directly (same as `ThreadStore`) rather than `TypedCollection` because
  `TypedCollection` is fixed to a single concrete model and can't validate a discriminated union.
- `correlation_id` and `parent_event_id` are first-class on every event — necessary for tracing causally-nested
  operations through the stream.
- Privacy filter is **schema-design discipline**, not runtime filtering. The `AnalyticsEvent` types simply don't have
  fields that could carry sensitive content. Adding such a field requires a schema change → code review → caught before
  merge.

## Change log

- 2026-05-01: Initial capture (brent + claude). Locks fine-grained capture for every category; commits v2 to schema +
  persistence + async + privacy filter; defers all rich consumers; specifies categories and the eventual consumer
  use-cases as guideposts.
- 2026-05-01: v2 schema + persistence + emitter landed in `jig/analytics/`. Wiring emission throughout the rest of the
  codebase is the remaining v2 work.
- 2026-05-01: Added Dev environment events category (4 new event types: `DevEnvironmentProvisioned`,
  `DevEnvironmentProvisioningFailed`, `DevEnvironmentCleaned`, `DevEnvironmentOrphanDetected`) per
  `docs/dev-environment/design.md`. Per-service-per-agent granularity for provision/cleanup, per-orphan for the periodic
  sweep. Required for mechanically detecting orphan accumulation and provisioning failures.
- 2026-05-01: Added Visual design events category (5 new event types: `WireframeAdded`, `WireframeRevised`,
  `WireframeApproved`, `DesignSystemImported`, `VisualComplianceFailed`) per `docs/visual-design/design.md`.
  Per-wireframe + per-design-system + per-visual-compliance-flag granularity. `VisualComplianceFailed` complements the
  generic `ReviewCommentPosted` so analytics can slice visual divergence specifically.
- 2026-05-03: Added `PerCommitCheckFailed` event for the per-commit cadence of the two-cadence reviewer model (resolved
  during the SA Q4 working session — see `docs/sa-architecture/design.md` and `docs/pm-workflow/design.md`). Distinct
  from `ReviewCommentPosted` because per-commit checks fire at a different cadence (every commit, not end-of-ticket) and
  only run mechanical reviewers (contract_compliance, cross_cutting_policy, spec_compliance). Pass cases don't emit
  events to keep volume tractable; only failures. Per-commit-failure rate is a tier-calibration signal independent of
  end-of-ticket review noise.
- 2026-05-03: Added 3 PM-workflow event types from the open-questions resolution session (see
  `docs/pm-workflow/design.md`): `AutoEscalationTriggered` (Coordinator forced-escalation with trip signal + metric
  value), `BonesPromotedIncomplete` (operator override of strict bones-first ordering), and
  `EstimationCalibrationUpdated` (analytics layer recomputed per-tier per-S/M/L bands; Planner reads on next pass).
  Total event types: 30. Updated Escalations row to reflect both voluntary and auto-triggered shapes; updated Plan
  events row to cover layer transitions, bones promotions, and calibration updates.
- 2026-05-03: Added 2 corpus-building event types from the agent-leverage v2/v2.x split (see
  `docs/agent-leverage/problem.md`): `BugDiscoveredPostMerge` (post-merge bug surfaces; carries originating ticket
  + reviewer set + failure category) and `BoundedFixLoopExhausted` (3-cycle review→fix cap reached; carries
recurring comment categories + reviewer roles involved + escalation outcome). Both captured from v2 day one even though
the consumer (adversarial-pairing design) is deferred to v2.x — without these events, the v2.x design would wait an
additional corpus-accumulation cycle. Cheap to add now; expensive to retrofit. Total event types:
  32. Updated Review events row to reflect the two new shapes.
- 2026-05-03: Added `simulator: bool` field to the event base class for the synthetic-operator simulator (see
  `docs/synthetic-operator/design.md`). EventEmitter respects `JIG_SIMULATOR=true` env var or `simulator_mode=True`
  constructor arg and stamps every emitted event accordingly. Consumer queries (analytics views, dashboards, calibration
  loops, retrospective summarization) filter to `simulator=False` by default; simulator events live in their own logical
  corpus so they don't pollute real-project analytics. Tested end-to-end: real / explicit-simulator / env-driven all
  produce correctly-tagged events. No new event types; this is a base-class field addition.
