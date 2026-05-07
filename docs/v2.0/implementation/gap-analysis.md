---
title: Jig v2 Gap Analysis
date: 2026-04-30
---

# Jig v2 Gap Analysis: Current Codebase vs Design Corpus

## Executive Summary

The current jig codebase (v1) is a single-agent-sequential coordinator for bounded, well-scoped tasks. The v2 design targets a multi-agent, multi-level, multi-role system with coherence discipline built in. The gap is structural — not a series of feature additions but a foundational rearchitecture. This document maps every major v2 design area against current code and classifies the work as NEW, EXTEND, REPLACE, LEAVE ALONE, or DELETE.

**Key insight:** ~40% of the current codebase (existing stores, bus, spec infrastructure, TUI frame) is foundation the v2 design builds on. ~60% needs replacement (init workflow, orchestrator, agent dispatch model, ticket schema).

---

## WORKFLOW & AGENT ROLES

### Multi-level PO (L0-L4 spec tiers)

**Current state:** Single-brief workflow in `init_workflow.py` (L0 pitch + flat capability list).

**v2 design requires:**
- L0 (pitch) — 1 sentence + problem + audience
- L1 (discovery) — personas + journeys + capability roster (first-class)
- L2 (suites) — capability groupings
- L3 (suite briefs) — per-suite detail (reuses existing brief format)
- L4 (tickets) — existing

**Artifacts on disk:**
- `project.md` (L0) — [v2 multi-level-spec/design.md L0]
- `discovery.md` (L1) + `discovery.state.yaml` (resume support) + `playbacks/<journey-id>.md` — [v2 multi-level-spec/design.md L1]
- `suites.yaml` (L2) — [v2 multi-level-spec/design.md L2]
- `suites/<s>/brief.md` (L3) — [v2 multi-level-spec/design.md L3]
- `project.structured.yaml` + `suites/<s>/spec.structured.yaml` (federated, v2) — [v2 multi-level-spec/design.md federated-spec-generation]

**Classification: REPLACE**

- `init_workflow.py` lines 1-500+ — rewrites L0+L1+L2 phases, adds journey-walk loop with five-phase pattern (Frame, Elicit, Walk, Probe, Playback) per discovery.md design.
- `init_mcp.py` — loses old brief-edit tools; adds discovery_* tools (discovery_set_intro, discovery_add_journey, discovery_add_capability, discovery_finalize, discovery_set_phase, discovery_set_next_question, discovery_stash_pending_capability, discovery_load_state) + ontology_* tools (ontology_stash_term, ontology_add_term, ontology_get_terms, ontology_lookup).
- Adds new project ontology parser in `jig/spec/` to read/write `.jig/spec/ontology.md` per [v2 multi-level-spec/design.md project-ontology].
- New brief parser for journey blocks (similar to existing brief parser but handles `### Journey` sections).

**Note:** L2 PO (suite organization) is lightweight — roughly same as existing "propose grouping" flow; reuses existing brief-parsing infra for L3.

---

### SA Discovery Loop + Contract Authoring

**Current state:** No SA; all capability refinement lumped into PO brief.

**v2 design requires:**
- `architecture.yaml` (project-level: stores, modules, cross-cutting policies, risk register) — [v2 sa-architecture/design.md architecture.yaml]
- `modules/<m>/contracts.yaml` (per-module: ownership, schemas, APIs, integration AC, open questions) — [v2 sa-architecture/design.md modules/<m>/contracts.yaml]
- `contracts/shared/*.yaml` (URI-anchored shared contract shapes) — [v2 sa-architecture/design.md shared-contracts]
- Risk register + spike workflow — [v2 sa-architecture/design.md risk-identification-and-spikes]
- SA discovery loop (same shape as L1 PO: walk modules, extract contracts, confirm, append) — [v2 sa-architecture/design.md sa-workflow]
- SA checklist enforcing over-specification — [v2 sa-architecture/design.md sa-checklist]

**Classification: NEW**

- New `SA` role in role registry.
- New `jig/sa/` module with:
  - `architecture.py` — `ArchitectureSpec`, `Module`, `Contract`, `RiskRegister` Pydantic models
  - `contracts.py` — `ContractsSpec` per module; validators for URI references
  - `discovery.py` — SA discovery loop, similar pattern to L1 PO
- New MCP tools: `arch_add_module`, `arch_add_contract`, `arch_log_risk`, `arch_propose_spike`, `arch_finalize`
- New state-tracking file: `.jig/arch/architecture.state.yaml` (resume support, like `discovery.state.yaml`)
- Spike ticket type (`type: spike`) — [v2 sa-architecture/design.md spike-tickets]
- Contract amendment cascade on `confirmed_impossible` spike — [v2 sa-architecture/design.md cascade-after-confirmed-impossible-spike]

**URI scheme extensions:** `project://arch/...`, `project://arch/modules/<m>/...`, `project://arch/contracts/shared/...` with sub-contract anchoring (e.g., `project://arch/modules/catalog-ingest/contracts#owns/products/write_access`) — [v2 spec_uri.py].

---

### VD Role (Frontend Architecture + Design System + Wireframes)

**Current state:** No VD; frontend decisions implicitly distributed.

**v2 design requires:**
- `frontend.yaml` — stack choice (default HTMX + Alpine + custom CSS), build tooling, component pattern, accessibility target — [v2 visual-design/design.md frontend-architecture]
- `system/tokens.yaml` — design tokens (always present, defaults applied at VD start) — [v2 visual-design/design.md design-system]
- `system/components.yaml` — component specs
- `wireframes/<screen-id>.html` — semantic HTML wireframes (not SVG); one per screen; state variants via Alpine `x-data` — [v2 visual-design/design.md wireframe-format]
- `wireframes/<screen-id>.notes.md` — interaction notes, state descriptions, cross-references (sidecar)
- `wireframes/screens.yaml` — screen roster mapping screen → journey ids → capability ids
- `wireframes/index.html` — auto-generated browser index of all wireframes (iframes + state toggles)
- `wireframes/wireframe.css` — static utility CSS (hand-written once at VD start, greyscale token values)
- `design/references/` — operator-dropped mood boards / screenshots (read-only, VD uses to bias initial wireframe drafts)

**Classification: NEW**

- New `VD` role in role registry.
- New `jig/vd/` module with:
  - `frontend.py` — `FrontendArchSpec` Pydantic model; stack defaults; operator override path
  - `wireframes.py` — wireframe HTML parsing + linting (deterministic Python module enforcing vocabulary: allowed elements, no inline styles, no real hex colors, required Alpine `x-data`/`x-show`) — [v2 visual-design/design.md wireframe-html-linter]
  - `design_system.py` — tokens/components schema; import paths (Claude Design / operator-supplied / defaults)
  - `discovery.py` — VD discovery loop (foundational decisions first, then iterative wireframe walk)
- New MCP tools: `vd_propose_screen_roster`, `vd_add_wireframe`, `wireframe_lint`, `wireframe_get`, `wireframe_set_notes`, `vd_finalize_wireframes`
- Browser index generator — generate `wireframes/index.html` after each wireframe write
- `/design serve` slash command (local HTTP server for wireframe viewing when `file://` doesn't suffice)

**URI scheme extension:** `project://design/wireframes/<screen-id>`, `project://design/system/tokens`, etc.

---

### Planner PM (Strategic Build Planning)

**Current state:** No PM; work flows ad-hoc through orchestrator.

**v2 design requires:**
- `build-plan.yaml` — living artifact: epics × three completeness layers (bones/MVP/final) with tickets, status, risks_addressed — [v2 pm-workflow/design.md build-plan.yaml]
- Epic decomposition: each epic → tracer bullet (bones) → multiple standard tickets (MVP) → final tickets (final)
- Ticket extensions: `type` (tracer-bullet | standard | spike), `layer`, `dev_tier` (standard | senior | sa), `reviewer_set`, `context_hints`, `risks_addressed`, `done_when` — [v2 pm-workflow/design.md ticket-structure-extensions]
- Tracer bullets: cross-module, single happy path, validates integration — [v2 pm-workflow/design.md tracer-bullets]
- S/M/L estimation with per-tier calibration — [v2 pm-workflow/design.md estimation-calibration]
- Reviewer federation selection logic per ticket characteristics — [v2 pm-workflow/design.md reviewer-federation-selection-logic]

**Classification: NEW**

- New `Planner PM` role (senior or SA tier; runs in passes after SA-done).
- New `jig/pm/` module with:
  - `build_plan.py` — `BuildPlanSpec`, `Epic`, `EpicLayer` Pydantic models
  - `planner.py` — Planner PM discovery loop; walks capabilities → tickets decomposition; confirms tier assignments
  - `estimator.py` — S/M/L heuristics + analytics-driven calibration per tier
- New MCP tools: `plan_add_epic`, `plan_add_ticket`, `plan_set_reviewer_set`, `plan_finalize`, `plan_estimate`
- New state file: `.jig/plan/build-plan.yaml`
- Estimation calibration: emit `EstimationCalibrationUpdated` events; Planner reads on next pass — [v2 analytics/problem.md EstimationCalibrationUpdated]

---

### Coordinator PM (Continuous Tactical Dispatch)

**Current state:** Orchestrator does naive FIFO dispatch; no escalation logic, no stalling awareness.

**v2 design requires:**
- Continuous dispatch: work the build plan bones-first across all epics, then MVP, then final (ordering rule enforced)
- Deterministic operations: ticket-done → dispatch next, bones-complete → unlock MVP, blocked > N hours → alert operator — [v2 pm-workflow/design.md coordinator-deterministic-operations]
- LLM-thin helpers: unstructured escalation classification, cross-ticket pattern detection — [v2 pm-workflow/design.md coordinator-llm-thin-operations]
- Auto-escalation thresholds: trip signals (repeated_same_failure, tool_call_flailing, no_commit_drift, out_of_budget, forced_reflection_no_progress) — [v2 pm-workflow/design.md auto-escalation-thresholds]
- Bounded fix loops: 3-cycle review→fix cap; escalate on exhaustion — [v2 pm-workflow/design.md bounded-fix-loops]
- Layer transition gating: bones all complete before any MVP starts

**Classification: REPLACE (Orchestrator dispatch logic)**

- Rewrite `orchestrator.py` dispatch loop (~150-200 lines) to enforce bones-first ordering, check escalation thresholds, emit auto-escalation triggers.
- New `Coordinator PM` agent (standard tier; mostly deterministic Python with thin LLM helpers).
- New thresholds configuration in `.jig/config.yaml` — escalation trip values per tier.
- Emit `AutoEscalationTriggered` events — [v2 analytics/problem.md AutoEscalationTriggered]

---

### Federated Reviewer Agents (Eight Reviewer Types)

**Current state:** No review infrastructure; agents ship unreviewed.

**v2 design requires:**
- Eight reviewer types: contract-compliance, cross-cutting-policy, spec-compliance, pattern-conformance, error-handling, test-adequacy, architectural-review, security-review, performance, visual-compliance (10 total) — [v2 pm-workflow/design.md reviewer-federation-selection-logic]
- Two-cadence review:
  - **Per-commit mechanical** (every commit): contract-compliance + cross-cutting-policy + spec-compliance only; fire within seconds; deterministic; emit `PerCommitCheckFailed` only on failure — [v2 analytics/problem.md PerCommitCheckFailed]
  - **End-of-ticket federation** (before handoff): all 10 reviewers in parallel; judgment-flavored; emit `ReviewCommentPosted` + `ReviewCommentResolved` — [v2 pm-workflow/design.md two-cadence-review]
- Severity tiers: critical (blocks) | important (should fix unless rework) | notable (can defer to deferred.jsonl) — [v2 pm-workflow/design.md severity-tiers]
- Lead reviewer (thin agent) to deduplicate + resolve conflicts across parallel reviewers
- Structured comments citing contract URIs with sub-contract anchoring — [v2 pm-workflow/design.md reviewer-comments]
- Visual compliance reviewer (senior tier, vision-based diff) for UI tickets — [v2 visual-design/design.md visual-review]

**Classification: NEW**

- New `jig/review/` module with:
  - `reviewers.py` — eight reviewer role configs
  - `review_coordinator.py` — spawns N reviewers in parallel; leads synthesis
  - `contract_compliance.py` — reads contracts + diff; checks ownership, schema, API shape
  - `cross_cutting_policy.py` — checks PII/secrets/audit-on-action/multi-tenant rules
  - `spec_compliance.py` — checks behavior AC + integration AC satisfaction
  - `pattern_conformance.py`, `error_handling.py`, `test_adequacy.py`, `architectural_review.py`, `security_review.py`, `performance.py`, `visual_compliance.py`
- New event types: `ReviewCommentPosted`, `ReviewCommentResolved`, `PerCommitCheckFailed`, `BugDiscoveredPostMerge`, `BoundedFixLoopExhausted` — [v2 analytics/problem.md review-events]

---

## SCHEMA & ARTIFACTS

### Spec Tiers (L0-L4 artifacts)

Already mapped under "Multi-level PO" above. Key schema files:

- `docs/brief.md` — L0 (REPLACE: add problem, audience, product-level non-goals)
- `.jig/spec/discovery.md` — L1 (NEW: personas, journeys, capability roster, journey playbacks)
- `.jig/spec/discovery.state.yaml` — L1 (NEW: resume support)
- `.jig/spec/suites.yaml` — L2 (NEW: suite list with capability assignments)
- `.jig/spec/suites/<s>/brief.md` — L3 (EXTEND: reuse existing brief format, scoped to one suite)
- `.jig/spec/suites/<s>/spec.structured.yaml` — L3 (NEW: federated spec per suite; spec-gen output)
- `.jig/spec/project.structured.yaml` — (NEW: thin federation pointing at suites; replaces v1 monolithic spec)
- `.jig/spec/ontology.md` — (NEW: project's domain vocabulary, operator's words, captured during L1) — [v2 multi-level-spec/design.md project-ontology]

**Files to EXTEND:**
- `jig/brief_parser.py` — add journey block parsing (similar to existing capability block parsing)
- `jig/spec_uri.py` — extend URI scheme for `project://spec/suites/...`, `project://spec/discovery/...`

---

### SA Artifacts

Already mapped under "SA Discovery Loop" above:

- `.jig/arch/architecture.yaml` — NEW
- `.jig/arch/modules/<m>/contracts.yaml` — NEW (per module)
- `.jig/arch/contracts/shared/*.yaml` — NEW (shared contract shapes)
- `.jig/arch/cascades/<risk-id>-<timestamp>.yaml` — NEW (cascade audit trail on spike-confirmed-impossible) — [v2 sa-architecture/design.md cascade-audit-trail]

**Schema files:**
- `jig/sa/architecture.py` — Pydantic models for architecture.yaml, contracts.yaml
- `jig/sa/contracts.py` — contract types (data, interface, ownership, process, event, cross-cutting-policy, external-dependency, integration-AC, behavioral) with validators

---

### VD Artifacts

Already mapped under "VD Role" above:

- `.jig/design/frontend.yaml` — NEW
- `.jig/design/system/tokens.yaml` — NEW (or imported)
- `.jig/design/system/components.yaml` — NEW (or imported)
- `.jig/design/system/brand.md` — NEW (or imported)
- `.jig/design/wireframes/<screen-id>.html` — NEW (per screen)
- `.jig/design/wireframes/<screen-id>.notes.md` — NEW (sidecar)
- `.jig/design/wireframes/screens.yaml` — NEW (roster)
- `.jig/design/wireframes/wireframe.css` — NEW (static utility CSS, generated once)
- `.jig/design/wireframes/index.html` — NEW (auto-generated browser index)
- `.jig/design/references/` — operator-owned (mood boards, screenshots, sketches)

---

### PM Artifacts

Already mapped under "Planner PM" above:

- `.jig/plan/build-plan.yaml` — NEW (living artifact owned by Planner PM)
- `.jig/plan/deferred.jsonl` — NEW (notable-severity items deferred from review) — [v2 pm-workflow/design.md severity-tiers]

**Ticket schema extensions:**
- `type: tracer-bullet | standard | spike`
- `suite_id: <suite-name>`
- `module_id: <module-name>` (future; v2 pre-wires schema but optional)
- `layer: bones | mvp | final`
- `estimate: S | M | L`
- `dev_tier: standard | senior | sa`
- `reviewer_set: [...]` (list of reviewer type names)
- `context_hints: { always_inject, auto_inject_uris, pull_available }`
- `risks_addressed: [...]` (risk ids from architecture.yaml)
- `done_when: <prose description>`

**Files to EXTEND:**
- `jig/ticket.py` — add new fields above
- `jig/store/tickets.py` — add indexing by `type` so `list spikes` / `list tracer-bullets` queries work

---

## INFRASTRUCTURE & ORCHESTRATOR

### Service Isolation via Namespacing

**Current state:** Single agent at a time; no provisioning infrastructure.

**v2 design requires:**
- Per-agent namespace isolation on shared services (Postgres schema, NATS subject prefix, Redis key prefix, S3-compat bucket prefix) — [v2 dev-environment/design.md provisioning-strategies]
- `dev_provisioning` block on every `data_store` + applicable `external_dependency` in `architecture.yaml` — [v2 dev-environment/design.md architecture.yaml-extensions]
- `.jig/dev/manifest.yaml` — derived from `architecture.yaml` at init time; operational view of what services + ports + health checks — [v2 dev-environment/design.md dev-environment-manifest]
- Provisioning hooks: `setup_hooks` (migrations), `seed_hooks` (fixtures), `cleanup_on_success` (drop | archive | keep), `cleanup_on_failure` (archive for debug)
- Fixture recording for external APIs: `recorded_fixtures` strategy with modes (replay_only | record_new | record_overwrite) — [v2 dev-environment/design.md external-api-mocking]

**Classification: NEW (Provisioning) + EXTEND (Agent spawn)**

- New `jig/dev/` module with:
  - `provisioner.py` — reads `architecture.yaml`, creates per-agent namespace/ephemeral instances, injects connection strings via env vars
  - `manifest.py` — derives `.jig/dev/manifest.yaml` from `architecture.yaml`
  - `cleanup.py` — runs cleanup hooks on agent completion; tracks orphans in `.jig/dev/orphans.jsonl`
  - `fixtures.py` — VCR.py-style recorded-fixture recorder for external APIs
- New provisioning step in agent spawn lifecycle: [v2 dev-environment/design.md agent-spawn-lifecycle]
  1. RESOLVE — look up ticket's module → accessed data_stores
  2. PROVISION — create namespaces / ephemeral instances / verify operator_supplied
  3. HEALTH-CHECK — verify connectivity; fail fast if not
  4. (existing: WORKTREE, SPAWN BWRAP, RUN AGENT)
  5. CLEANUP — drop/archive/keep based on success/failure

**Files to EXTEND:**
- `jig/orchestrator.py` — add steps 1-3, 8 to agent spawn
- `jig/agent.py` — pass env-var injection map into bwrap

**New configuration:**
- `architecture.yaml` gets `dev_provisioning` blocks per data_store — already specified above under SA Artifacts

---

### Per-Ticket Reference Resolution (Tier Promotion Support)

**Current state:** Context loaded at agent spawn time; no mid-work tier promotion.

**v2 design requires:**
- Three-tier context injection (per contract consumption design):
  - **Always-injected:** cross-cutting policies (PII, secrets, no-direct-cross-module-db, etc.) — cheap, high cost-of-miss
  - **Auto-injected:** module contracts + shared contracts referenced by capability's integration AC
  - **Pull-on-demand:** anything else via MCP tools
- Mid-work tier promotion: restart agent with new tier, preserve worktree — [v2 pm-workflow/design.md bounded-fix-loops]

**Classification: EXTEND**

- Refactor `jig/context_resolver.py` to implement three-tier model — [v2 sa-architecture/design.md contract-consumption]
- Add MCP tools for pull-on-demand: `get_contract(uri)`, `list_contracts(filter)`, `get_risk(id)`, `list_risks(status)`
- Extend agent spawn to support tier override (from Coordinator PM escalation) — restart same ticket on new tier

---

### Bus & Message Store

**Current state:** `jig/store/bus.py` implements append-only message stream; used for inter-agent communication.

**Classification: LEAVE ALONE**

The bus design is sound and v2-compatible. No changes needed for foundational v2 work. Enhancements (correlation IDs, message filtering by topic) land later.

---

## TUI

### Existing Tab Frame + Composer

**Current state:** Four tabs (Now / Tickets / Spec / Events); Composer in Now tab; footer showing project + daemon state.

**Classification: LEAVE ALONE**

The frame is solid. v2 enhances content within it, not the frame itself.

---

### New Slash Commands

**Current state:** `init`, `help`, `quit` are the main ones; sketches exist for `/spec` and `/ticket`.

**v2 design requires:**

New slash commands — [v2 multi-level-spec/design.md workflow-integration]:
- `/init` — runs L0 + L1 + L2 in sequence (already exists; rewrite phases)
- `/init --resume` — resumes wherever discovery state says
- `/suite list` — show suites.yaml status (pending vs done)
- `/suite init <name>` — L3 simple-brief for that suite
- `/suite refresh <name>` — re-run suite brief incorporating L1 changes
- `/journey add <persona>` — L1 add-journey iteration
- `/journey list` — show personas + journeys

New slash commands for SA / VD / PM:
- `/sa review` — show architecture.yaml + open questions
- `/sa risks` — show risk register
- `/sa open-questions` — show open questions
- `/vd wireframes` — browse wireframes (or `/design browse`)
- `/design serve` — local HTTP server for wireframe viewing
- `/design reimport` — pull latest from Claude Design or operator-supplied
- `/plan list` — show build-plan status
- `/plan estimate <capability>` — view/override S/M/L calibration
- `/plan calibrate` — re-run estimation calibration

**Classification: NEW (command handlers) + EXTEND (TUI dispatch)**

- New command handlers in `jig/tui/commands/` for each command
- Wire into daemon command registry so they're invokable as TUI slash commands + via `jig --print "/<command>"` for scripting

---

### New Rendering Needs

**Classification: NEW (screen updates)**

- Suite status (suite list with L3 brief progress)
- Journey list (personas + journeys)
- Wireframe browser (when `/design serve` runs; opens local URL)
- Contract violations (visual review failures from reviewer federation)
- Build-plan visualization (epics × layers with status)
- Cycle progress (how many turns into current ticket, estimated remaining)
- Escalation alerts (blocked tickets, auto-escalations)
- Operator-override prompts (gates requiring confirmation with structured reason categories) — [v2 TENETS.md tenet-5]

These are mostly enhancements to Spec / Tickets tabs + new modal overlays, not fundamental frame changes.

---

## ANALYTICS

### Event Capture Schema

**Current state:** `jig/analytics/events.py` has 32 event types captured. Core event infrastructure landed: `jig/analytics/store.py`, `jig/analytics/emitter.py`.

**Classification: EXTEND (wiring)**

Events are already defined (see `/Users/brent/Projects/personal/jig/jig/analytics/events.py`). Remaining work is wiring emission throughout the codebase:

- **Wiring sites:**
  - `orchestrator.py` — ticket state transitions
  - `agent.py` — agent spawn / completion / tool calls (tool-call interception is currently absent)
  - `prompt_builder.py` — context fetches (currently absent)
  - `init_workflow.py` — gate confirmations, operator overrides (currently absent)
  - `check_runner.py` — per-commit check failures
  - `mcp_server.py` — all MCP invocations
  - `ws_server.py` — TUI operator actions (overrides, gate confirmations)
  - New review coordinator — reviewer events

- **Files to EXTEND:**
  - `jig/orchestrator.py` (line ~50) — obtain `EventEmitter` from `__init__` params; emit state transitions
  - `jig/agent.py` (line ~200+) — wrap MCP calls to emit `ToolCalled`; track context fetches
  - `jig/prompt_builder.py` (line ~100+) — emit `ContextFetched` on every URI resolution
  - `jig/ws_server.py` — emit `OperatorOverride` / `OperatorGateConfirmed` on TUI actions
  - Daemon shutdown path — `await emitter.drain()` before loop closes

**Note:** Event schema is locked from v1 onward per [v2 analytics/problem.md privacy-and-retention]. No schema changes in v2; only emission wiring.

---

## AGENT LEVERAGE (v2 commitments)

### 1. Intent Layer (problem / simplest_solution / complications_considered)

**Current state:** Spec / contracts use flat `rationale` field.

**v2 design requires:** Disciplined sequence forcing chain of thought — [v2 agent-leverage/problem.md intent-as-artifact]

```yaml
problem: "What's this solving?"
simplest_solution: "Dumb obvious baseline."
complications_considered:
  scale: "<applies? forces what?>"
  concurrency: "<...>"
  failure_modes: "<...>"
  cross_cutting: "<...>"
  # plus problem-specific ones per artifact type
```

**Classification: NEW (schema fields) + EXTEND (prompts)**

- Add `problem`, `simplest_solution`, `complications_considered` to:
  - `ArchitectureSpec.modules` (per module)
  - `ContractsSpec` (per contract)
  - `TicketSpec` (per ticket)
  - `BuildPlanSpec.epics` (per epic)
- Update role prompts to enforce the sequence (prompt checks that `simplest_solution` is distinct from actual proposal; flags boilerplate)
- Add reviewer pass to check sequence quality (length, uniqueness per field)

---

### 2. Synthetic Operator Simulator

**Current state:** No simulation; workflow tested only manually.

**v2 design requires:** LLM-driven operator simulator walking full project lifecycles — [v2 agent-leverage/problem.md synthetic-operator-simulation]

**Classification: NEW**

- New `jig/testing/operator_simulator.py` module
- Operator personas: methodical-and-thorough, fast-and-shippy, scope-creeper, ambivalent-and-vague, hostile (3-5 starting)
- Scenario library: fresh projects, mid-project pivots, operator errors, bad inputs
- Success-criteria tracking and coverage metrics
- "Realism budget" queue for real-world operator behaviors that surprise the simulator

This lands in parallel with v2 build, not after, because it's a quality multiplier on workflow design.

---

### 3. Quartermaster Agent

**Current state:** No background analysis; operator gets nothing but gate prompts.

**v2 design requires:** Continuous background agent reading analytics event stream; produces periodic operator briefings — [v2 agent-leverage/problem.md quartermaster]

**Classification: NEW**

- New `Quartermaster` agent (standard tier; runs asynchronously)
- Reads `events.jsonl` for aggregates: ticket completion rate, escalation patterns, contract amendments, risks
- Produces weekly (configurable) briefing: "this week: N tickets, module X showing escalations, Y things need attention"
- TUI surface for briefing display
- Feedback path: "was this useful?" → quartermaster prompt-tunes on feedback

Lands after analytics wiring is complete (depends on event stream flowing).

---

### 4. Adversarial Pairing (deferred to v2.x)

**Current state:** Single agent per ticket; no skeptic shadow.

**v2 design:** Periodic-checkpoint flavor (dev pauses at breakpoints; skeptic reviews; dev iterates) deferred to v2.x — [v2 agent-leverage/problem.md adversarial-pairing]

**Classification: DEFER (v2.x)**

**v2 prerequisite (v2 captures data for v2.x design):**
- `BugDiscoveredPostMerge` event — fires when bug surfaces in already-merged code; carries originating ticket + reviewer set + failure category — [v2 analytics/problem.md review-events]
- `BoundedFixLoopExhausted` event — fires when ticket hits 3-cycle review→fix cap — [v2 analytics/problem.md review-events]

Both events already in `jig/analytics/events.py`; wiring needed when bounded fix-loop enforcement lands.

---

### 5. Ensemble Decision-Making (deferred to v2.x)

**Current state:** Single agent per decision; no vote/merge.

**v2 design:** Spawn N agents with varied prompts in parallel; vote/merge on high-stakes decisions — [v2 agent-leverage/problem.md ensemble-decision-making]

**Classification: DEFER (v2.x)**

**v2 prerequisite:**
- `OperatorOverride` event (already in analytics) — every override is a vote; corpus shows which decisions get overridden
- `AutoEscalationTriggered` event (already in analytics) — indicators of decisions where dev didn't catch its own blocking

---

### 6. Translation Renderers — Pydantic from Data Contracts (v2 ships)

**Current state:** Contracts are YAML; code imports Pydantic separately; manual sync.

**v2 design:** Render `data` contracts as Pydantic models automatically — [v2 agent-leverage/problem.md translation-renderers]

**Classification: NEW (one renderer; others defer)**

- New `jig/renderers/` module with:
  - `pydantic_from_data_contract.py` — reads `ContractsSpec.data` contract, emits Pydantic model Python code
  - Per-renderer: Pydantic ✓; OpenAPI / SQL DDL / GraphQL SDL / sequence diagrams deferred
- Wired into codebase such that SA agent can invoke `render_to_pydantic(contract_uri)` and get back Python model source
- Validate round-trip: parse generated code, re-render, compare

---

## SUMMARY TABLE

| Design Area | Classification | Key Files | Notes |
|---|---|---|---|
| **Multi-level PO (L0-L4)** | REPLACE | `init_workflow.py`, `init_mcp.py`, `brief_parser.py`, `spec_uri.py` | L0+L1+L2 rewrite; L3 reuses brief format |
| **SA Discovery** | NEW | `jig/sa/`, `architecture.py`, `contracts.py`, MCP tools | Risk register + spike workflow |
| **VD Role** | NEW | `jig/vd/`, `frontend.py`, `wireframes.py`, wireframe linter | HTML wireframes (not SVG); browser index |
| **Planner PM** | NEW | `jig/pm/`, `build_plan.py`, planner discovery loop | S/M/L estimation + tier assignment |
| **Coordinator PM** | REPLACE | Orchestrator dispatch logic (~150-200 lines) | Bones-first ordering + auto-escalation |
| **Federated Reviewers** | NEW | `jig/review/`, 10 reviewer types, lead-reviewer synthesis | Two-cadence: per-commit mechanical + end-of-ticket federation |
| **Spec Tiers** | REPLACE | `.jig/spec/` on-disk layout, parsers, validators | L0-L4 artifacts + federated spec generation |
| **SA Artifacts** | NEW | `.jig/arch/`, `architecture.yaml`, `contracts.yaml`, cascades | URI-anchored shared contracts |
| **VD Artifacts** | NEW | `.jig/design/`, wireframes + system + frontend architecture | Always-present design system defaults |
| **PM Artifacts** | NEW | `.jig/plan/`, `build-plan.yaml`, `deferred.jsonl` | Epic × layer decomposition |
| **Service Isolation** | NEW | `jig/dev/`, provisioner, manifest, cleanup | Per-agent namespace injection; fixture mocking |
| **Tier Promotion** | EXTEND | `context_resolver.py`, agent spawn | Restart with new tier; preserve worktree |
| **Bus/Stores** | LEAVE ALONE | `jig/store/`, `jig/thread.py` | Foundation is solid |
| **TUI Frame** | LEAVE ALONE | `jig/tui/app.py`, `.tcss`, widgets | Enhance content, not frame |
| **TUI Slash Cmds** | NEW | `jig/tui/commands/`, daemon command registry | `/init`, `/suite`, `/journey`, `/sa`, `/vd`, `/plan`, `/design` |
| **TUI Rendering** | NEW | Screen updates for suite status, wireframes, violations, build plan | Modal overlays + tab enhancements |
| **Analytics Events** | EXTEND (wiring) | `jig/analytics/`, 32 event types already defined | Wire emission throughout codebase |
| **Intent Layer** | NEW (schema) + EXTEND (prompts) | Multi-artifact schema fields + role prompts | problem → simplest → complications |
| **Operator Simulator** | NEW | `jig/testing/operator_simulator.py` | Lands in parallel with v2 build |
| **Quartermaster** | NEW | Quartermaster agent, briefing generation, TUI surface | Lands after analytics wiring |
| **Adversarial Pairing** | DEFER (v2.x) | Pre-wire: `BugDiscoveredPostMerge`, `BoundedFixLoopExhausted` events | Data capture for v2.x design |
| **Ensemble Decisions** | DEFER (v2.x) | Pre-wire: `OperatorOverride`, `AutoEscalationTriggered` events | Data capture for v2.x design |
| **Pydantic Renderer** | NEW | `jig/renderers/pydantic_from_data_contract.py` | Other renderers defer |

---

## Implementation Sequencing

**Phase 1 (Foundations):** L0-L3 spec infrastructure, SA architecture, VD wireframes, service isolation, analytics wiring.

**Phase 2 (Orchestration):** Planner PM, Coordinator PM with bounded fix loops, build-plan enforcement, two-cadence review.

**Phase 3 (Leverage):** Intent layer, operator simulator, quartermaster, Pydantic renderer.

**Phase 2.x (Deferred):** Adversarial pairing, ensemble decisions, additional renderers (once data shows the need).

---

## Risk Areas

1. **L1 discovery conversation length** (30-50 turns) — mitigation: per-persona spawn; resume support; state file tracking.
2. **Contract over-specification** — mitigation: SA checklist defaults aggressively to "N/A"; sized guidance per project size.
3. **Reviewer false positives** — mitigation: configurable sensitivity; operator override always available; default permissive at bones, strict at final.
4. **Wireframe authoring friction** — mitigation: agent-generated initial drafts; operator iterates, doesn't author from scratch.
5. **Service isolation orphan accumulation** — mitigation: cleanup hooks emit events; periodic sweep attempts recovery; operator confirmed before destructive action.
6. **PII/secrets leaking into event stream** — mitigation: schema-design discipline (no event carries prose/prompts/secrets); periodic audit.

---

## Open Questions for Implementation

1. **L1 PO: should it suggest "what comes next" mid-journey, or stay purely Socratic?** Current: stay Socratic by default; operator can `/journey suggest` to opt in.
2. **suites.yaml status field:** derivable from disk (does brief.md exist + ticket resolved)? Probably drop the explicit field.
3. **L1 → L2 transition:** auto-handoff on `discovery_finalize`, or separate `/suites organize` command? Current: auto-handoff with approval gate.
4. **Cascade proposal staging:** present all at once, or allow operator to accept contract amendments now and defer build-plan re-plan? Current: support staging within transactional discipline.
5. **VD foundational decisions:** operator can skip (keep all defaults) at any point — no friction. Confirm this design at TUI prototype.
6. **Reviewer synthesis algorithm:** vote? Confidence-weighted merge? Pick strongest? Run synthesizer agent? Probably depends on decision type.
7. **Quartermaster cadence:** daily / weekly / on-demand? Probably configurable per operator, default weekly.
8. **Intent field discipline:** how to prevent agents from writing boilerplate? Length heuristic? Uniqueness check? Dedicated reviewer pass?

---

## Rollout Path

**v2 launch:** Spec tiers + SA + VD + Planner + Coordinator + reviewers + service isolation + analytics wiring.

**v2.1:** Operator simulator, quartermaster, intent layer in all schemas, Pydantic renderer.

**v2.x (when data shows need):** Adversarial pairing, ensemble decisions, additional renderers.

The design is intentionally sequenced to defer high-speculation features (4, 5) until we have data on what actually matters. Capture requirements (events) from day one; build consumers when patterns emerge.
