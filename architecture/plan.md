---
title: Jig Architecture Migration — Implementation Plan
type: plan
status: draft
owner: brent-hoover
created: 2026-06-25
updated: 2026-06-28
design: ./jig-instance.md
---

# Jig Architecture Migration — Implementation Plan

## Overview

Brownfield migration of Jig's codebase from its current structure (god-object
`orchestrator.py` at 4543 lines, `mcp_server.py` at 3374 lines, scattered schemas,
magic-string bus topics, duplicate `OntologyTerm`) to the target architecture
defined in `model.md` and `jig-instance.md`: a layered system of CORE Model →
Substrate → Agent Runtime → Engines → EDGE, coordinated via `project://` store
authorities and a typed Bus, with a pure `decide()` state machine at the heart
of the Build engine.

The migration follows a bones-first progression: stand up the target boundaries
and seams first, flow one thin happy path end-to-end through the new structure
(with logic shimmed to old code where needed), then migrate real logic behind
each boundary epic-by-epic. The bones acceptance test is the evaluability
driver: the code-pipeline and review loop run headless on a fixture, without the
daemon/TUI, using fake agents.

This plan is throwaway — it exists to coordinate the migration, not to document
the system. The architecture lives in `model.md` and `jig-instance.md`.

## Absorbed feature-work docs

This plan supersedes the following `feature-work/` problem statements, whose
requirements are folded into the epics below. Each row maps the superseded doc
to the **exact epic and phase** that absorbs it, so a contributor recovering
intent (e.g. for "Project Onboarding" or "SA as Architect") lands on the precise
task list without re-reading the old doc.

| Superseded doc | Absorbed into | Phase(s) | Key absorbed requirements |
|---|---|---|---|
| `larger-projects/problem.md` | Epic 6 | MVP 1–5, Final 3–4 | SAU role unification, TB planning phase, graph tools, derisker ordering, TracerSpec integration, medium eval |
| `sa-architect/problem.md` | Epic 6 | MVP 2 | Grounded-decision protocol (Context7 + WebFetch) preserved in unified SAU |
| `architecture-skeleton/problem.md` | Epic 6 | MVP 2 | Full artifact set: modules, contracts, boundaries, data ownership |
| `module-boundaries/problem.md` | Epic 5 | MVP 4–5 | Mechanical (non-LLM-adjudicated) boundary + vocabulary enforcement |
| `medium-l0-l3-pipeline/problem.md` | Epic 6 | Bones 4 | Discovery interview replaces the v1/v2 L0-L3 split |
| `project-onboarding/problem.md` | Epic 6 | MVP 6 | Onboarding behind `project://spec/` (`engines/discovery/onboard.py`) |
| `jig-init-process/problem.md` | Epic 6 | Bones 4 | Init conversation becomes the Discovery interview |

All docs above are marked `status: superseded` with
`superseded_by: ../../architecture/plan.md`. Phase numbers refer to the
numbered task lists under each epic below (e.g. "MVP 4–5" = items 4 and 5 of
that epic's **MVP** list).

## Preconditions

- [x] Phase 3 architecture approved (see `jig-instance.md` → Phase 3)
- [x] `project://` URI scheme exists (`jig/ownership.py`) — store authorities are
      not greenfield
- [x] Bus exists (`jig/store/bus.py`) with topic-based publish/subscribe — typed
      events are an upgrade, not a new system
- [x] JSONL store layer exists (`jig/store/core.py`) — substrate is an extraction,
      not a build
- [x] Agent spawn/sandbox exists (`jig/agent.py`, `jig/sandbox.py`) — Agent
      Runtime seam is a contract extraction, not a new capability
- [ ] Spikes resolved (see Spikes section — at minimum the `decide()` purity
      spike must be attempted before Build MVP, but bones can start without it)
- [ ] Operator confirms the bones-first progression and the epic ordering

## Current state (the gap)

The target architecture has 5 layers and 7 engine suites. The current codebase
maps to it as follows — this is the migration gap:

| Target layer/suite | Current code | Gap |
|---|---|---|
| CORE Model | `jig/schemas/arch.py` (1250), `jig/schemas/po.py` (705), `jig/ticket.py`, `jig/thread.py` | Scattered across schemas/ + ticket.py + thread.py; not a single pure module; `OntologyTerm` duplicated in arch.py and po.py; no pure invariant functions |
| Substrate: Store | `jig/store/` (16 modules), `jig/ownership.py` | Exists but not behind `project://` authorities as a unified interface; stores are per-type, not authority-grouped |
| Substrate: Bus | `jig/store/bus.py`, `jig/events.py` | Magic-string topics (`"orchestrator"`); no typed event schema |
| Agent Runtime | `jig/agent.py` (1113), `jig/sandbox.py`, `jig/container.py`, `jig/mcp_server.py` (3374) | No `RunAgent` contract; agent spawn is coupled to orchestrator + MCP; no substitutable seam for fakes |
| Build engine | `jig/orchestrator.py` (4543), `jig/coordinator.py` (726), `jig/deadlock.py`, `jig/stall_detector.py` | God-object; no pure `decide()`; dispatch/effects/logic entangled; supervisor not separated |
| Enforcement | `jig/boundary_rules.py`, `jig/checks.py`, `jig/check_runner.py`, `jig/reviewers/` (1215+644) | Exists as scattered checks + reviewer federation; not a clean library Build invokes; not headless-invocable |
| Discovery | `jig/init_workflow.py` (2697), `jig/onboard_workflow.py` (1658), `jig/po_*.py` (4 MCP modules), `jig/spec_generator.py` | Init + onboard + PO MCPs are one tangled workflow; not behind a `project://spec/` authority |
| Architecture | `jig/sa_mcp.py`, `jig/sa_incremental_mcp.py` (1564), `jig/init_mcp.py` | Three SA roles (sa, sa_mvp, sa_v2); not unified; cascade governance not extracted |
| Reconciliation | (mostly missing) | No derive-from-code; no drift detection; new static-analysis dependency |
| Evaluation | `jig/eval/`, `jig/evals/`, `jig/sim/` | Exists but not headless; not driving Build on fixtures; synthetic-operator simulator is separate |
| EDGE | `jig/cli.py` (3119), `jig/ws_server.py` (794), `jig/tui/`, `jig/daemon.py` | TUI/CLI/daemon exist but are thick; persona variation not localized; internals are directly accessed |

## Spikes

Three registered spikes (from `jig-instance.md` → Phase 4). Bones can start
without them, but each must be resolved before its epic's MVP.

### Spike 1: Can `decide()` be genuinely pure under the async SDK?

- **Slot before:** Build MVP (Epic 4)
- **Question:** The Claude Agent SDK is async/streaming. Can a pure
  `decide(state, event) -> (next_state, actions)` function work when actions
  include "spawn agent" (inherently async)? Or does the state machine need an
  async variant?
- **Approach:** Write a minimal `decide()` that handles one ticket transition
  (e.g. `ready → in_progress`) and returns an action like
  `SpawnAgent(ticket_id, role)`. Test it with synthetic events. If the action
  is a dataclass (not a coroutine), purity holds — the shell executes it
  asynchronously. If `decide` must `await` to decide, purity breaks and we need a
  different seam.
- **Time-box:** One session.
- **Resolved (#201):** **Purity holds.** A synchronous
  `decide(state, event) -> (next_state, actions)` handles `OPEN → IN_PROGRESS`
  and returns an inert `SpawnAgent` dataclass; an async dispatch shell executes
  it. `decide` never awaits, never mutates input state, and is deterministic.
  The Build seam is functional-core (`jig/engines/build/decide.py`, pure) /
  imperative-shell (`dispatch.py`, async). Proof + tests landed in
  `jig/engines/build/` ahead of Epic 4 bones.

### Spike 2: Static-analysis approach for Reconciliation

- **Slot before:** Reconciliation MVP (Epic 7)
- **Question:** How do we derive the actual import/dependency graph from Python
  code? `grimp`? `ast` walk? Something else?
- **Approach:** Try `grimp` against Jig's own codebase. If it produces a usable
  module graph, compare it to the declared architecture. If `grimp` is too
  opinionated about package layout, fall back to a custom `ast`-based importer.
- **Time-box:** One session.
- **Resolved (#202): use grimp.** Empirically, grimp built a 318-module graph of
  `jig/` in ~0.03s with no package-layout complaints, and correctly resolved the
  `from pkg import module` module-vs-name ambiguity a naive `ast` walk can't
  (without reimplementing module resolution). grimp is the import-graph engine
  behind import-linter — purpose-built for "actual structure from code" and
  reusable by Enforcement's boundary checks. Footprint is negligible: its only
  requirement, `typing-extensions`, is already a Jig dependency. `ast` fallback
  not needed. `derive_actual_graph` is now implemented in
  `jig/engines/reconciliation/derive.py` (this also delivers Reconciliation MVP
  task 1, #221); the dogfood drift run + surfacing drift as tickets remain in
  #221.

### Spike 3: Agent record/replay for fixture-based evals

- **Slot before:** Agent Runtime seam MVP (Epic 3) and Evaluation (Epic 8)
- **Question:** Can we record a real agent's streaming output and replay it
  through the `RunAgent` seam as a fixture?
- **Approach:** Record one `claude-agent-sdk` run (capture the stream events).
  Build a `RecordedAgent` impl of `RunAgent` that replays them. Verify a
  downstream consumer (the check runner) can't tell the difference.
- **Time-box:** One session.
- **Resolved (#203): yes.** An agent's streaming output is the sequence of
  `JigEvent`s it emits to an `EventEmitter` during the run; its final state is
  the `AgentRunResult`. A `Recording` captures both, serializably (persistable as
  a fixture). `record(make_run_agent, ctx)` wraps any `RunAgent` — incl.
  `RealRunAgent`, so `record(lambda em: RealRunAgent(emitter=em), ctx)` records a
  real run — with a capturing emitter. `RecordedRunAgent` re-emits the recorded
  events to an emitter then returns the recorded result, so a consumer subscribed
  to the emitter observes an **identical stream and result** (proven by a
  fidelity test: live vs replay observations are equal). Replay reproduces event
  order/content, not wall-clock timing — what fixture-based evals want.
  Implemented in `jig/runtime/recorded.py` (this also delivers Agent Runtime MVP
  task 1, #217). Capturing a real live run is an operator action; the eval
  corpus of recorded runs lands in #222.

## Epic dependency matrix

Most epic **Bones** are deliberately decoupled: each stands up a boundary/seam
with logic shimmed, so they can proceed largely in parallel (the global gate is
that all bones land before any MVP — see the bones acceptance test). Two caveats
to "independent":
- **Epic 8 Bones is the full-composition exception** — its harness wires Epics
  1, 3, 4, 5 together, so it must land after those bones exist (it does not touch
  Epic 2 / `StoreAuthority` — see the bones-gate scope note).
- **Epics 5 and 7 Bones each carry a narrow Epic 1 contract dependency** — they
  import model-layer types (`Finding`, the dependency-graph types), so Epic 1's
  bones must define those before Epic 5/7 bones compile. This is a type-contract
  dependency, not a full composition like Epic 8.

**MVP** work is where the remaining cross-epic contracts bind. The
matrix below makes the ordering explicit so contributors don't build against an
incomplete contract or duplicate ownership.

| Epic | Bones depends on | MVP depends on | Blocks (downstream) |
|---|---|---|---|
| 1 — CORE Model | — | — | 2, 4, 5, 7, 8 (all consume `Finding`/entities) |
| 2 — Substrate | — | Epic 1 (entities) | 4 (typed events), 6 (`project://` authorities) |
| 3 — Agent Runtime | — | Spike 3 | 8 (`FixtureRunAgent`) |
| 4 — Build engine | — | Epic 1, Epic 2 (typed events), Spike 1 | 7 (drift→tickets), 8 (headless Build) |
| 5 — Enforcement | Epic 1 (`Finding`) | Epic 1 | 4 (Build invokes `Review`), 8 (headless Enforcement) |
| 6 — Authoring | — | Epic 1, Epic 2 (authorities) | — (terminal for this migration) |
| 7 — Reconciliation | Epic 1 | Spike 2, Epic 4 (surfaces drift via Build) | — |
| 8 — Evaluation | Epics 1, 3, 4, 5 Bones | Epics 3, 4, 5, 1 | — (terminal; the bones acceptance gate) |
| 9 — EDGE | — | all engines (daemon API over them) | — (terminal) |

Reading: a row's **MVP depends on** entries must reach at least MVP before that
row's MVP can wire real behavior. Epics 6, 8, 9 are terminal — nothing depends
on them, so they can land last. Epic 1 is the universal prerequisite.

## Epics (dependency order)

Each epic has three phases: **Bones** (tracer bullet — boundary + seam, logic
shimmed), **MVP** (real logic migrated behind the boundary), **Final** (edge
cases, full coverage). Bones for all epics should land before any epic's MVP.

Every epic below carries an explicit **Goal** (one-line intent), its phased task
lists, and a **Verify** block that doubles as the epic's acceptance gate — so the
plan is implementable from the repo alone, with GitHub issues as links rather
than required context.

---

### Epic 1 — CORE Model

**Goal:** A pure, I/O-free `jig/model/` holding the Living-Invariant entities and
the five invariant functions, with `OntologyTerm` defined exactly once.
Foundational — everything depends on it.

**Bones:**
1. Create `jig/model/` package (pure, no I/O imports).
2. Move `OntologyTerm` to `jig/model/ontology.py` (single definition).
3. Update `jig/schemas/arch.py` and `jig/schemas/po.py` to re-export from
   `jig/model/ontology.py` (backwards-compatible shim — the duplicate definitions
   become aliases).
4. Define the 5 invariants as pure function signatures in
   `jig/model/invariants.py` (stub bodies, not yet implemented):
   - `coverage(model) -> list[Finding]`
   - `conformance(model, code) -> list[Finding]`
   - `containment(model, code) -> list[Finding]`
   - `vocabulary(model) -> list[Finding]`
   - `ownership(model) -> list[Finding]`
5. Move `Ticket` and `Thread` domain types to `jig/model/ticket.py` and
   `jig/model/thread.py` (re-export from current locations for compat).

**MVP:**
1. Implement the `coverage` invariant (orphan capability/contract/journey
   detection) as a pure graph query over trace edges.
2. Implement the `containment` invariant (boundary dependency check) as a pure
   graph query.
3. Implement `vocabulary` (one concept, one home — count concepts with >1
   definition).
4. Migrate consumers to import from `jig/model/` directly. Leave the re-export
   shims in place — shim *removal* is a Final task, not MVP (see shim-removal
   policy).

**Final:**
1. Implement `conformance` (requires code analysis — may defer to
   Reconciliation).
2. Implement `ownership` (every Boundary has exactly one owner).
3. Remove all re-export shims.

**Verify:** `pytest tests/` passes (existing 346 test files). New
`tests/model/test_invariants.py` covers the pure functions.

---

### Epic 2 — Substrate (Store + Bus)

**Goal:** The store layer reachable through unified `project://` authorities and
the Bus carrying typed events instead of magic-string topics — a substrate the
engines depend on without knowing the JSONL backing.

**Bones:**
1. Create `jig/substrate/` package.
2. Define `StoreAuthority` — a unified read/write interface for
   `project://spec/`, `project://arch/`, `project://design/`, `project://plan/`,
   `project://store/`. Bones **defines, parses, and routes the facade only**:
   `read` delegates to the existing resolver for wired authorities and raises
   `UnimplementedAuthorityError` for the rest; **all** `write` calls raise until
   MVP (the URI is still parsed first, so malformed URIs fail fast). No new
   persistence — MVP routes the existing JSONL stores through it.
3. Define `TypedEvent` schema (pydantic) for bus events. Map existing
   magic-string topics (`"orchestrator"`, `"tickets.{id}"`) to typed equivalents.
   The bus-scoped lifecycle events are `TicketCreated` and `TicketUpdated`
   (status transitions) — faithful to `jig.ticket_events._build_payload`.
   Completion/failure are `TicketUpdated` status transitions; the operator-facing
   relay (the emitter channel the TUI reads) is a separate, later concern, not a
   new bus event type.
4. Add a typed-event adapter in the Bus that accepts both old string topics and
   new typed events (compatibility layer).

**MVP:**
1. Route all store access through `StoreAuthority` (existing stores become
   internal impls). **Routing model: see
   `../docs/reference/adr-0001-runtime-store-access-routing.md` (ADR-0001)** —
   runtime uses typed `StoreAuthority` ports; `project://` URIs are the
   cross-boundary serialization surface delegating to the same stores.
2. Migrate orchestrator's bus publishes from `"orchestrator"` string to typed
   events.
3. Migrate bus subscribers to consume typed events.

**Final:**
1. Remove the string-topic compatibility layer.
2. Delete `jig/events.py` if fully superseded.

**Verify:** `pytest tests/` passes. New `tests/substrate/` is the **Substrate
composition test** — the dedicated home for validating the seam the bones
acceptance gate deliberately does *not* cover: `read` routes to the right
authority and raises `UnimplementedAuthorityError` for unwired ones, `write`
raises until MVP, and typed events round-trip. Bus tests (`tests/test_bus.py`,
`tests/test_bus_recent.py`) pass unchanged through the compat layer, then
updated.

---

### Epic 3 — Agent Runtime seam

**Goal:** A `RunAgent` contract that abstracts agent spawn/sandbox/MCP lifecycle,
with real/recorded/fixture implementations — the substitutable seam that makes
headless, fixture-driven evals possible.

**Bones:**
1. Create `jig/runtime/` package.
2. Define `RunAgent` contract:
   ```python
   class RunAgent(Protocol):
       async def __call__(self, ctx: AgentRunContext) -> AgentRunResult: ...
   ```
   Where `AgentRunContext` carries ticket, role, worktree, MCP config.
   `AgentRunResult` carries stream events, exit status, artifacts.
3. Wrap `jig.agent.run_agent` in `RealRunAgent` — a **wrapper seam**, not a
   physical extraction: `RealRunAgent` delegates to the existing entangled
   spawn + sandbox + MCP path. Real extraction of that logic into the runtime is
   MVP/Final work (see below), not Bones.
4. Add `FixtureRunAgent` (returns canned `AgentRunResult` — the bone for
   headless evals).

**MVP:**
1. Resolve Spike 3 (record/replay). Add `RecordedRunAgent`.
2. Route all agent spawns through `RunAgent` (orchestrator calls the seam, not
   `agent.py` directly).
3. Extract per-agent MCP server lifecycle from `mcp_server.py` into the runtime.
   `mcp_server.py` becomes registration/wiring only.

**Final:**
1. Sandbox/container lifecycle as runtime-internal.
2. Full `RecordedRunAgent` with streaming replay.

**Verify:** `pytest tests/` passes. New `tests/runtime/` covers the seam with
all three implementations. Existing agent tests
(`tests/test_agent_*.py`) pass through `RealRunAgent`.

---

### Epic 4 — Build engine (the god-object fix)

**Goal:** `orchestrator.py` split into a pure `decide()` state machine + an async
dispatch/effects shell + a supervisor — the highest-risk, highest-coupling epic,
and the precondition for headless code-pipeline evals.

**Bones:**
1. Create `jig/engines/build/` package.
2. Extract `decide(state, event) -> (next_state, actions)` as a pure function
   in `jig/engines/build/decide.py`. Start with one ticket lifecycle
   (e.g. `ready → in_progress → review → merged`). The state machine is
   data-driven (states + transitions as data, not if/else chains).
3. Extract the dispatch/effects shell in
   `jig/engines/build/dispatch.py` — the part that executes `decide()`'s actions
   (spawn agent, run check, merge worktree). The shell is the only async part.
4. Extract `Supervisor` in `jig/engines/build/supervisor.py` — deadlock/stall
   detection that emits events into `decide()` (never mutates tickets
   directly). Based on existing `jig/deadlock.py` and `jig/stall_detector.py`.
5. Wire the thin coordinator (`decide()` + `dispatch` + `supervisor` + bus
   subscription) and exercise it with **synthetic events in tests only**. The
   one-transition bones `decide()` must **not** own real production dispatch:
   the live `orchestrator.py` keeps its existing dispatch path, and the facade
   delegates only the migrated transition. Routing production traffic through the
   state machine is Epic 4 MVP work, gated on the transition/idempotency design
   (see Seam stubs & post-bones blockers). This keeps Bones synthetic-only and
   avoids an incomplete machine taking real side effects.

**MVP:**
1. Resolve Spike 1 (`decide()` purity). If pure works, migrate all ticket
   transitions to the state machine.
2. Migrate review federation orchestration into the Build engine (currently
   in orchestrator's `_run_review_federation`).
3. Migrate dependency resolution (`_unblock_dependents`,
   `_cascade_fail_dependents`) into `decide()` transitions.

**Final:**
1. Migrate sad-path transitions (blocked, needs-info, review-failed,
   merge-conflict, replan).
2. Migrate the full escalation/replan flow.
3. Remove the `orchestrator.py` facade (all consumers now use the new modules).

**Verify:** `pytest tests/` passes. New `tests/engines/build/test_decide.py`
tests the pure state machine with synthetic events (no I/O). Existing
orchestrator tests pass through the facade, then migrate to test the new
modules directly.

---

### Epic 5 — Enforcement

**Goal:** Checks + reviewer federation extracted into a headless-invocable
`async Review(diff, invariant_context) -> list[Finding]` library that Build
calls, with mechanical (non-LLM) boundary and vocabulary enforcement. Absorbs
`feature-work/module-boundaries/problem.md` (module boundary enforcement).

**Bones:**
1. Create `jig/engines/enforcement/` package.
2. Define `async Review(diff, invariant_context) -> list[Finding]` as the
   headless-invocable contract (an async `Protocol.__call__` — reviewers spawn
   agents, so the contract is async, not sync).
3. Expose `jig/boundary_rules.py` at its new home
   `jig/engines/enforcement/mechanical/` via re-export (old path stays a shim
   until Final — see shim-removal policy).
4. Expose `jig/reviewers/dispatch.py` at its new home
   `jig/engines/enforcement/reviewers/` via re-export (old path stays a shim
   until Final).

**MVP:**
1. Migrate `jig/check_runner.py` into the enforcement library.
2. Wire Build to invoke Enforcement via the `Review` contract (not direct
   calls to `jig/reviewers/`).
3. Make the review loop headless: `review(diff, context) -> findings → fix →
   re-review` runnable without the daemon.
4. Mechanical boundary enforcement: import deny-lists checked per-commit against
   declared module boundaries. A boundary violation is a build-blocking finding,
   not a reviewer judgment. (From `module-boundaries/problem.md`: the module
   boundary check must be deterministic, not LLM-adjudicated.)
5. Vocabulary/ontology enforcement: flag term drift against the project
   Ontology — synonyms, redefined terms, concepts with multiple homes. This is
   the "one concept, one home" invariant made mechanical.

**Final:**
1. Full reviewer federation (all reviewer types).
2. Severity/disposition calibration.
3. Contract conformance checks (code verified against declared contracts —
   deterministic-ish, may overlap with Reconciliation for derived-from-code
   checks).

**Verify:** `pytest tests/` passes. New `tests/engines/enforcement/` covers the
`Review` contract and mechanical boundary checks. Reviewer tests
(`tests/test_review_*.py`) pass through the new location. Module boundary tests
(`tests/test_boundary_*.py`) pass through the enforcement library.

---

### Epic 6 — Authoring engines (Discovery, Architecture, VD)

**Goal:** The init/onboard/SA workflows extracted behind their `project://`
authorities, with the three SA roles unified into one size-adaptive SAU that
produces both architecture (Phase 1) and a tracer-bullet plan (Phase 2).
Absorbs `feature-work/larger-projects/problem.md` (SAU unification + TB
planning), `feature-work/sa-architect/problem.md` (grounded decisions),
`feature-work/architecture-skeleton/problem.md` (architecture artifacts),
`feature-work/medium-l0-l3-pipeline/problem.md` (PO L0-L3 pipeline),
`feature-work/project-onboarding/problem.md` (brownfield onboarding), and
`feature-work/jig-init-process/problem.md` (init conversation).

**Bones:**
1. Create `jig/engines/discovery/`, `jig/engines/architecture/`,
   `jig/engines/visual_design/`.
2. Define authority boundaries: Discovery writes `project://spec/...`,
   Architecture writes `project://arch/...`, VD writes `project://design/...`.
3. Expose the SA↔operator loop (the ask/answer/approval flow) at
   `jig/engines/architecture/` via a **re-export/wrapper seam** over
   `jig/init_workflow.py`. Real extraction of the flow is MVP work, not Bones.
4. Expose the PO interview conversation at `jig/engines/discovery/` via a
   **re-export/wrapper seam** over `jig/init_workflow.py`. Replacing the v1 flat /
   v2 L0-L3 split with one Discovery interview at architectural resolution is
   MVP work (from `medium-l0-l3-pipeline/problem.md` and
   `jig-init-process/problem.md`) — Bones only stands up the seam.

**MVP:**
1. **Unify the three SA roles** (sa, sa_mvp, sa_v2) into one size-adaptive
   `jig/engines/architecture/sa.py`. All profiles route through this single
   role. No `sa` vs `sa_mvp` vs `sa_v2` branching in routing. (From
   `larger-projects/problem.md` requirement 1.)
2. **SAU Phase 1 (Architecture):** the unified SA produces the full
   architecture artifact set — contracts, schemas, boundaries, data ownership,
   grounded tech decisions. Preserves `sa_mvp` scope (per-module contracts,
   behavioral + data contracts, risks) and `sa`'s grounded-decision protocol
   (Context7 + WebFetch). (From `larger-projects/problem.md` requirement 2,
   `sa-architect/problem.md`, `architecture-skeleton/problem.md`.)
3. **SAU Phase 2 (Planning):** the unified SA produces an ordered tracer-bullet
   execution plan as a machine-readable artifact. Slices are thin vertical
   cross-module cuts, each ending with working, testable software. Order
   follows the derisker-first principle: highest-downstream-impact slices first.
   (From `larger-projects/problem.md` requirement 3.) This is the first-class
   execution planning phase that Jig is missing today.
4. **Graph tools first-class:** `graph_get_impact`, `graph_neighbors`,
   `graph_consumers_of`, `graph_tracers_for`, `graph_changed_interfaces`
   functional in the unified SAU role, allow-listed under strict_tools. These
   were stranded in `sa_v2` only; they now work in every SAU call. (From
   `larger-projects/problem.md` requirement 5.)
5. **TB plan composes with TracerSpec:** the planning-layer TB plan integrates
   with the existing eval-layer `TracerSpec` smoke test schema
   (`jig/schemas/tracer.py`) via `bones_ticket_id` or equivalent. The TB plan is
   upstream of tickets; PM consumes it for ticket slicing. (From
   `larger-projects/problem.md` requirement 4.)
6. Extract onboarding from `jig/onboard_workflow.py` into
   `jig/engines/discovery/onboard.py`. (From `project-onboarding/problem.md`.)
7. Profile routing: SAU keeps a small/medium behavioral distinction (same role,
   different modes — small projects produce fewer TBs, not no TBs). This is
   size-graded, not profile-graded. (From `larger-projects/problem.md` open
   question 3 — resolved here as "keep the distinction, it's depth-of-descent
   not separate roles.")

**Final:**
1. VD engine (light for Jig-the-TUI, full for general projects).
2. Cascade governance (model-change propagation) in Architecture.
3. TB-plan operator confirmation: operator reviews/overrides the TB plan
   alongside the existing architecture confirmation (not a separate gate).
   (From `larger-projects/problem.md` open question 4.)
4. Brownfield TB support as a design target (not a requirement for this
   migration). Greenfield TB planning is the requirement; brownfield uses the
   onboarding path. (From `larger-projects/problem.md` requirement 8.)

**Open questions to resolve before MVP:**
- **TB plan output format:** new SAU artifact (e.g.
  `.jig/spec/tracer_bullets.yaml`), a new section in `architecture.yaml`, or
  embedded in `suites.yaml`? (From `larger-projects/problem.md` open question 1.)
- **Derisker heuristic:** how to compute "most derisking" slice ordering.
  Candidates: external integrations first, module coupling spine, technology
  choices under load, or some combination. Does the operator confirm/override
  the ordering? (From `larger-projects/problem.md` open question 2.)
- **Onboarding SAU dispatch:** clean replacement or needs greenfield/brownfield
  signal? (From `larger-projects/problem.md` open question 5.)

**Verify:**
- `pytest tests/` passes. Init/onboard tests (`tests/test_init_*.py`,
  `tests/test_onboard_*.py`) pass through the new locations. SA role tests
  updated for the unified role (`test_sa_adjudication.py`,
  `test_sa_v2_registration.py`, `test_sa_incremental_registration.py` rewritten).
- One SA role YAML in `jig/defaults/roles/`. All three old files collapse.
  (From `larger-projects/problem.md` success criteria.)
- SAU produces two distinct, observable outputs: architecture (Phase 1) and
  tracer-bullet plan (Phase 2). TB plan is parseable and machine-readable.
- Graph tools functional in SAU.
- Existing `TracerSpec` smoke tests continue to run and validate systems; no
  regression from SAU TB planning.
- hn-cli eval stays green. (From `larger-projects/problem.md` success criteria.)
- Medium-sized eval scenario uses SAU and produces a valid TB plan. (From
  `larger-projects/problem.md` success criteria — lands when the medium eval
  project is ready.)

---

### Epic 7 — Reconciliation

**Goal:** Derive the actual structure from code, diff it against the declared
model, and surface drift as work (tickets via Build). New capability — no
existing implementation to extract.

**Bones:**
1. Create `jig/engines/reconciliation/`.
2. Define `derive_actual_graph(code_path) -> DependencyGraph`.
3. Define `diff(declared, actual) -> DriftReport`.

**MVP:**
1. Resolve Spike 2 (static-analysis approach). Implement
   `derive_actual_graph` using the chosen tool.
2. Run drift detection on Jig's own codebase (dogfood — the declared
   architecture in `architecture/` vs the real code).
3. Surface drift as tickets via Build.

**Final:**
1. Intent drift (reviewer-adjudicated).
2. Cascade triggering from structural drift.

**Verify:** New `tests/engines/reconciliation/`. Dogfood: running
reconciliation on Jig itself produces a drift report comparing the target
architecture to the current code.

---

### Epic 8 — Evaluation (headless harness)

**Goal:** Drive the Build + review loop headless on fixture corpora — reviewer
precision/recall against labeled diffs and a fix-loop convergence metric. This
epic's bones run is the **code-pipeline composition check** — one (important) item
on the aggregate bones gate, not the whole gate.

**Bones:**
1. Create `jig/engines/evaluation/`.
2. Define the eval harness contract: `EvalRun(build_config, fixtures) ->
   EvalResult`.
3. Wire the harness to use `FixtureRunAgent` (Epic 3) + headless Build
   (Epic 4) + headless Enforcement (Epic 5).
4. **Run the code-pipeline eval headless on a fixture end-to-end — this *is* the
   bones acceptance test, and it lands in Bones, not MVP** (so the "bones before
   MVP" gate is satisfiable by bones alone).

**MVP:** (expand the harness beyond the single happy-path fixture)
1. Build a fixture corpus of labeled diffs → expected findings (for reviewer
   precision/recall).
2. Build a fix-loop convergence metric.
3. Wire the real dispatch feedback loop (dispatch emits the next event from the
   agent run, rather than the bones script driving the event sequence directly).

**Final:**
1. Full eval corpus.
2. Metrics over time (quality tracking).
3. Synthetic-operator simulator integration.

**Verify:** The bones acceptance test passes: the code-pipeline runs headless
on a fixture, without the daemon/TUI, using fake agents.

---

### Epic 9 — EDGE (thin client)

**Goal:** The TUI/CLI/daemon reduced to a thin client over a daemon API, with no
direct engine/store imports and persona variation (developer vs founder)
localized to the edge layer.

**Bones:**
1. Define the daemon API contract (command/event/snapshot protocol) in
   `jig/edge/api.py`.
2. Audit `jig/cli.py` and `jig/tui/` for direct access to engine internals.
   List the violations.

**MVP:**
1. Route all CLI/TUI access through the daemon API (no direct store or engine
   imports).
2. Localize persona variation points (developer vs founder density) in the
   edge layer.

**Final:**
1. TUI refactor (thinning).
2. CLI command consolidation.
3. Persona-conditioned rendering.

**Verify:** `pytest tests/` passes. TUI tests
(`tests/test_tui_*.py`, `tests/tui/`) pass through the API. No engine module
is imported by `jig/edge/` except via the API contract.

---

## Bones acceptance gate (before any MVP starts)

The bones phase ends — and MVP may begin — only when **both** of the following
hold. The first is the global gate; the second is one (important) item within it,
not a substitute for it.

### 1. Aggregate bones checklist (the global gate)

Every epic's **Bones** task list is complete **and** its **Verify** block passes.
No single test stands in for this — the per-epic **Verify** blocks above are the
source of truth. Tick each before any MVP starts:

- [ ] Epic 1 — Model: `tests/model/` invariant-fn signatures + `OntologyTerm` single home
- [ ] Epic 2 — Substrate: `tests/substrate/` composition test (read routes/raises, write raises)
- [ ] Epic 3 — Runtime: `tests/runtime/` seam with `Real`/`Fixture` impls
- [ ] Epic 4 — Build: `tests/engines/build/test_decide.py` pure state machine
- [ ] Epic 5 — Enforcement: `tests/engines/enforcement/` `async Review` + mechanical checks
- [ ] Epic 6 — Authoring: authority-boundary tests (Discovery/Architecture/VD seams)
- [ ] Epic 7 — Reconciliation: `tests/engines/reconciliation/` `derive`/`diff` contracts
- [ ] Epic 8 — Evaluation: the code-pipeline composition check below
- [ ] Epic 9 — EDGE: `jig/edge/api.py` daemon-API contract + CLI/TUI coupling audit list

### 2. Code-pipeline composition check (one checklist item, the hardest composition)

The single hardest composition — the headless code-pipeline — runs green. This is
**Epic 8's Bones Verify**, named explicitly because it exercises four epics at
once:

> The code-pipeline and review loop run **headless on a fixture**, without the
> daemon/TUI, using `FixtureRunAgent` (Epic 3) + pure `decide()` (Epic 4) +
> `async Review` contract (Epic 5) + Model entities (Epic 1). No `Orchestrator`
> god-object, no `mcp_server.py`, no daemon, no TUI in the path.
> (See `jig/engines/evaluation/harness.py` / `tests/engines/evaluation/test_acceptance.py`.)

This check validates the **Epics 1, 3, 4, 5** composition specifically. It is one
row on the checklist above — **not** the whole gate. It does **not** exercise
Epic 2 (Substrate), 6 (Authoring), 7 (Reconciliation), or 9 (EDGE); those are
gated by their own **Verify** blocks in item 1. Passing this check alone does not
authorize MVP — the full checklist must be green.

**Scope of the composition check — what it does and does not cover:**

- **`StoreAuthority` (Epic 2) is *not* on this path.** The shipped harness
  composes Model + Runtime + Build + Enforcement; it does not read or write
  artifacts through `StoreAuthority`. Listing Substrate here would be a false
  validation, so it is excluded — Substrate composition is gated separately by the
  **Epic 2 composition test** (item 1): `read` routes through the authority and
  raises `UnimplementedAuthorityError` for unwired authorities; `write` raises
  until MVP.
- **`EDGE` (Epic 9) is not on this path either.** Its bones (the daemon API
  contract in `jig/edge/api.py` + the CLI/TUI coupling audit) are gated by Epic 9's
  own **Verify** (item 1), not by the headless run.

This is the evaluability driver from `jig-instance.md` → Build suite signal #4.
If the bones compose, the architecture is validated on Jig's own hardest case
before any real logic migrates.

## Seam stubs & post-bones blockers

The bones phase intentionally ships seams whose behavior is shimmed, stubbed, or
empty. Each is fine as a bone, but several encode behavioral commitments that
must be made real **before any production routing depends on them** — otherwise a
skeleton silently becomes load-bearing. This table is the register of those
stubs: where the real implementation lands, and whether it is a hard blocker
before production use.

| Stub / seam | Introduced (bones) | Real impl lands | Blocker before production routing? |
|---|---|---|---|
| `StoreAuthority.write()` unimplemented | Epic 2 | Epic 2 MVP 1 | **Yes** — see security requirements below; no production write may route through it until done |
| Invariant fns are stub bodies (`coverage`, `conformance`, …) | Epic 1 | Epic 1 MVP/Final | No — Enforcement/Reconciliation gate on these, not production traffic |
| `decide()` handles one transition only | Epic 4 | Epic 4 MVP 1 (all transitions), Final (sad paths) | **Yes** — partial state machine must not own real dispatch |
| At-least-once dispatch + idempotent effect handlers | Epic 4 (dispatch shell) | Epic 4 MVP | **Yes** — ack/idempotency tracking required before real side effects (spawn/merge) run |
| Partial multi-action dispatch error handling | Epic 4 (dispatch shell) | Epic 4 MVP (design first) | **Yes** — concrete failure-semantics design required before real side effects; see below |
| Build coordinator serialized (single in-flight) | Epic 4 | Epic 4 Final (per-ticket concurrency) | No — but carries a perf acceptance criterion before production use; see below |
| `FixtureRunAgent` canned results | Epic 3 | Epic 3 MVP (`RecordedRunAgent`), Final | No — fixtures are eval-only; production uses `RealRunAgent` |
| `Review` contract returns from moved code, not headless loop | Epic 5 | Epic 5 MVP 3 | No — Build keeps calling existing reviewers until the headless loop lands |
| Merge-conflict transition not modeled | Epic 4 | Epic 4 Final 1 | No — falls back to existing orchestrator facade path until migrated |
| ~~`derive_actual_graph()` returns empty graph~~ — **resolved (Spike 2, #202): real grimp-backed implementation landed** | Epic 7 | ~~Epic 7 MVP 1~~ done | n/a — drift surfacing still wires to Build in #221 |

### Concrete requirements for the **Yes** blockers

- **`StoreAuthority.write()` security (Epic 2 MVP).** The bones signature
  `async write(self, uri: str, doc: dict) -> str` carries no caller identity, so
  authority-scoped authorization is **not expressible against it as written** —
  closing this requires an API change at MVP, not just logic. Before any
  production write routes through the authority, MVP must: (a) add a caller
  context via an **authority-scoped handle** — this is the decided model, not an
  open question: `StoreAuthority.for_authority("spec") -> ScopedWriter`, so a
  `spec` caller physically cannot address `project://arch/...`. (Caller-principal
  and capability-token designs were considered and rejected as heavier than the
  problem; do not reopen the API choice — extend the handle if more is needed.)
  (b) enforce `project://` URI validation and
  path safety — reject traversal, out-of-authority paths, and malformed URIs
  loudly (no silent normalization); (c) validate the payload schema against the
  target authority's typed model. All three are Epic 2 MVP acceptance criteria,
  not Final polish.
- **Partial multi-action dispatch (Epic 4 MVP).** `decide()` can return multiple
  actions; the shell may succeed on some and fail on others. Before real side
  effects are wired, the failure semantics must be pinned: per-action ack,
  idempotent re-dispatch on retry (so a re-run can't double-spawn or double-merge),
  and a defined state for "ticket whose action set partially applied". **This
  design lands as an ADR in `docs/reference/` (e.g. `ADR-NNN-dispatch-semantics`),
  linked from Epic 4 MVP, before — not alongside — the first real effect handler.**
  (Migration epics get no `feature-work/` docs; cross-cutting dispatch semantics
  are a long-lived decision, so they belong in `docs/reference/`, not the
  throwaway plan.)
- **Build coordinator concurrency (Epic 4 Final).** Serializing the coordinator is
  acceptable for bones/MVP. Before production use, the acceptance criterion is
  concrete: with **N = 8** concurrent in-flight tickets (matching the default
  agent-concurrency cap), the coordinator adds **< 50 ms p95** dispatch overhead
  beyond agent-runtime time, and a single slow/blocked agent never stalls dispatch
  for other ready tickets (no head-of-line blocking). Per-ticket concurrency is
  Final work; the numeric criterion is recorded now so it isn't lost.

## Compatibility & shim-removal policy

The migration is backwards-compatible at each step via re-export shims and
string-topic compat layers (see Rollback). To keep "when does the old path go
away" from being implicit:

- A shim or old import path stays until its epic's **Final** phase.
- A shim may only be removed once **all** consumers import the new path and the
  full suite is green; removal is itself a Final-phase task (e.g. Epic 1 Final 3,
  Epic 2 Final 1–2), never bundled into Bones or MVP.
- Old import paths become thin re-export shims at Bones, are dual-supported
  through MVP, and are deleted at Final — in that order, never skipping a step.

## Rollback

The migration is brownfield and backwards-compatible at each step:

- Epic 1: re-export shims mean consumers can ignore the new `jig/model/` package.
  Rollback = delete `jig/model/`, restore the original schema definitions.
- Epic 2: typed events are additive (string topics still work through the
  compat layer). Rollback = remove typed-event adapter, keep string topics.
- Epic 3: `RealRunAgent` wraps the existing `agent.py` — no behavior change.
  Rollback = route spawns back to `agent.py` directly.
- Epic 4: `orchestrator.py` becomes a facade delegating to new modules.
  Rollback = remove the facade's delegation, restore the original method bodies.
- Epic 5–9: extraction moves code, not behavior. Rollback = restore the
  original file locations.

If multiple epics are partially migrated and the system is unstable, the safe
state is: revert to the last commit where all tests pass, re-plan the failing
epic, and retry. The test suite (346 files) is the safety net — if it's green,
the migration hasn't broken anything.

## Out of scope for this plan

- **Bug fixes** — tracked separately on the board, not folded into this
  migration. They proceed on their own schedule. The eval-found issues (#186
  dep-merge race, #187 same-module divergence, #188 PM re-plan retry) are
  bug fixes, not architecture migration work.
- **New features** (deployment agent, mutation testing, kanban board, etc.) —
  remain on the board as individual tickets, not part of this migration.
- **Pair-programming experiment** — separate eval experiment after the
  architecture is stable. SAU's graph tools + TB plan feed directly into it,
  but the experiment itself is not scoped here (from `larger-projects/problem.md`
  non-goals).
- **Onboarding internal redesign** — the onboard workflow is extracted as part
  of Epic 6, but its internal redesign is a separate concern. The extraction
  moves it behind a `project://spec/` authority; how onboarding works inside
  that boundary is a future design doc.
- **Non-Python boundary enforcement** — out of scope (see `model.md`).
- **Runtime (import-hook) enforcement** — out of scope (see `model.md`).
- **VD engine full implementation** — light for Jig-the-TUI only in this
  migration; full VD is a general-project concern.
- **Brownfield TB retrofitting** — TB planning applies to new / not-yet-understood
  work. Existing-running code uses the onboarding path. Brownfield TB support is
  a design target, not a requirement (from `larger-projects/problem.md`
  non-goals).
- **Changes to PM ticket schema** — TB plans are upstream of tickets. How PM
  slices tickets against the TB plan is a downstream design question (from
  `larger-projects/problem.md` non-goals).

## Change log

- 2026-06-25: Initial draft (brent-hoover, Frank)
- 2026-06-26: Folded `larger-projects/problem.md` requirements into Epic 6
  (SAU unification, TB planning phase, graph tools, derisker ordering,
  TracerSpec integration, medium eval scenario). Folded `module-boundaries`
  into Epic 5 (mechanical boundary + vocabulary enforcement). Added absorbed
  feature-work docs section. Added open questions from larger-projects to
  Epic 6. Updated out-of-scope for consistency. (Frank)
- 2026-06-26: Addressed design-review findings (job 698). Converted the absorbed
  feature-work list into a supersession table mapping each doc to its exact
  epic+phase. Added an epic dependency matrix and per-epic **Goal** lines (plan
  is now implementable from the repo alone). Added a "Seam stubs & post-bones
  blockers" register with concrete acceptance criteria for `StoreAuthority.write()`
  security, partial multi-action dispatch semantics, and Build-coordinator
  concurrency. Added a compatibility & shim-removal policy.
- 2026-06-26: Addressed second-round design-review findings (job 703).
  Reconciled plan text with the shipped code: `Review` is `async`; bus events are
  `TicketCreated`/`TicketUpdated` (not `TicketScheduled`/`TicketCompleted`).
  `StoreAuthority.write()` security now names the API change (authority-scoped
  handle) since the bones `write(uri, doc)` signature carries no caller identity.
  Marked Epic 8 Bones as depending on Epics 1/3/4/5 (+2) in the matrix. Scoped
  Epic 4 Bones coordinator to synthetic-only (no production dispatch). Reworded
  Epic 1 MVP / Epic 5 Bones to "expose via re-export", not "remove"/"move", per
  the shim policy. Pinned the coordinator perf criterion (N=8, <50ms p95) and the
  partial-dispatch ADR location (`docs/reference/`). Added "do not implement"
  banners to the eight superseded design/plan bodies.
- 2026-06-26: Addressed third-round design-review findings (job 711). Removed
  `StoreAuthority` from the bones acceptance gate entirely — the shipped harness
  (`jig/engines/evaluation/harness.py`) composes Model+Runtime+Build+Enforcement
  and never routes through Substrate, so the prior "harness asserts StoreAuthority
  routing" text was aspirational; Substrate is now verified by a dedicated Epic 2
  composition test instead. Scoped the gate explicitly to the Epics 1–8
  code-pipeline (Epic 9 EDGE bones verified by its own Verify). Moved the headless
  fixture run from Epic 8 MVP into Epic 8 Bones (so "bones before MVP" is
  satisfiable by bones). Reworded Epic 2 Bones to "facade only — reads/writes
  raise until MVP". Removed the now-moot "+2" gate dependency from the matrix.
- 2026-06-26: Addressed fourth-round design-review findings (job 715). Committed
  firmly to the authority-scoped-handle write model (removed the "alternatives
  decided later" language that left the API ambiguous). Marked
  `feature-work/module-boundaries/{design,plan}.md` superseded with do-not-implement
  banners pointing to Epic 5 MVP 4–5 (only `problem.md` had been marked before).
  Reworded the dependency intro to name Epic 5/7's narrow Epic 1 type-contract
  deps alongside Epic 8's full-composition exception. Normalized the
  `BuildCoordinator` module docstring to "decide, dispatch, then commit" (the
  module header still said "advance state, dispatch", contradicting the code).
- 2026-06-26: Addressed fifth-round design-review findings (job 720). Split the
  bones gate into an **aggregate bones checklist** (every epic's Bones + Verify —
  the real global gate) and the **code-pipeline composition check** (the Epic 8
  headless run, renamed from "the gate for the whole bones phase" since it only
  covers Epics 1/3/4/5). Reworded Bones tasks that said "extract" real logic to
  "wrapper / re-export seam" to match the shipped shim-first pattern: Epic 3
  (`RealRunAgent` wraps `jig.agent.run_agent`) and Epic 6 (SA/PO flows exposed via
  seam, real extraction deferred to MVP).
