# C4 Code Level: Jig Framework

## Overview

- **Name**: Jig — AI Agent Orchestration Framework for Structured Specification Generation
- **Description**: A comprehensive system for breaking down software projects into structured, layered specifications (L0-L3 Product Owner hierarchy) and orchestrating specialized AI agents to work through discovery, suite organization, architectural design, planning, and review phases. Built on YAML-based spec language, contract-driven architecture, and federated reviewer systems.
- **Location**: `/Users/brent/Projects/personal/jig/jig/`
- **Language**: Python 3.11+
- **Purpose**: Provide an orchestration backbone for AI-driven software development with deterministic scenarios, multi-level specification synthesis, contract compliance, and intelligent ticket routing.

## Architecture Overview

Jig consists of 6 interconnected subsystems plus a core orchestration layer:

```
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                         │
│  (coordinator.py, orchestrator.py, run_agent)                    │
│  - Ticket lifecycle management                                   │
│  - Agent dispatch & lifecycle                                    │
│  - Phase transitions & handoff routing                           │
└─────────────────────────────────────────────────────────────────┘
          ↑                ↑                  ↑                 ↑
    ┌─────┴────┐    ┌─────┴─────┐    ┌──────┴──────┐    ┌──────┴────┐
    │    PO     │    │    PM      │    │     SA      │    │  Reviewers │
    │ Subsystem │    │ Subsystem  │    │ Subsystem   │    │ Subsystem  │
    └─────┬────┘    └─────┬─────┘    └──────┬──────┘    └──────┬────┘
          │              │                  │                   │
          ├─────────────────────────────────┴───────────────────┤
          │                                                       │
┌─────────┴──────────────────────────────────────────────────────┴───┐
│                      DATA PERSISTENCE LAYER                         │
│  (store/): bus.py, tickets.py, threads.py, review_comments.py,     │
│  memory.py, checkpoints.py                                          │
│  - JSONL-based event sourcing                                       │
│  - Message pub/sub                                                  │
│  - Agent memory                                                     │
└─────────────────────────────────────────────────────────────────────┘
          ↑                                              ↑
    ┌─────┴──────────────────────────────────────────────┴────┐
    │        Schemas (po.py, arch.py, plan.py, etc.)         │
    │        Wireframes (HTML + linting)                      │
    │        Skills (md docs)                                 │
    └────────────────────────────────────────────────────────┘
```

## Subsystem: Product Owner (PO) Hierarchy

**Files**: `po_l0_mcp.py`, `po_l1_mcp.py`, `po_l2_mcp.py`, `po_l3_mcp.py`

**Purpose**: Breaks projects into structured, multi-level specifications via 4 distinct conversational phases.

### L0 PO — Pitch Capture (`po_l0_mcp.py`)

Captures project pitch, problem, audience, and product-level non-goals in 3-5 turns.

**Key Functions**:
- `handle_l0_finalize(tickets, threads, bus, project_path, name, pitch, problem, audience, non_goals, author) -> str`
  - Constructs `Project` schema, writes `docs/brief.md` (markdown) + `.jig/spec/project.structured.yaml`
  - Posts `Handoff` to thread, publishes orchestrator message, resolves post-handoff state
  - Returns handoff entry id
  - Location: lines 103-175
- `render_project_md(project: Project) -> str`
  - Renders Project to markdown with name, pitch, problem, audience, non-goals sections
  - Location: lines 40-83

**Dependencies**:
- `jig.schemas.po.Project`, `ProductNonGoal`
- `jig.store.threads.ThreadStore`, `jig.store.tickets.TicketStore`, `jig.store.bus.MessageBus`
- `jig.thread.Handoff`
- `jig.handoff_resolve.resolve_after_handoff`

**State**: Writes to project path under `docs/`

---

### L1 PO — Discovery (`po_l1_mcp.py`)

Walks operator through 5-phase journey discovery (Frame/Elicit/Walk/Probe/Playback) to extract personas, journeys, and capabilities.

**Key Functions**:
- `handle_discovery_finalize(tickets, threads, bus, project_path, project_name, intro, personas, journeys, capability_roster, author) -> str`
  - Synthesizes discovery doc from sidecar YAML or explicit args, validates invariants
  - Writes `.jig/spec/discovery.md` (markdown) + `.jig/spec/discovery.structured.yaml`
  - Clears in-flight state, posts handoff to L2 PO
  - Location: lines 1023-1128
- `handle_discovery_set_phase(project_path, persona_id, journey_id, phase, step) -> None`
  - Records current position in 5-phase walk to `discovery.state.yaml`
  - Location: lines 594-610
- `handle_discovery_add_journey(project_path, persona_id, journey_id, title, narrative, capability_ids, playback_text) -> None`
  - Stages a committed journey in sidecar YAML, optionally writes per-journey playback markdown
  - Location: lines 716-753
- `handle_discovery_add_capability(project_path, capability_id, description, journey_ids) -> None`
  - Stages/merges capability roster entry (union-merge journey_ids on id collision)
  - Location: lines 756-777
- `handle_discovery_resume(project_path, reconcile_mode) -> ResumeResult`
  - Resumes session, validates state-vs-doc consistency, applies reconciliation (auto/prefer-state/prefer-doc/abandon-state)
  - Location: lines 355-412
- `validate_state_consistency(state, discovery_doc, on_disk_digest) -> list[StateDivergence]`
  - Detects stale-journey, concurrent-edit, partial-walk-orphan divergences
  - Location: lines 146-234
- `render_discovery_md(doc: DiscoveryDoc) -> str`
  - Renders discovery doc with personas, journeys, capability roster
  - Location: lines 448-519

**Dependencies**:
- `jig.schemas.po` (DiscoveryDoc, DiscoveryState, Journey, Persona, CapabilityRosterEntry, etc.)
- `jig.spec_loader` (discovery_path, load_discovery, load_discovery_state, save_discovery_state)
- `jig.store` (threads, tickets, bus)

**State**: Maintains `discovery.state.yaml`, staged sidecars (journeys, personas, roster, intro cache), playback files

---

### L2 PO — Suite Organization (`po_l2_mcp.py`)

Groups L1 capabilities into 4-6 suites with soft validation (3-5 capabilities per suite).

**Key Functions**:
- `handle_l2_finalize(tickets, threads, bus, project_path, suites, crosscutting_non_goals, author) -> str`
  - Reads `discovery.structured.yaml` for coverage validation, writes `suites.yaml`
  - Enforces: every L1 capability in exactly one suite, no orphans/duplicates
  - Soft warnings for suite size out of 3-5 range (advisory, not blocking)
  - Location: lines 215-310
- `validate_capability_coverage(suites, discovery) -> None`
  - Checks suite ids unique, capability union equals roster, no missing/extra/duplicate
  - Location: lines 121-180
- `compute_size_warnings(suites, soft_min, soft_max) -> list[str]`
  - Returns warnings for suites outside [3, 5] capability target
  - Location: lines 183-209

**Dependencies**:
- `jig.schemas.po` (SuitesIndex, Suite, DiscoveryDoc)
- `jig.spec_loader` (load_discovery, suites_index_path)

**State**: Writes to `.jig/spec/suites.yaml`

---

### L3 PO — Suite Brief (`po_l3_mcp.py`)

Elaborates one suite's brief and structured spec (capabilities, behaviors, user stories, acceptance criteria).

**Key Functions**:
- `handle_l3_finalize(tickets, threads, bus, project_path, suite_id, intro, capabilities, non_goals, author) -> str`
  - Reads `suites.yaml` for suite definition + capability allowlist, validates L3 inputs
  - Writes `.jig/spec/suites/<suite_id>/brief.md` + `spec.structured.yaml`
  - Location: lines 283-381
- `render_suite_brief_md(suite, intro, capabilities, non_goals) -> str`
  - Renders markdown with suite title, intro, Built/Planned/Backlog/Archived sections, non-goals
  - Location: lines 67-129
- `validate_capability_allowlist(suite, capabilities) -> None`
  - Rejects capability ids not in suite's allowlist (gap → must go to L1)
  - Location: lines 255-277

**Dependencies**:
- `jig.schemas.po` (SuitesIndex, Suite), `jig.spec_schema` (StructuredSpec, Capability)
- `jig.spec_loader` (load_suites_index, suite_brief_path, suite_structured_path)

**State**: Writes per-suite artifacts under `.jig/spec/suites/<suite_id>/`

---

## Subsystem: Software Architect (SA)

**Files**: `sa_mcp.py`

**Purpose**: Writes project-level architecture and per-module contracts (data stores, modules, cross-cutting policies, risks).

### SA Main Handler (`sa_mcp.py`)

**Key Functions**:
- `handle_sa_finalize(tickets, threads, bus, project_path, architecture, module_contracts, author) -> str`
  - Validates architecture + contracts against schemas, enforces bones minimums (≥1 data store, ≥1 module, ≥1 owned collection, ≥1 integration AC)
  - Writes `.jig/spec/architecture.yaml` + `.jig/spec/modules/<m>/contracts.yaml`
  - Enforces module contract's module id appears in architecture
  - Location: lines 136-221
- `_validate_bones_minimums(arch, contracts) -> None`
  - Checks for required minimums per bones scope
  - Location: lines 91-114
- `_validate_module_link(arch, contracts) -> None`
  - Ensures contracts' module id is in architecture
  - Location: lines 117-130

**Dependencies**:
- `jig.schemas.arch` (Architecture, ContractsFile, Module, etc.)
- `jig.spec_loader` (architecture_path, module_contracts_path)
- `jig.store` (threads, tickets, bus)

**State**: Writes to `.jig/spec/architecture.yaml` + module-specific contracts YAML

---

## Subsystem: Product Manager (PM)

**Files**: `pm/calibration.py`, `pm/cycle_view.py`, `pm/overrides.py`, `pm/tier_promotion.py`

**Purpose**: Estimation calibration, cycle-aware coordination, tier promotion, and PM-specific views.

### Calibration (`pm/calibration.py`)

**Key Functions**:
- `record_completion_sample(ticket, analytics, outcome) -> CalibrationSample`
  - Extracts observations: turns, tool calls, tokens from a completed ticket
  - Location: (signature inferred from docstring)
- `current_envelopes(calibration_store) -> dict[Size, Envelope]`
  - Computes per-size (S/M/L) median + p90 envelopes for turns/cost, falls back to defaults if <5 samples
  - Returns envelope data for planner PM to use in estimation
  - Location: (method inferred)

**Key Data Structures**:
- `CalibrationSample` — one observation (size, tier, turns, tool_calls, tokens, duration)
- `Envelope` — bounds for turns/cost at p50/p90
- `CalibrationStore` — JSONL append-only at `.jig/plan/calibration.jsonl`

**Dependencies**:
- `jig.ticket.Size`, `jig.ticket.Ticket`
- `jig.analytics.store.AnalyticsStore`
- `jig.analytics.events.EstimationCalibrationUpdated`

---

## Subsystem: Synthetic Operator Simulator (Sim)

**Files**: `sim/driver.py`, `sim/scenario.py`, `sim/assertions.py`, `sim/cli.py`, `sim/coverage.py`

**Purpose**: Loads YAML scenarios describing end-to-end workflows and validates them with assertions (artifact writes, analytics events, ticket statuses, reviewer comments, cost budgets).

### Scenario Schema (`sim/scenario.py`)

**Key Data Structures**:
- `StepKind` enum — dispatcher cases (INVOKE_L0_FINALIZE, WRITE_ARCHITECTURE, MATERIALIZE_TICKETS, etc.)
- `ScenarioStep` — (kind, params dict)
- `Scenario` — (name, description, steps, final_assertions, coverage_tags, tier)

**Dependencies**:
- `jig.sim.assertions.ScenarioAssertionUnion`

**Example**: See bones scenario in test fixtures

---

### Driver (`sim/driver.py`)

**Key Functions**:
- `Driver.run(project_root, scenario) -> ScenarioRunResult`
  - Sets up per-run isolation (fresh .jig/store/, JIG_SIMULATOR=true env)
  - Dispatches each step via `_dispatch_step(step_kind, step_params)`
  - Evaluates assertions after each step
  - In mock mode: dev step is deterministic helper; in real mode: boots Orchestrator
  - Location: (class & method signatures inferred from docstring)

**Assertion Evaluation** (`sim/assertions.py`):
- `ArtifactWrittenAssertion` — file exists, optional substring/regex match
- `AnalyticsEventEmittedAssertion` — event type emitted, optional field constraints
- `TicketStatusAssertion` — ticket reached status
- `ReviewerReturnedNoCriticalAssertion` — reviewer's output has no critical comments
- `CostUnderBudgetAssertion` — total spend under USD threshold

**Dependencies**:
- All v2 MCP handlers (po_l0_mcp, po_l1_mcp, po_l2_mcp, po_l3_mcp, sa_mcp, planner_pm_mcp, vd_mcp)
- `jig.coordinator.Coordinator`
- `jig.analytics.store.AnalyticsStore`
- `jig.store` (tickets, threads, bus)

---

## Subsystem: Reviewer Federation

**Files**: `reviewers/dispatch.py`, `reviewers/comment.py`, `reviewers/contract_compliance.py`, `reviewers/cross_cutting_policy.py`, `reviewers/spec_compliance.py`, `reviewers/intent_compliance.py`, etc.

**Purpose**: Federated review system with mechanical (deterministic) + judgment (LLM) reviewers across contract, intent, spec, security, performance, and architectural dimensions.

### Reviewer Selection & Dispatch (`reviewers/dispatch.py`)

**Key Functions**:
- `select_reviewers_for_ticket(ticket) -> list[str]`
  - Returns reviewer ids for end-of-ticket cadence based on ticket layer + reviewer_set
  - Default-on sets: bones = contract-compliance + cross-cutting; mvp/final = contract + intent + cross-cutting + spec
  - Auto-selections: security (touches-auth/pii/secrets/payments labels or tier=SA), performance (perf-budget label or AC keywords), architectural (touches-contract label or dev_tier=sa)
  - Location: (signature inferred)
- `dispatch_for_cadence(tickets, threads, ticket, cadence) -> dict[str, list[ReviewerComment]]`
  - Runs reviewers at cadence (per_commit or end_of_ticket)
  - Per-commit runs only mechanical subset (fast, single-digit-second budget)
  - End-of-ticket runs full default-on set
  - Location: (signature inferred)

**Reviewer Ids**:
- Mechanical (deterministic, no LLM):
  - `contract-compliance` — contract-violation, empty-diff, integration-ac-not-referenced
  - `intent-compliance` — intent-too-short, intent-boilerplate-restatement, intent-complications-skipped
  - `cross-cutting-policy` — universal-rule violations
  - `spec-compliance` — behavior-AC reference misses, capability existence checks
  - `visual-compliance` — visual_compliance checks for UI tickets
  - `accessibility` — WCAG AA mechanical
  - `responsive-design` — responsive design enforcement
- Judgment (LLM-driven, Track G MVP follow-on):
  - `reviewer-pattern-conformance` — code quality patterns
  - `reviewer-security` — security analysis (auto-selected for sensitive tickets)
  - `reviewer-performance` — performance budget validation (auto-selected for perf-budget tickets)
  - `reviewer-architectural` — architecture pattern analysis (auto-selected for SA-tier tickets)

### Comment Structure (`reviewers/comment.py`)

**Key Data Structure**:
- `ReviewerComment` — (reviewer, ticket_id, comment_type, severity [critical/important/notable], location [file/line/contract_uri], suggested_diff, confidence [0-1], evidence, auto_apply_after)
  - Comment types: empty-diff, contract-violation, intent-too-short, spec-violation, pattern-divergence, etc.
  - Deterministic reviewers use confidence=1.0; judgment reviewers express certainty as 0-1

**Key Functions**:
- `format_comment_markdown(comment) -> str`
  - Renders comment in human-readable markdown with severity, location, suggested fix
  - Location: (signature inferred)

### MCP Handler (`reviewer_mcp.py`)

**Key Functions**:
- `handle_reviewer_post_comment(project_path, reviewer_role, args, ticket_id, cycle) -> str`
  - Validates comment payload against ReviewerComment schema
  - Runs self-check gate (drops low-signal noise)
  - Persists to ReviewCommentsStore at `.jig/store/review_comments.jsonl`
  - Returns comment id
  - Location: lines 63-113

**Dependencies**:
- `jig.reviewers.comment.ReviewerComment`
- `jig.reviewers.self_check.validate_comment_for_self_check`
- `jig.store.review_comments.ReviewCommentsStore`

---

## Subsystem: Data Schemas

**Files**: `schemas/po.py`, `schemas/arch.py`, `schemas/plan.py`, `schemas/frontend.py`, `schemas/design_system.py`, `schemas/dev_env.py`

**Purpose**: Pydantic models defining all v2 artifact shapes (PO outputs, SA outputs, PM outputs, VD outputs).

### PO Schemas (`schemas/po.py`)

**Key Models**:
- `Project` — L0 artifact (name, pitch, problem, audience, non_goals: list[ProductNonGoal])
- `ProductNonGoal` — (id, text, rationale)
- `Persona` — (id, description)
- `Journey` — (id, persona_id, title, narrative, capability_ids)
- `CapabilityRosterEntry` — (id, description, journey_ids)
- `DiscoveryDoc` — (project_name, intro, personas, journeys, capability_roster, generated_at)
- `DiscoveryState` — (status, current [DiscoveryPhase], next_question, partial_walk [CapabilityCandidate], pending_capabilities, personas_pending, discovery_doc_digest)
- `Suite` — (id, title, summary, capabilities: list[str])
- `SuitesIndex` — (spec_version, suites, crosscutting_non_goals)
- `StateDivergence` — (kind, detail, suggested_resolution) — for resume conflict handling

**Validators**:
- `_validate_kebab` — enforces kebab-case on id fields
- `validate_tz_aware` — ensures datetimes are timezone-aware (UTC)

---

### SA Schemas (`schemas/arch.py`)

**Key Models**:
- `Architecture` — (spec_version, data_stores, modules, shared_contracts, cross_cutting_policies, risks, change_log)
- `Module` — (id, intent, tier_hint, owned_collections, exposed_apis, external_dependencies, behavioral_contracts, data_contracts, integration_ac, open_questions)
- `ContractsFile` — (spec_version, module, owns, integration_ac, behavioral_contracts, data_contracts, external_dependencies, change_log)
- `DataStore` — (id, kind [sql/nosql/kv/cache/etc.], intent)
- `OwnedCollection` — (name, data_store, intent, description)
- `DataContract` — (id, polarity, intent, description, acceptance_criteria, changes)
- `BehavioralContract` — (id, polarity, intent, description, acceptance_criteria, changes)
- `IntegrationAcceptance` — (capability_id, description, musts, shoulds, wont_haves)
- `Risk` — (id, intent, impact, likelihood, status, open_questions, dependent_contracts, spike)

**Validators**:
- `validate_kebab_id`, `validate_project_uri_shape` — enforce naming conventions

---

### Plan Schemas (`schemas/plan.py`)

**Key Models**:
- `BuildPlan` — (spec_version, layers [Layer], ordering_rule, cascade_proposals)
- `Layer` — (id, name, status, order, tickets)
- `OrderingRule` — (kind [sequential/capability_coverage/dependency_driven], params)

---

## Subsystem: Proposal System

**Files**: `proposal_mcp.py`

**Purpose**: Manages ticket spec changes via proposals with acceptance/rejection/refinement workflow.

### MCP Handlers (`proposal_mcp.py`)

**Key Functions**:
- `handle_propose_change(tickets, threads, sender, args, project_path) -> dict`
  - Creates Proposal thread entry (pending state), routes to owner via OwnerRouting
  - Optionally spawns helper agent per routing.assignment config
  - Args: ticket_id, target, change [YAML], section, content [rationale]
  - Location: lines 47-114
- `handle_resolve_proposal(tickets, threads, sender, args, project_path) -> dict`
  - Accept/reject/refine a pending proposal with self-cert guard (blocked/warn modes)
  - For spec targets: enforces section locks, applies spec change, validates work-type schema
  - Location: lines 120-228
- `handle_list_proposals(threads, args) -> list[dict]`
  - Query helper: filters by ticket_id, state, target
  - Excludes resolver entries (parent_id != None) from results
  - Location: lines 234-266

**Dependencies**:
- `jig.ownership.resolve_owner`, `OwnerRouting`
- `jig.configs.load_config` — reads self_approval mode
- `jig.specs.load_ticket_spec`, `save_ticket_spec` — ticket-level spec updates
- `jig.section_locks.locked_sections_for_ticket` — lock enforcement post-handoff

---

## Subsystem: Wireframes (Visual Design)

**Files**: `wireframes/__init__.py`, `wireframes/format.py`, `wireframes/linter.py`, `wireframes/wireframe_css.py`, `wireframes/index_generator.py`

**Purpose**: HTML wireframe format with constrained vocabulary, linting, and browser index generation.

### Key Functions

- `extract_meta(html) -> WireframeMeta`
  - Parses HTML comment at top to extract metadata (screen-id, title, description, state-options)
  - Location: (inferred from module docstring)
- `lint_wireframe(html) -> list[LintError]`
  - Enforces constrained vocabulary (Alpine only, no inline styles, no real colors, approved utility classes)
  - Returns LintError objects with severity [error/warning]
  - Location: (inferred)
- `generate_index(wireframe_dir) -> str`
  - Renders `index.html` over all per-screen HTMLs with state-option toggles
  - Location: (inferred)
- `generate_wireframe_css() -> str`
  - Returns canonical `wireframe.css` for all screens
  - Location: (inferred)

**Key Data Structure**:
- `WireframeMeta` — (screen_id, title, description, state_options: list[StateOption])
- `LintError` — (severity, message, line, suggestion)

---

## Subsystem: Data Persistence (Store)

**Files**: `store/core.py`, `store/bus.py`, `store/tickets.py`, `store/threads.py`, `store/review_comments.py`, `store/memory.py`, `store/checkpoints.py`

**Purpose**: JSONL-based event sourcing for tickets, threads, messages, review comments, and agent memory.

### Core Store (`store/core.py`)

**Key Class**:
- `JsonlStore` — JSONL append-only storage with async load/insert/update/delete
  - Maintains in-memory index on configured fields for fast lookup
  - Validates record size (default 1 MiB cap)
  - Location: lines 22-176 (inferred)

### Message Bus (`store/bus.py`)

**Key Class**:
- `MessageBus` — pub/sub message broker backed by TypedCollection
  - `publish(message) -> str` — stores + broadcasts to subscribers
  - `subscribe(topic) -> Queue` — returns queue for topic
  - `subscribe_agent(topic, agent_id) -> Queue` — stable per-agent subscription
  - `get_history(topic, limit) -> list[Message]` — query by topic
  - `recent(limit, kind) -> list[Message]` — cross-topic recent messages
  - Location: lines 36-134

**Key Model**:
- `Message` — (sender/from, to, type [TASK_ASSIGNMENT/COMPLETION/QUESTION/ANSWER/CONTEXT_UPDATE/STATUS], payload, timestamp, correlation_id, topic)
- `MessageType` enum — message kind discriminator

### Ticket Store (`store/tickets.py`)

**Key Class**:
- `TicketStore` — manages Ticket entries
  - `get(ticket_id) -> Ticket | None`
  - `all_for_layer(layer) -> list[Ticket]`
  - `find_for_phase(phase) -> list[Ticket]`
  - Location: (inferred from usage)

### Thread Store (`store/threads.py`)

**Key Class**:
- `ThreadStore` — manages thread entries (Note, Proposal, Handoff, SystemEvent)
  - `post(entry) -> str` — append to thread, return id
  - `get(entry_id) -> ThreadEntry | None`
  - `find_by_kind(ticket_id, kind) -> list[ThreadEntry]`
  - `all_by_kind(kind) -> list[ThreadEntry]`
  - Location: (inferred)

**Key Models**:
- `Note` — prose comment on a ticket
- `Proposal` — spec change proposal (pending → accepted/rejected/refining)
- `Handoff` — phase transition record with outputs + summary
- `SystemEvent` — orchestrator-internal state change (status_change, etc.)

### Review Comments Store (`store/review_comments.py`)

**Key Class**:
- `ReviewCommentsStore` — manages ReviewerComment entries at `.jig/store/review_comments.jsonl`
  - `append(comment: ReviewerComment) -> str` — store comment, return id
  - `find_for_ticket(ticket_id) -> list[ReviewerComment]` — query by ticket
  - Location: (inferred)

### Memory Store (`store/memory.py`)

**Key Class**:
- `MemoryStore` — stores agent memory (Handoff learnings, explicit Learning entries)
  - `post(memory) -> str`
  - `find_learnings(agent_role) -> list[Learning]`
  - Location: (inferred)

**Key Models**:
- `Handoff` — learnings extracted from prior phase transitions (blockers, surprises, successes)
- `Learning` — explicit knowledge recorded by agents or operators

---

## Top-Level Orchestration

**Files**: `coordinator.py`, `orchestrator.py`, `run_agent()` (in agent.py), `spec_generator.py`, `proposal_mcp.py`, `handoff_resolve.py`

**Purpose**: Ties all subsystems together for end-to-end workflow execution.

### Coordinator (`coordinator.py`)

- Manages per-layer ticket lifecycle (ready → dispatched → completed → promoted)
- Enforces layer ordering (L0 before L1, L1 before L2, etc.)
- Tracks in-flight agent dispatches per ticket

### Orchestrator (`orchestrator.py`)

- Main event loop that:
  1. Loads project state (spec, architecture, build plan, tickets, threads)
  2. Selects which tickets are ready for dispatch
  3. Routes each ticket to the appropriate agent based on phase/layer
  4. Waits for agent completion (via analytics store or thread updates)
  5. Runs reviewer federation post-agent
  6. Updates ticket status and plan
  7. Repeats until all layers done or blocked

### Handoff Resolution (`handoff_resolve.py`)

- `resolve_after_handoff(tickets, threads, bus, ticket_id, author) -> None`
  - Called after each phase completes (PO L0/L1/L2/L3, SA, PM)
  - Extracts learnings from handoff summary
  - Publishes next-phase-ready context update
  - Triggers coordinator to re-evaluate ready tickets

---

## Cross-Cutting Concerns

### Spec Loading (`spec_loader.py`)

Top-level loaders for all artifacts:
- `load_structured_spec(project_path) -> (StructuredSpec, Path)` — v1 monolithic
- `load_suites_index(project_path) -> SuitesIndex` — L2
- `load_discovery(project_path) -> DiscoveryDoc` — L1
- `load_architecture(project_path) -> Architecture` — SA
- `load_build_plan(project_path) -> BuildPlan` — PM
- Path helpers: `suite_brief_path()`, `suite_structured_path()`, `architecture_path()`, etc.

### Spec Generation (`spec_generator.py`)

- `run_spec_generator(project_path, tickets, threads, memory, bus, emitter) -> None`
  - One-shot non-conversational agent that translates brief into structured spec
  - Validates spec, posts `spec_publish` or `spec_report_gaps` before exit
  - Returns when agent process exits

---

## Key Interfaces & Data Flow

### PO Layer Handoff Chain

```
L0 (Project)
  ↓ writes docs/brief.md + .jig/spec/project.structured.yaml
  ↓ posts Handoff("po-l1") → orchestrator routes to L1 PO
L1 (Discovery)
  ↓ writes .jig/spec/discovery.md + .jig/spec/discovery.structured.yaml
  ↓ posts Handoff("po-l2") → orchestrator routes to L2 PO
L2 (Suites)
  ↓ writes .jig/spec/suites.yaml
  ↓ posts Handoff("po-l3") → orchestrator routes to L3 PO
L3 (Suite Briefs)
  ↓ writes .jig/spec/suites/<id>/brief.md + spec.structured.yaml
  ↓ posts Handoff("sa") → orchestrator routes to SA
```

### SA → PM → Dev Handoff Chain

```
SA (Architecture + Contracts)
  ↓ writes .jig/spec/architecture.yaml + .jig/spec/modules/<m>/contracts.yaml
  ↓ posts Handoff("pm") → orchestrator routes to Planner PM
PM (Build Plan)
  ↓ writes .jig/plan/build-plan.yaml with layers + ordering
  ↓ posts Handoff("dev") → Coordinator materializes dev tickets
Dev (Per-Ticket Impl)
  ↓ writes code + tests, posts via proposal_mcp
  ↓ Reviewer federation validates contract compliance
  ↓ posts Handoff("complete") → layer promotion
```

### Ticket State Machine

```
pending → in_progress (agent dispatched)
       → waiting_for_input (blocked, awaits proposal resolution)
       → completed (agent finished, reviews passed)
       → promoted (layer complete, ready for next)
```

---

## Key Dependencies & Integration Points

### External Integrations

- **Claude Agent SDK** — runs LLM agents via `run_agent()` in context of `AgentSpawnContext`
- **Pydantic V2** — all schemas inherit from BaseModel with validation
- **YAML** — spec artifacts serialize to YAML for operator readability
- **JSONL** — event sourcing backend (.jig/store/ files)
- **Asyncio** — async/await throughout orchestrator and store operations

### Configuration System

- `jig.config.load_config(project_path) -> Config`
  - Reads `.jig/config.yaml` for self_approval mode, defaults, routing rules
  - Passed to proposal handlers, ownership resolution

### Ownership & Routing

- `jig.ownership.resolve_owner(config, target_uri, work_type_schema) -> OwnerRouting`
  - Maps ticket-spec targets to owner roles (developer, designer, architect, etc.)
  - Used by proposal system to route changes to the right agent

---

## Typical Execution Flow

1. **Operator initializes project** via CLI
   - Creates project dir structure, .jig/ dirs
   - TUI/CLI spawns L0 PO agent

2. **L0 PO runs** (agent-driven or synthetic)
   - Conversational: 3-5 turns capturing pitch + problem + audience + non-goals
   - Calls `handle_l0_finalize()` → writes Project + Handoff
   - Orchestrator detects Handoff, routes to L1 PO

3. **L1 PO runs** (agent-driven or synthetic)
   - 5-phase walk (Frame/Elicit/Walk/Probe/Playback) per journey
   - Stages personas/journeys/capabilities incrementally
   - Calls `handle_discovery_finalize()` → writes DiscoveryDoc + Handoff
   - Orchestrator routes to L2 PO

4. **L2 PO runs** (currently synthetic in bones; MVP adds agent)
   - Groups capabilities into suites
   - Validates coverage, emits size warnings
   - Calls `handle_l2_finalize()` → writes SuitesIndex + Handoff
   - Orchestrator routes to L3 PO

5. **L3 PO runs** (agent-driven)
   - Per-suite brief elaboration
   - Calls `handle_l3_finalize()` → writes suite brief + spec.structured.yaml + Handoff
   - Orchestrator routes to SA

6. **SA runs** (agent-driven or synthetic)
   - Architecture + contract authoring (incremental multi-call dance in MVP)
   - Calls `handle_sa_finalize()` → writes architecture.yaml + contracts.yaml + Handoff
   - Orchestrator routes to PM

7. **PM Planner runs** (MVP; bones uses synthetic handoff)
   - Reads architecture + L3 spec
   - Estimates ticket sizes + turns + cost
   - Calls `handle_plan_finalize()` → writes build-plan.yaml + Handoff
   - Orchestrator enters Coordinator dispatch loop

8. **Coordinator cycles** through layers
   - For each layer, materializes next-ready tickets
   - Orchestrator dispatches dev agent per ticket
   - Dev agent reads ticket spec, writes code, posts proposals
   - Reviewer federation validates (contract compliance, spec compliance, etc.)
   - Coordinator waits for completion, promotes tier, re-evaluates ready

9. **Simulator (optional)** validates with assertions
   - After each driver step, evaluates assertions
   - Can run in mock mode (deterministic helper dev) or real mode (full LLM orchestrator)

---

## Bones vs MVP vs Final Scope Notes

- **Bones**: L0 + L3 (full workflow), synthetic L1/L2, mock dev agent, 5 assertion kinds, contract-compliance reviewer
- **MVP**: Adds L1/L2 agents, full coordinator cycle, additional reviewers, real dev mode
- **Final**: Adds judgment reviewers, multi-module SA, risk spikes, cascade proposals, estimation calibration, tier promotion

---

## Files Not Yet Examined

The following are related but not detailed in this document (further C4 investigation):
- `coordinator.py` — detailed layer/tier logic
- `orchestrator.py` — event loop + dispatch
- `agent.py` — agent spawn context + SDK wiring
- `ticket.py` — Ticket model + Size enum
- `thread.py` — thread entry models (Note, Proposal, Handoff, SystemEvent)
- Analytics subsystem (`analytics/`) — event emission + cost tracking
- Dev environment (`dev_env/`) — provisioning + manifest
- Various utility modules (atomic writes, safe paths, URI validation, etc.)

