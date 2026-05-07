---
title: v2 Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-05-03
---

# v2 Implementation Plan

## Summary

v2 ships the multi-agent jig redesign described across the design corpus. This plan synthesizes ~13 design docs plus the
gap analysis into a concrete cross-doc dependency graph, bones/MVP/final layering applied to v2 itself, and a
track-decomposed sequence of work.

**Design corpus this plan synthesizes:**

- `TENETS.md`, `ontology.md` — principles + vocabulary
- `docs/v2.0/multi-level-spec/` — PO discovery (L0–L4)
- `docs/v2.0/sa-architecture/` — SA contracts, risks, spikes, cascade
- `docs/v2.0/visual-design/` — VD frontend arch + design system + wireframes
- `docs/v2.0/pm-workflow/` — Planner + Coordinator, build plan, federated reviewers
- `docs/v2.0/dev-environment/` — service isolation + provisioning
- `docs/v2.0/analytics/` — event capture (schema in `jig/analytics/`)
- `docs/v2.0/agent-leverage/` — six commitments with v2/v2.x split
- `docs/v2.0/synthetic-operator/` — workflow validation via simulator
- `docs/v2.0/uri-scheme/` — multi-authority URI extension
- `docs/v2.0/learnings/` (deferred), `docs/v2.0/agent-testing/` (deferred placeholder)
- `docs/v2.0/implementation/gap-analysis.md` — current jig vs v2 designs

**No migration.** Confirmed 2026-05-03: clean break, no v1→v2 tooling, no backward-compat detection. v2 is the format
from day one.

**v2 ships:**
- Multi-level PO workflow (L0–L4)
- SA workflow (contracts, risks, spikes, cascade-after-impossible)
- VD workflow (frontend arch + design system + wireframes), default minimal stack (HTMX + Alpine + custom utility CSS)
- PM workflow (Planner + Coordinator, build plan with bones/MVP/final layering, cycles)
- Reviewer federation (mechanical reviewers + judgment reviewers, two-cadence review)
- Auto-escalation thresholds + forced reflection
- Service isolation primitives (Postgres schema, NATS subject prefix, etc.)
- Analytics event capture wired through every emit site
- Quartermaster agent (after analytics wiring solid)
- Intent-layer schema discipline (problem → simplest → complications)
- One renderer (Pydantic-from-data-contract)
- Synthetic operator simulator (parallel with v2 build)
- URI scheme extension (multi-authority + sub-contract anchoring + versioning)

**v2.x defers:** adversarial pairing, ensemble decision-making, additional renderers, heuristics layer, full
agent-driven testing framework. Each has explicit revisit triggers in its respective design doc; v2 captures the
analytics events those v2.x designs will need so the corpus accumulates from day one.

---

## Bones / MVP / Final layering applied to v2 itself

We're using v2's own discipline to ship v2. Three completeness layers:

### Bones (the walking skeleton)

**One happy-path scenario goes end-to-end through every layer of the v2 system.** A single methodical operator drives a
single trivial project from L0 pitch through one ticket merged. Every module touched, contracts demonstrably compose, no
edge cases, no error handling, no full reviewer federation.

**Done when:** the synthetic operator simulator can run `po-l1-happy-path-bones.scenario.yaml` (a stripped-down
scenario) end-to-end and the assertion suite passes. This is the bones tracer bullet for v2.

### MVP

**Each major role and workflow fleshed out to "useful for a real medium-sized project."** Real projects can be specced,
planned, dispatched, and shipped through v2. Federated reviewer covers the mechanical-cadence reviewers
+ default judgment reviewers. Service isolation handles the common services. Analytics wires through every emit
site. Quartermaster surfaces operator-facing briefings. Synthetic operator covers the smoke tier of scenarios.

**Done when:** an operator can drive an ATS-sized real project (15-25 capabilities, 4-6 suites) through v2 end-to-end
with reasonable success rate, and the simulator's smoke tier covers the canonical happy paths.

### Final

**Edge cases, polish, full reviewer set, accessibility / responsive completion, full auto-escalation tuning.** All the
reviewer types (pattern, error handling, test adequacy, security, performance, visual compliance, architectural review).
Auto-escalation thresholds calibrated against observed runs. Cascade-after-impossible-spike workflow validated by
regression scenarios. Operator UX polished (TUI affordances completed). Pydantic renderer shipping. Intent-layer fully
integrated across schemas. Full simulator scenario tier (full + nightly).

**Done when:** the simulator's full + nightly tiers pass; v2 is recommended for production use; deferred items have
analytics events flowing to support their eventual v2.x designs.

### Why the layering matters for v2 itself

The same argument that justifies bones-first for projects built *with* v2 justifies bones-first for v2 itself: agents
(and humans) building v2 will produce work that "passes its own tests but doesn't compose" if the integration spine
isn't validated early. Bones forces the integration test before MVP fleshes out features.

---

## Cross-doc dependency graph

```
                          ┌─────────────────────────┐
                          │  URI scheme extension   │
                          │  (foundation; blocks    │
                          │   contract refs, etc.)  │
                          └────────────┬────────────┘
                                       │
              ┌────────────────────────┼─────────────────────────┐
              │                        │                         │
              ▼                        ▼                         ▼
   ┌─────────────────┐      ┌─────────────────┐       ┌──────────────────┐
   │  Analytics      │      │  Pydantic       │       │  Intent-layer    │
   │  wiring         │      │  schemas (PO,   │       │  schema fields   │
   │  (foundation)   │      │  SA, VD, PM)    │       │  (problem→simp)  │
   └────────┬────────┘      └────────┬────────┘       └────────┬─────────┘
            │                        │                         │
            │       ┌────────────────┼─────────────────┐       │
            │       │                │                 │       │
            ▼       ▼                ▼                 ▼       ▼
   ┌─────────────────┐    ┌──────────────────┐   ┌──────────────────┐
   │ Synthetic       │    │ PO workflow      │   │ SA workflow      │
   │ operator        │    │ (L0→L1→L2→L3)    │   │ (architecture,   │
   │ (test infra,    │    │                  │   │  contracts,      │
   │ parallel)       │    │                  │   │  risks, cascade) │
   └────────┬────────┘    └────────┬─────────┘   └────────┬─────────┘
            │                      │                      │
            │                      └──────────┬───────────┘
            │                                 │
            │                                 ▼
            │                      ┌──────────────────┐
            │                      │  VD workflow     │
            │                      │  (parallel SA)   │
            │                      └────────┬─────────┘
            │                               │
            │              ┌────────────────┼────────────────┐
            │              │                │                │
            │              ▼                ▼                ▼
            │   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
            │   │  Dev environment │  │  PM workflow     │  │  Reviewer        │
            │   │  (service        │  │  (Planner +      │  │  federation      │
            │   │   isolation)     │  │   Coordinator)   │  │  (mechanical)    │
            │   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │            │                     │                     │
            │            └─────────────────────┼─────────────────────┘
            │                                  │
            │                                  ▼
            │                       ┌──────────────────┐
            │                       │  Dev + reviewer  │
            │                       │  loop runs       │
            │                       │  end-to-end      │
            │                       └────────┬─────────┘
            │                                │
            └────────── validates ───────────┤
                                             │
                                             ▼
                                  ┌──────────────────┐
                                  │  Quartermaster   │
                                  │  (after analytics│
                                  │   wiring solid)  │
                                  └──────────────────┘
```

**Critical path** (longest dependency chain):

1. URI scheme + Pydantic schemas (foundation)
2. Multi-level PO (L0→L1→L2→L3)
3. SA workflow
4. PM workflow
5. Dev + reviewer loop
6. Quartermaster (depends on analytics + PM)

**Parallel opportunities:**

- Synthetic operator can be built in parallel with everything once URI + analytics + schemas are in place — it just
  drives whatever exists.
- VD can run parallel with SA (both consume PO output, neither blocks the other).
- Dev environment can be built in parallel with PM (Coordinator integrates with it; design exists independently).
- Reviewer federation members can be built in parallel once the framework lands.
- Intent-layer schema fields integrate during the relevant Pydantic-schema work.

---

## Tracks

v2 work decomposes into 9 concurrent tracks. Each has its own internal sequence; inter-track dependencies are marked.

### Track A — Foundations

**Owner:** any. Lands first; everyone depends on it.

| #  | Deliverable                                                                                            | Files / docs                                              |
|----|--------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| A1 | Multi-authority URI scheme (`jig/uri/`)                                                                | `docs/v2.0/uri-scheme/design.md` §Implementation phases        |
| A2 | Pydantic schemas for v2 artifact types (PO, SA, VD, PM, dev_provisioning)                              | each design doc has the schema sketches                   |
| A3 | Intent-layer fields baked into all v2 schemas (problem / simplest_solution / complications_considered) | `docs/v2.0/agent-leverage/problem.md` item 1                   |
| A4 | Analytics wiring throughout existing emit sites (already-shipped event types from `jig/analytics/`)    | `docs/v2.0/analytics/problem.md` "What's still owed for v1 v2" |

**Blocks:** every other track depends on A1, A2 directly. A3 lands during the schema work. A4 enables the quartermaster.

### Track B — PO workflow

**Owner:** any. Replaces large parts of `init_workflow.py`.

| #  | Deliverable                                                                  | Notes                                                   |
|----|------------------------------------------------------------------------------|---------------------------------------------------------|
| B1 | L0 PO (pitch, problem, audience, product non-goals)                          | Replaces existing init PO; mostly extends current shape |
| B2 | L1 PO (5-phase journey discovery, persona walking, project ontology capture) | Largest single piece                                    |
| B3 | L1 conversation state + resume (`discovery.state.yaml`, playbacks)           | State persistence + greeting on resume                  |
| B4 | L2 PO (suite organization from capability roster)                            | Smaller; reads L1 output                                |
| B5 | L3 PO (suite briefs — extends existing simple-brief PO scoped to one suite)  | Mostly an extension                                     |
| B6 | Project ontology MCP tools + sidecar `.jig/spec/ontology.md` integration     | Used by every subsequent agent                          |
| B7 | New PO MCP tools (per `multi-level-spec/design.md`) + role configs           | ~15 new MCP tools across L0/L1/L2/L3                    |
| B8 | TUI affordances: `/init`, `/init --proceed`, `/suite *`, `/journey *`        | Slash commands + screen rendering                       |

**Depends on:** A1, A2, A3. **Blocks:** SA + VD (both consume PO output); dev + reviewer loop (need spec to dispatch
tickets against).

### Track C — SA workflow

**Owner:** any.

| #  | Deliverable                                                                             | Notes                                                         |
|----|-----------------------------------------------------------------------------------------|---------------------------------------------------------------|
| C1 | `architecture.yaml` schema + `modules/<m>/contracts.yaml` schema + shared contracts     | Pydantic models + sample fixtures                             |
| C2 | SA discovery loop (walk modules + integration boundaries)                               | Reuses the PO discovery-loop pattern                          |
| C3 | SA checklist enforcement in role prompt                                                 | Each module addresses every category or N/A                   |
| C4 | Behavioral contracts framework (precondition / postcondition / invariant / side-effect) | Highest-leverage type per design                              |
| C5 | Risk register + spike workflow (spike: true ticket type)                                | Including `dependent_contracts` field                         |
| C6 | Cascade-after-impossible-spike workflow                                                 | 5-step orchestration; transactional confirmation              |
| C7 | SA MCP tools + role config                                                              | `arch_add_module`, `arch_add_contract`, `arch_log_risk`, etc. |
| C8 | TUI affordances: `/sa risks`, `/sa open-questions`, `/sa review`                        | + cascade-confirmation screen                                 |

**Depends on:** A1, A2, A3, B (consumes PO output). **Blocks:** PM (build plan reads SA contracts); reviewer federation
(contracts are what reviewers check against).

### Track D — VD workflow

**Owner:** any. Runs parallel with SA.

| #  | Deliverable                                                                                                | Notes                                        |
|----|------------------------------------------------------------------------------------------------------------|----------------------------------------------|
| D1 | `frontend.yaml` schema + default minimal stack (HTMX + Alpine + custom utility CSS)                        | Operator override path                       |
| D2 | `system/` (tokens, components, brand) — defaults + Claude Design import path                               | Defaults always present                      |
| D3 | `wireframe.css` — hand-written ~200-300 lines of utility CSS with Tailwind-shaped names + greyscale tokens | Hand-curated once at VD discovery start      |
| D4 | VD discovery loop (3-stage: foundational decisions + screen roster + per-screen walk)                      | Reuses discovery-loop pattern                |
| D5 | Per-screen wireframe HTML format + linter (rejects inline styles, real colors, etc.)                       | Constrained vocabulary enforcement           |
| D6 | Browser index generator (`index.html` over per-screen HTMLs with state-toggle)                             | Operator opens locally                       |
| D7 | VD MCP tools: `wireframe_lint`, `wireframe_get/set_notes`, plus design-system import                       | Few thin wrappers; agent edits HTML directly |
| D8 | TUI affordances: `/design wireframes`, `/design serve`, `/design reimport`                                 | + browser-open helper                        |

**Depends on:** A1, A2, B (consumes PO output). **Blocks:** PM (UI tickets reference wireframes); reviewer federation
(visual_compliance reviewer).

### Track E — Dev environment

**Owner:** any. Runs parallel with C/D.

| #  | Deliverable                                                                                                                                      | Notes                                                                        |
|----|--------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| E1 | `dev_provisioning` block schema on `data_stores` in architecture.yaml                                                                            | Three strategies: shared+namespace / per_agent_ephemeral / operator_supplied |
| E2 | Manifest derivation: `architecture.yaml` → `.jig/dev/manifest.yaml` (+ docker-compose.yaml)                                                      | Generated, not authored                                                      |
| E3 | Per-agent namespace provisioning hooks (Postgres CREATE SCHEMA, NATS subject prefix, S3-compat bucket prefix, Redis key prefix, SQLite per-file) | The 4 cheap services + ephemeral fallback                                    |
| E4 | Connection-string injection into bwrap sandbox (env var map)                                                                                     | Agent never sees raw service config                                          |
| E5 | Cleanup hooks: `cleanup_on_success: drop`, `cleanup_on_failure: archive`                                                                         | Plus orphan tracking + periodic sweep                                        |
| E6 | External-API recorded fixtures (vcr.py-style): `replay_only` default + phase-gated `record_new`                                                  | For Shopify-style external deps                                              |
| E7 | TUI affordances: `/dev manifest`, `/dev archive list/drop/purge`, `/dev shell`                                                                   | Operator inspection / cleanup                                                |

**Depends on:** A1, C1 (provisioning blocks read from architecture.yaml). **Blocks:** parallel dispatch of agents (which
the federated reviewer assumes). Sequential dispatch fine without it.

### Track F — PM workflow

**Owner:** any.

| #   | Deliverable                                                                                                                            | Notes                                  |
|-----|----------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------|
| F1  | `build-plan.yaml` schema (epics × layers × tickets)                                                                                    | Living artifact                        |
| F2  | Ticket schema extensions (type, layer, dev_tier, reviewer_set, context_hints, risks_addressed, done_when)                              | Extends existing ticket store          |
| F3  | Planner PM agent (discovery loop walking capabilities → tickets)                                                                       | Senior or SA tier                      |
| F4  | Coordinator PM (mostly deterministic Python service + LLM-thin helpers for unstructured escalation + cross-ticket pattern detection)   | Continuous, event-driven               |
| F5  | Cycle dispatching (the "next batch of tickets" unit)                                                                                   | Replaces ad-hoc dispatch               |
| F6  | Auto-escalation thresholds (consecutive-failure, tool-call-flailing, no-commit-drift, out-of-budget, forced reflection every 20 turns) | Coordinator monitors                   |
| F7  | Mid-work tier promotion (restart with new tier, preserve worktree)                                                                     | Plus structured escalation report      |
| F8  | DEFERRED queue (`.jig/plan/deferred.jsonl`) + Planner triage at re-plan + slash commands                                               | Coordinator creates / Planner triages  |
| F9  | Estimation calibration loop (`EstimationCalibrationUpdated` events feed Planner sizing)                                                | Per-tier per-S/M/L bands               |
| F10 | Bones-first ordering enforcement + per-epic operator override + `cascade_risk_low` flag                                                | `BonesPromotedIncomplete` events       |
| F11 | TUI affordances: `/plan show/revise`, `/deferred review/promote/drop/merge`, `/plan unblock <epic>`, `/plan calibrate`                 | Visualization of cycles + layer status |

**Depends on:** A, B, C, D. **Blocks:** dev + reviewer loop (PM dispatches tickets).

### Track G — Reviewer federation

**Owner:** any. Runs parallel with E and F once schemas land.

| #   | Deliverable                                                                                                | Notes                                |
|-----|------------------------------------------------------------------------------------------------------------|--------------------------------------|
| G1  | Reviewer agent base + structured comment format (type, severity, contract_uri, confidence, suggested_diff) | Used by every reviewer               |
| G2  | Mechanical reviewers: contract-compliance, cross-cutting-policy, spec-compliance                           | Per-commit cadence + end-of-ticket   |
| G3  | Two-cadence integration: per-commit reviewer (mechanical only) + end-of-ticket reviewer federation         | Coordinator triggers both            |
| G4  | Auto-apply path for confidence-1.0 mechanical fixes                                                        | Skips operator confirmation          |
| G5  | Bounded fix loops (cap at 3 review→fix cycles, then escalate)                                              | Plus `BoundedFixLoopExhausted` event |
| G6  | Judgment reviewers: pattern-conformance, error-handling, test-adequacy                                     | End-of-ticket only                   |
| G7  | Specialty reviewers: visual-compliance, security, performance, architectural-review                        | Lands as projects need them          |
| G8  | Reviewer self-check before posting                                                                         | Cheap filter for false positives     |
| G9  | Lead-reviewer agent (orchestrator-side mechanical dedup in v2; semantic dedup in v2.x)                     | Mechanical first                     |
| G10 | Severity tiers (critical / important / notable) + DEFERRED-queue integration for notable                   | Operator override path               |

**Depends on:** A, C (reviewers check against contracts). **Blocks:** dev loop end-to-end validation (reviewers gate
ticket completion).

### Track H — Synthetic operator simulator

**Owner:** any. Runs parallel with everything once A1+A2 land. **Lands EARLY in v2 build per agent-leverage.**

| #   | Deliverable                                                                                     | Notes                                       |
|-----|-------------------------------------------------------------------------------------------------|---------------------------------------------|
| H1  | Scenario YAML schema + Pydantic validation                                                      | Drives everything else                      |
| H2  | Driver: spawn isolated daemon, play scenario, capture pass/fail report                          | First scenario can run                      |
| H3  | Synthetic-operator agent role + first persona (methodical)                                      | Cheap model + persona prompt                |
| H4  | Assertion framework (Pydantic union of 13 assertion kinds)                                      | Extensible; first kinds cover the smoke set |
| H5  | First smoke scenarios: PO L1 happy path, SA cascade happy, PM bones-first happy                 | Validates driver works                      |
| H6  | Per-run isolation (workspace + daemon + analytics tagging via `JIG_SIMULATOR=true`)             | EventEmitter already supports               |
| H7  | Additional personas: fast-and-shippy, scope-creeper, ambivalent, hostile                        | Persona × scenario matrix                   |
| H8  | Policy-driven turns (synthetic-operator agent generates response from persona + constraints)    | Beyond scripted turns                       |
| H9  | Coverage metrics (coverage_tags taxonomy + aggregate report)                                    | Surfaces untested paths                     |
| H10 | Realism budget (`/realism log`, gaps.jsonl, periodic-review affordance)                         | Tracks simulator-vs-reality drift           |
| H11 | CI integration (smoke = PR-blocking, full = nightly, nightly = weekly)                          | Cost-tiered                                 |
| H12 | TUI-driving mode (for scenarios that specifically test TUI behavior)                            | Opt-in; daemon API default                  |
| H13 | Regression scenario discipline (`regression-` prefix; CI fails if a regression scenario passes) | Bug fix lands with regression scenario      |

**Depends on:** A1, A2, A4 (analytics tagging). **Blocks:** nothing — but it validates everything else, so the bones
layer of every other track passes through the simulator before being declared done.

### Track I — Agent-leverage v2 items

**Owner:** any. Mostly small additions during other tracks.

| #  | Deliverable                                                                                                                | Notes                                 |
|----|----------------------------------------------------------------------------------------------------------------------------|---------------------------------------|
| I1 | Intent-layer fields integrated into all schemas (already in A3)                                                            | Cheap; ride along with each schema    |
| I2 | Quartermaster agent (continuous, reads analytics, produces operator briefings)                                             | Depends on A4 (analytics wiring)      |
| I3 | Pydantic-from-data-contract renderer (one renderer for v2)                                                                 | Depends on C1 (data contracts schema) |
| I4 | Corpus-building events for v2.x deferred items (already in events.py: `BugDiscoveredPostMerge`, `BoundedFixLoopExhausted`) | Lands during analytics wiring         |

**Depends on:** Track-specific (see notes per item). **Blocks:** v2.x design work later (item 4-5 designs need their
corpus-building events flowing in v2).

---

## Bones scope (the v2 walking skeleton)

**Goal:** one happy-path scenario goes end-to-end through every layer of v2 with the synthetic operator driving.

**Concrete bones deliverables per track** (the minimum from each track that participates in the bones tracer bullet):

| Track              | Bones deliverable                                                                                                                                                                                                                                                                                       |
|--------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A — Foundations    | A1 URI scheme parser (spec authority only, others stub) + A2 schemas for L0 / one-suite L3 / one-module SA / one-module PM / one ticket extension fields + A3 intent-layer fields (just on those schemas) + A4 analytics wiring on agent-spawn / ticket-state-changed / agent-completed (~3 emit sites) |
| B — PO             | B1 L0 PO writes pitch + audience + non-goals; B5 L3 PO writes a one-capability one-behavior brief (skip B2/B3/B4 for bones; manually create the L1 + L2 outputs the L3 needs)                                                                                                                           |
| C — SA             | C1 architecture.yaml + one-module contracts.yaml; C2 minimum SA loop (one module, one capability, one integration AC, no risks)                                                                                                                                                                         |
| D — VD             | Skip for bones (backend-only project)                                                                                                                                                                                                                                                                   |
| E — Dev env        | Skip for bones; agent runs against operator-shared sqlite                                                                                                                                                                                                                                               |
| F — PM             | F1 build-plan.yaml with one epic + bones layer + one ticket; F4 Coordinator dispatches the ticket (no Planner agent yet — operator manually creates the plan)                                                                                                                                           |
| G — Reviewer       | G2 contract-compliance reviewer only (mechanical, end-of-ticket); skip per-commit cadence for bones                                                                                                                                                                                                     |
| H — Simulator      | H1 scenario schema + H2 driver + H3 methodical persona + H4 the 4-5 assertion kinds the bones scenario needs + H5 the bones scenario itself + H6 per-run isolation                                                                                                                                      |
| I — Agent leverage | I1 intent-layer fields (covered in A3)                                                                                                                                                                                                                                                                  |

**Bones success criteria:**

- Synthetic operator drives scenario `bones-walking-skeleton.scenario.yaml` end-to-end:
  - Project init via `/init`, L0 pitch captured.
  - Operator manually walks past L1/L2/L3 with minimal one-suite/one-capability content.
  - Operator manually writes a 5-line architecture.yaml + 5-line contracts.yaml.
  - Operator manually writes a build-plan with one ticket.
  - Coordinator dispatches the ticket; dev agent implements it; contract-compliance reviewer passes; ticket resolves;
    merge.
  - Analytics events flow throughout: 1 `AgentSpawned`, 1 `TicketStateChanged`, 1 `AgentCompleted`, 1
    `ReviewCommentPosted`, etc.
- Assertion suite passes; `simulator: true` events isolated from any real analytics.
- Total scenario cost under $1; runtime under 10 minutes.

**What bones explicitly skips:**

- Real L1 discovery (5-phase conversation) — operator pre-populates the artifacts manually
- VD entirely (backend-only project)
- Dev environment isolation (operator-shared sqlite, no namespace primitive)
- Federated reviewer federation (one reviewer only)
- Per-commit cadence (only end-of-ticket)
- Auto-escalation, mid-work tier promotion, cascade workflows, calibration loop
- Quartermaster, Pydantic renderer, intent-layer enforcement
- Personas beyond methodical
- Policy-driven turns; scripted only

These all flesh out in MVP.

---

## MVP scope

**Goal:** real medium-sized project (15-25 capabilities, 4-6 suites) ships through v2 end-to-end.

**Per-track MVP additions on top of bones:**

| Track              | MVP additions                                                                                                                                                                                           |
|--------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A — Foundations    | All authority resolvers (`spec`, `arch`, `design`, `plan`, `store`); path-style fragments; revision pinning; full Pydantic schemas across all v2 artifact types                                         |
| B — PO             | Full L1 5-phase discovery; L1 state + resume; L2 suite organizer agent; L3 PO scoped per suite; project ontology MCP tools; full slash commands                                                         |
| C — SA             | Full SA discovery loop with checklist enforcement; behavioral contracts framework; risk register + spike workflow; cascade-after-impossible (without all the failure-mode mitigations)                  |
| D — VD             | Full VD workflow for UI projects: frontend.yaml + design system + wireframes + browser index; default stack; Claude Design import path; visual_compliance reviewer (basic)                              |
| E — Dev env        | Postgres schema namespacing (the most common case); manifest derivation; provisioning + cleanup hooks; orphan tracking; no per-agent ephemeral yet                                                      |
| F — PM             | Planner PM agent (discovery loop); Coordinator dispatching cycles; auto-escalation thresholds (with conservative defaults); DEFERRED queue with Planner triage                                          |
| G — Reviewer       | Mechanical reviewers (contract-compliance, cross-cutting-policy, spec-compliance) + judgment reviewers (pattern-conformance, error-handling, test-adequacy); two-cadence; auto-apply; bounded fix loops |
| H — Simulator      | Smoke tier of scenarios (~10); methodical + fast-and-shippy + ambivalent personas; coverage report; realism-budget logging; CI integration on smoke tier                                                |
| I — Agent leverage | Intent-layer enforcement (reviewer flags thin sequences); quartermaster (after analytics wiring solid); Pydantic-from-data-contract renderer                                                            |

**MVP success criteria:**

- An operator can drive an ATS-sized project (15-25 capabilities, 4-6 suites) through PO discovery → SA architecture →
  PM planning → dev + reviewer loop → bones-first across all epics → at least one full epic through MVP layer.
- Smoke tier of synthetic operator scenarios passes consistently.
- Quartermaster produces a useful weekly briefing from real-project analytics.
- Cascade-after-impossible-spike workflow validated by a regression scenario.

**What MVP explicitly skips:**

- Specialty reviewers (security, performance, architectural-review)
- Per-agent ephemeral provisioning
- Continuous-shadow adversarial pairing (deferred to v2.x)
- Full TUI polish for SA confirmation UX (Q6 deferred)
- All v2.x items

---

## Final scope

**Goal:** v2 is recommended for production use; deferred items have analytics events flowing.

**Per-track Final additions on top of MVP:**

| Track              | Final additions                                                                                                                                                                                                                               |
|--------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| A — Foundations    | Caching with event-driven invalidation; full Pydantic field validators; programmatic URI constructors per artifact kind                                                                                                                       |
| B — PO             | TUI affordances polished; resume-from-state edge cases; project-ontology operator-edit affordances                                                                                                                                            |
| C — SA             | Full cascade workflow with all four failure-mode mitigations; cascade audit trail viewer; SA-suggested cascade_risk_low for bones promotion                                                                                                   |
| D — VD             | Visual compliance reviewer at full sensitivity (vision-based screenshot diff); accessibility reviewer (WCAG AA at Final layer); responsive-design enforcement                                                                                 |
| E — Dev env        | Per-agent ephemeral support (SQLite, opinionated CLIs); external-API fixture recording in record_new mode; orphan sweeper with operator-confirmation                                                                                          |
| F — PM             | Full mid-work tier promotion mechanics; estimation calibration loop running; bones-first override + cascade_risk_low integration; full cycle visualization in TUI                                                                             |
| G — Reviewer       | Specialty reviewers (security, performance, architectural-review); reviewer self-check before posting; severity-tier policy (critical = block, important = consult SA, notable = DEFERRED); structured comment + suggested-diff format polish |
| H — Simulator      | Full + nightly tiers complete; all 5 personas; policy-driven turns; coverage reaching ≥80% of identified workflow paths; regression scenario discipline established (every bug lands with one); TUI-driving mode for TUI-specific scenarios   |
| I — Agent leverage | All intent-layer reviewer enforcement landed; quartermaster prompt-tuning loop based on operator feedback; Pydantic renderer used by SA contract authoring                                                                                    |

**Final success criteria:**

- v2 can be recommended to anyone running medium-sized projects.
- Synthetic operator full + nightly tiers pass nightly without flake.
- Realism-budget metric trending stable or down (simulator keeping up with reality).
- All deferred items (adversarial pairing, ensemble, additional renderers, heuristics, full agent-driven testing) have
  their corpus-building events flowing so v2.x designs can land informed by real data.

---

## Critical path

The longest dependency chain — what blocks the most other things:

1. **A1 URI scheme + A2 Pydantic schemas + A3 intent-layer fields + A4 analytics wiring** (Track A foundations).
   Everything depends on these. Sequence: URI parser first, schemas next (in parallel: PO schemas / SA schemas / etc.),
   intent-layer fields ride along, analytics wiring lands last in the foundation phase.
2. **B1+B5 L0/L3 PO** + **C1+C2 architecture.yaml + module contract** (the minimum spec + arch needed for the bones
   tracer bullet).
3. **F1+F2+F4 build-plan + ticket + Coordinator** (dispatch).
4. **G2 contract-compliance reviewer** (validates the ticket against the contract).
5. **H1+H2+H5 driver + first scenario** (validates the spine).

Bones success = (1) → (2) → (3) → (4) all work end-to-end and (5) confirms it.

After bones, the tracks can fan out and parallelize. Critical path through MVP is roughly:

- B (full PO workflow) → C (full SA workflow) → F (Planner PM) → G (full reviewer set) → bones-first across all epics
- Parallel: E (dev env), D (VD if UI), H (simulator scenarios growing)

Critical path through Final is mostly per-track polish + the longer-tail items; less inter-track sequencing.

---

## Parallelism opportunities

- **A1 + A2 + A3 + A4** can be split across people once they're scoped — different schemas don't conflict.
- **B vs C vs D** can run parallel after the schemas land. PO output is consumed by SA + VD; both can start once L0/L1
  land.
- **E (dev env) is fully parallel** with B/C/D — only depends on schemas being defined.
- **H (simulator)** is fully parallel — drives whatever exists; scenarios accumulate as features land.
- **G (reviewers)** can be built in parallel — each reviewer is a separate agent role.
- **F (PM) needs B + C + D mostly done** before its Planner can do meaningful work. Coordinator can start earlier with
  manual plans.

For a single operator implementing v2 alone (the realistic case), parallelism is mental-task-switching rather than
literal parallel work. The dependency graph still tells you what to build first.

---

## Milestones / gates

### Bones-done gate

- **Trigger**: H5 (the bones scenario) passes consistently in synthetic operator.
- **Operator confirms**: review the bones scenario report; spot-check the artifacts produced; declare bones complete.
- **Unlocks**: MVP work fans out across tracks.
- **Estimated when**: depends on actual implementation pace; doc says "v2 phase 1" loosely.

### MVP-done gate

- **Trigger**: ATS-sized project successfully driven through v2 end-to-end (real or via simulator full tier); smoke tier
  passes consistently in CI; quartermaster produces useful briefings.
- **Operator confirms**: hands a real project through, reports back.
- **Unlocks**: Final polish, recommend for use.

### Final-done gate (v2 ships)

- **Trigger**: full + nightly simulator tiers pass; all reviewer types in production use; all deferred items have their
  corpus events flowing; operator UX polish complete.
- **Operator confirms**: declares v2 ready.
- **Unlocks**: v2.x design work for the deferred items begins (with corpus available).

---

## Risks

- **Synthetic operator infra slips, leaving everything else without validation.** Mitigation: H1+H2+H5 are part of the
  bones layer — they MUST land for bones to be declared done. If the simulator slips, bones gates can't pass.
- **Schema thrash during build.** Pydantic schemas designed in A2 prove wrong as they meet implementation reality;
  cascading rewrites. Mitigation: build the bones tracer bullet against the schemas before fanning out; bones success
  validates the schemas are workable; later schema changes are amendments, not rewrites.
- **Coordinator PM proves harder than "mostly deterministic + LLM-thin helpers."** If the LLM helpers turn out to need
  more reasoning capability than expected, the cost ceiling on Coordinator goes up. Mitigation: prototype Coordinator
  early (during bones); measure helper-invocation rate; if too high, redesign before MVP fan-out.
- **Reviewer federation false-positive rate is high.** Mechanical reviewers fire too often; agents drown in comments.
  Mitigation: bones layer ships with one reviewer only; expand carefully; auto-apply path catches high-confidence
  mechanical fixes without operator intervention; analytics on operator-override patterns drive reviewer prompt tuning.
- **VD wireframe quality is poor at agent-author time.** Agent-written HTML wireframes look ugly or structurally wrong;
  operator iteration cycles dominate. Mitigation: start with the simplest scenarios (single-screen wireframes) before
  scaling to multi-screen; iterate the wireframe.css + linter to catch the worst patterns.
- **Service isolation reveals concurrency bugs in v2 itself.** Parallel dispatch surfaces races we missed in design.
  Mitigation: bones layer ships with sequential dispatch only; parallel dispatch lands during MVP only after isolation
  primitives are battle-tested in single-agent runs.
- **Operator UX is bad enough to make v2 worse than v1.** Possible if TUI design lags or the conversation flows feel
  wrong. Mitigation: synthetic operator with hostile/ambivalent personas catches the worst patterns; operator-feedback
  retros at MVP gate; scope reduction allowed if specific UX paths are intractable.
- **Cost overruns**. Federated reviewer + simulator + per-tier dispatch could blow the LLM budget. Mitigation: cost
  ceiling on synthetic-operator scenarios; tier-cost telemetry from analytics; bones-first by design keeps early cycles
  cheap; full federation only at MVP gate.

---

## Out of scope for v2 (deferred to v2.x)

Per `docs/v2.0/agent-leverage/problem.md` v2/v2.x split + design-doc deferred sections:

- **Adversarial pairing (skeptic shadow)** — defer to v2.x; v2 captures `BugDiscoveredPostMerge` +
  `BoundedFixLoopExhausted` corpus events so the design lands informed by real escape data.
- **Ensemble decision-making** — defer to v2.x; v2 captures `OperatorOverride` + `AutoEscalationTriggered` corpus
  events.
- **Additional translation renderers** (OpenAPI, SQL DDL, GraphQL SDL, sequence diagrams) — defer; build per renderer
  when project pain materializes.
- **Heuristics layer** (`docs/v2.0/learnings/`) — defer to v2.x; revisit trigger is 3+ medium projects shipped.
- **Full agent-driven testing framework** beyond the synthetic operator (`docs/v2.0/agent-testing/`) — defer; revisit trigger
  is the synthetic operator running for a meaningful corpus and a specific kind earning its way in.
- **Continuous-shadow adversarial pairing** (the second flavor of item 4) — defer behind periodic-checkpoint.
- **Full reviewer self-check loop** at scale — basic version in v2; full version in v2.x.
- **TUI design refinements** for SA confirmation UX (Q6 deferred to TUI design phase).
- **Multi-agent SA in parallel** (one SA agent at a time in v2; parallel SAs deferred).
- **Cross-project URI references** (each jig project self-contained in v2).
- **LLM-generated scenarios** for synthetic operator (hand-curated in v2).
- **Visual companion** beyond browser-open (operator opens files manually in v2; richer companion deferred).
- **Cascade-confirmed-impossible UI affordances** beyond the structured trace + enumerated options (operator reads the
  trace in v2; richer cascade visualization deferred).

---

## Implementation phases (top-level summary)

| Phase | Tracks | Goal | Estimated milestone |
|---|---|---|---|
| **v2 phase 1 (bones)** | A1-A4 + B1+B5 + C1+C2 + F1+F2+F4 + G2 + H1+H2+H5+H6 | Walking skeleton: synthetic operator drives one bones scenario end-to-end | Bones-done gate |
| **v2 phase 2 (MVP)** | All tracks fan out to MVP scope | Real medium-sized project ships through v2 | MVP-done gate |
| **v2 phase 3 (Final)** | All tracks complete to Final scope | v2 ready for production use | Final-done gate (v2 ships) |
| **v2.x** | Deferred designs land | Adversarial pairing, ensemble, additional renderers, heuristics, etc. | Per-design revisit triggers |

---

## What this plan does NOT specify

- **Wall-clock estimates.** Designed to be implementation-pace-agnostic; estimate when track work begins.
- **Person assignments.** Single operator (you) executing; tracks are mental-task-switching units.
- **Per-track detailed file lists.** Each track's design doc + the gap analysis carry the file-level breakdown.
- **Specific commit / PR cadence.** Standard practice (frequent, atomic commits per track); no ceremony.
- **Test strategy beyond "synthetic operator validates workflow + pytest validates code."** Adopt-as-you-go.

## Change log

- 2026-05-03: Initial plan (brent + claude). Synthesizes 13 design docs + gap analysis into 9 tracks + bones/MVP/Final
  layering applied to v2 itself + critical-path identification + parallelism opportunities + milestone gates + risks +
  explicit v2/v2.x split. Bones layer scoped concretely (one happy-path scenario end-to-end through every layer of v2
  with one reviewer agent). Synthetic operator simulator mandated as part of bones (validates the spine).
