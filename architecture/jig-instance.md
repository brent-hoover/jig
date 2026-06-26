---
title: Dogfood — Jig's Own Living Invariant
type: reference
status: draft
owner: brent-hoover
created: 2026-06-21
updated: 2026-06-26
---

# Dogfood — Jig's Own Living Invariant

Jig run through its own interview. This is both the product test (can the model describe a real, messy system?)
and Jig's actual self-description. Generated live as we walk the interview.

## Values hierarchy (reconciled with TENETS.md / FIRST_PRINCIPLES.md)

Reading the existing docs corrected an overclaim. I had written "primary value is simplicity & clarity," but
`TENETS.md` is explicit: **correctness — does the app actually do what it's supposed to — is the bar.** These
aren't rivals once stacked:

- **Bar (the goal):** *correctness.* The built app does what it's supposed to. (TENETS, "Why Jig Exists")
- **Mission (how, on non-trivial projects):** *coherence.* Keep each agent task small enough to hold in one
  window of attention, and make the small tasks add up to a whole. (TENETS tenet 1, "Bite-sized work,
  coherent whole")
- **Value / aesthetic (what keeps coherence achievable and the system livable):** *simplicity & clarity* —
  the thing that triggered this whole effort.

Causal chain: **simplicity/clarity → coherence → correctness.** The Living Invariant exists so agents never
have to make architectural calls mid-implementation — that's how small tasks stay correct *and* add up.

**The Living Invariant is not a new idea — it formalizes `FIRST_PRINCIPLES.md` law 3:** "the product spec is
one living, breathing doc that AGENTS use to work off of." This effort makes that law *enforced and
reconciled* instead of merely stated.

**Meta-irony, now doubled:** capturing this, I almost wrote a *competing* statement of Jig's primary value
right next to TENETS — a fresh one-concept-two-homes violation. Caught only by reading first. That is the
"one concept, one home" invariant working *by hand*; the goal is to make it work mechanically.

## Step 1 — Pitch

**Pitch (v3):**

> Jig is an application that helps developers and founders build high-quality, well-architected,
> small-to-medium size projects that are reliable and maintainable by using agent-tailored architectural
> processes and a focus on code quality that is enforced at every level, driven by real evals rather than
> vibes.

Passes the stopping rule: persona ✓ (developers + founders), scope bound ✓ (small-to-medium), mechanism ✓
(agent-tailored architectural processes; enforcement at every level), contrast ✓ ("rather than vibes").

**Pillars (the mechanism, extracted — these seed top-level Capabilities):**

1. **Agent-tailored architectural process** — the authoring + factory side.
2. **Multi-level enforcement** — "enforced at every level" (mechanical → reviewer).
3. **Eval-driven quality** — "real evals rather than vibes" (the *measurement* face; ties to the Metrics
   store). Distinct from reconciliation: reconciliation asks "does code match the declared architecture?";
   evals ask "is the architecture any good, and is enforcement actually working?"

> The dogfood loop closed on itself: pushing on Jig's pitch surfaced that **Jig's own differentiator is the
> Living Invariant** — the exact feature being scoped.

## Step 1.5 — Non-goals

**Anti-persona:** "not for people who just want to get something/anything up quickly." Deliberately rejects
the instant-app market (Bolt, Lovable, v0, raw vibe-coding). Coherent with the pitch (inverse of "rather than
vibes").

Two axes fused in that one line (they reject different people):

1. **Values axis** — doesn't care how it's built; wants a result at any cost.
2. **Patience / investment axis** — won't put in upfront effort; wants instant gratification.

The interview *itself* enforces axis 2: a push-back-laden discovery process is a filter — someone who won't
sit through it self-selects out before writing any code. **The process is the gate.**

Durable encoding (so a future *faster* Jig doesn't wrongly exclude quality-minded users): **"doesn't value
durable quality, and won't invest to get it."**

**Seed persona (the inverse):** someone who **values durable quality enough to invest upfront effort.** →
defining trait of persona #1.

**Candidate project principle:** *"Durable quality over speed."* Promote from non-goal to an enforced
`CrossCuttingPolicy` agents cite at every speed-vs-quality fork.

## Step 2 — Operator persona

**Developer** (design target). Founder = reduction ("just less"). For the Jig dogfood, operator persona
coincides with the product's user persona. Flow detail in `interview.md` Phase 2.

## Phase 2 output — suites (derived from Jig's journey = the phase flow)

**Discovery**, **Architecture**, **Build**, **Enforcement**, **Reconciliation**, **Evaluation**,
**Operator Experience**. (Last four cross-cutting; may fold the invariant three into one "Living Invariant"
suite.)

## Phase 2 output — capabilities at architectural resolution

Each = user story (trace) + facets {data, integrations, NFRs, risk}. Testable behaviors/AC deferred to build
layer.

### Suite: Discovery

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Capture pitch | As a dev, I want to state my project in one line and have Jig sharpen it, so the project has a clear scope arbiter | Project root + pillar seeds → spec store | none | interactive | low |
| Elicit non-goals | As a dev, I want Jig to draw out what it's NOT, so scope has a ceiling | ProductNonGoal + candidate principles → spec store | none | interactive | low |
| Enumerate personas | As a dev, I want to define who uses the product, so every capability traces to a real user | Persona → spec store | none | interactive | low |
| Conduct journey interview | As a dev, I want Jig to walk each persona's journey, so capabilities derive from real usage | Journey + project Ontology → spec store | none | interactive, **stateful/resumable** | **med** (multi-turn state; thinking-exercise quality) |
| Capture project ontology | As a dev, I want my domain terms captured once, so every agent uses my vocabulary | Ontology/domain terms → spec store (`.jig/spec/ontology.md`) | read by all later agents | low | med (vocabulary-consistency enforcement is cross-suite) |
| Derive suites & capabilities | As a dev, I want Jig to group journeys into suites/capabilities, so requirements are complete | Suite + Capability + UserStory → spec store | feeds Architecture | low | med (LLM-driven derivation; coverage quality) |

**Architectural signal for the SA:** Discovery is a mostly self-contained suite that writes the intent model
to the spec store; conversational/interactive; the journey interview is **stateful (persist/resume)**; no
external integrations; feeds Architecture. Hard parts: stateful multi-turn interview + LLM-derivation quality.

### Suite: Architecture

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Ingest intent model | As a dev, I want the architect to read personas/suites/capabilities, so the architecture is grounded in requirements | reads spec store | spec store | low | low |
| Propose modules & boundaries | As a dev, I want suites mapped to modules with boundaries, so the system has clear units | Module/Boundary → arch store | reads spec | low | med (core design judgment) |
| Define contracts | As a dev, I want a contract at each boundary, so pieces compose | Contract (API/Event/Data/Behavioral) → arch store | none | low | med |
| Ask the operator (the loop) | As a dev, I want the architect to ask me when intent is underspecified, so I fill gaps instead of the agent guessing | Question/Answer → thread | **operator** | interactive | low |
| Trigger spikes | As a dev, I want technical unknowns explored via bounded spikes, so architecture isn't blocked on guesses | Spike ticket + learning | Build/Factory | low | med |
| Flag risks & open questions | As a dev, I want risks and open questions surfaced, so unknowns are explicit | Risk, OpenQuestion → arch store | operator/spike | low | low |
| Produce architecture for approval | As a dev, I want to review and approve the architecture, so I stay in control (founder: auto) | arch doc + approval | **operator (gate)** | low | low |

**Architectural signal for the SA:** first suite with a **bidirectional operator loop** (Ask the operator /
approval gate) and a **dependency on Build/Factory** (spikes). Two kinds of "need more": operator-answerable →
ask; technical-unknown → spike.

### Suite: Build (the factory loop)

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Plan the build | As a dev, I want approved capabilities decomposed into ordered tickets (epics × bones/MVP/final), so work is sized and sequenced | BuildPlan / Epic / Ticket → plan store | reads arch + spec | low | med (decomposition + ordering quality) |
| Dispatch tickets | As a dev, I want ready tickets dispatched to agents automatically, so work progresses without shepherding | ticket state transitions, agent tasks | **Agent Runtime, Bus** | concurrency, reliability | **HIGH** (this is the orchestrator god-object) |
| Write tests for a ticket | As a dev, I want a test agent to write failing tests against AC, so the implementation is verified | test files → worktree | Agent Runtime, worktree | **QA/dev isolation** (FIRST_PRINCIPLES law 4) | med |
| Implement a ticket | As a dev, I want a dev agent to implement against the contract + AC, so the capability gets built | code → worktree | Agent Runtime, worktree, per-agent MCP, sandbox | isolation | med-high |
| Review a ticket | As a dev, I want federated reviewers run on the PR, so quality is gated before merge | review comments | **invokes Enforcement suite**, Agent Runtime | parallelism | med |
| Validate & merge | As a dev, I want the worktree linted/tested and merged when green, so main stays coherent | check results, commit/merge | worktree, git | correctness gate | med |
| Handle failure / escalation | As a dev, I want failed/stalled tickets escalated (replan / spike / operator), so the build never silently stalls | escalation, deferred queue | PM, operator, **Supervisor** | reliability | **HIGH** (deadlock / stall / cascade) |

**Architectural signal for the SA — this is the crux suite.** Highest risk and highest coupling; it's where
today's `orchestrator.py` god-object lives. The load-bearing architectural calls:

1. **One per-ticket state machine (happy + sad path); one thin global supervisor.** The orchestrator's two
   duties — *walk a ticket through the pipeline* and *deal with its problems* — are the **same** per-ticket
   state machine: problems (blocked, needs-info, review-failed, merge-conflict) are its **sad-path
   transitions**, decidable from `(ticket state + event)`. The **only** thing that is a separate engine is
   cross-ticket / cross-time **detection** one ticket's state can't reveal: deadlock (cycle in the dependency
   graph), stall (heartbeat timeout).
   - **Decision test:** *can you decide it from one ticket's state + one event?* yes → state machine; no →
     supervisor.
   - **Single-writer rule (dissolves the entanglement):** the supervisor never mutates tickets — it **emits an
     event** into the relevant ticket's state machine, which stays the sole writer of ticket state. A stall
     sweep hands the machine a `stalled` event; it doesn't race the reactive path. (`deadlock.py` /
     `stall_detector.py` become event-emitting detectors, not mutators.)
   - **Naming (one concept, one home):** "Supervisor" is a *new* term (today's `ontology.md` has only
     "Orchestrator", meaning everything). Post-decomposition "Orchestrator" must **narrow** (to the Build
     coordinator wiring *ticket state machine* + *dispatch/effects* + *supervisor*) or **retire** — else it
     becomes a synonym for its own parts. Supervisor ≠ Orchestrator.
2. **Agent isolation** — QA/dev never share code (law 4); each agent gets a worktree + sandbox + own MCP.
3. **Containment** — Build *invokes* Enforcement and Agent Runtime but must not *own* them (it depends down,
   not sideways).
4. **Evaluable in isolation (a stated driver — see README "Drivers").** The pipeline must run **headless** —
   fed `ticket + contract + AC + worktree fixture`, returning `code + check results` — *without* the
   daemon/TUI/whole app. Forces: (a) a programmatic entry point; (b) **Agent Runtime as a substitutable seam**
   (real vs simulated/recorded/fixture agents — the code-side analog of the synthetic-operator simulator);
   (c) the pure `decide()` machine is directly testable with synthetic events; (d) an Evaluation↔Build harness
   contract. This is the original "hard to test" pain aimed at the pipeline — and it's *why* the
   state-machine/shell split earns its keep.

### Suite: Enforcement

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Mechanical structure checks | As a dev, I want boundary/dependency violations caught mechanically, so containment holds without judgment | check results | reads arch (boundaries) + code; import deny-lists / bwrap | fast, deterministic, per-commit | low |
| Contract conformance checks | As a dev, I want code verified against its declared contracts, so pieces compose as promised | check results | arch contracts + code | deterministic-ish | med |
| Trace / coverage checks | As a dev, I want orphan capabilities/contracts and uncovered journeys flagged, so coverage holds | graph-query results | reads full Living-Invariant graph | graph compute | low (**deterministic once trace is data** — the lever) |
| Semantic review (LLM reviewers) | As a dev, I want reviewers to judge "does this code actually fulfill the behavior", so the residue mechanical checks can't decide is covered | findings / review comments | **Agent Runtime**, invoked by Build | parallel federation | med (LLM judgment quality) |
| Severity & disposition | As a dev, I want findings triaged by severity (critical blocks merge), so the gate is calibrated | findings + acks + deferred queue | Build (merge gate), operator (override) | low | med |
| Vocabulary / ontology enforcement | As a dev, I want synonym/term drift flagged against the ontology, so "one concept, one home" holds | check results | reads ontology + artifacts | low | low-med |

**Architectural signal for the SA:** Enforcement is a **library of checks invoked by Build** (and run
per-commit) — it depends down on the Model, never owns dispatch (clean containment: Build → Enforcement →
Model). Mostly **deterministic** (structure / contract / trace / vocabulary); the only LLM piece is semantic
review, which needs Agent Runtime. The trace/coverage checks collapse to deterministic graph queries **once
trace edges are first-class data** — the central lever for "mechanical as much as possible."

**Evaluability (stated driver):** the **code-review loop** must be runnable **headless on a labeled-diff
fixture corpus** (`diff → expected findings`) with substitutable reviewer agents — so reviewer precision/recall
and fix-loop convergence are measurable without the whole app. Cross-suite: Build orchestrates
review→fix→re-review; Enforcement provides reviewers/findings; Agent Runtime supplies the agents.

### Suite: Reconciliation (the "living" leg — mostly missing today)

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Derive actual structure from code | As a dev, I want the real import/dependency graph extracted from code, so declared structure can be compared to reality | derived graph | code analysis (grimp-like), catalog/graph modules | periodic / per-merge | med (derivation accuracy) |
| Diff declared vs actual | As a dev, I want drift between the declared architecture and the real code surfaced, so the map can't silently lie | drift report | reads Model + derived structure | periodic | med |
| Surface drift as work | As a dev, I want drift turned into tickets/findings, so reconciling is actionable, not just a report | tickets / findings | **Build** (creates work), operator (triage) | low | med |
| Reconcile intent drift | As a dev, I want semantic drift (a contract no longer serving any journey) adjudicated, so intent stays honest too | findings | Agent Runtime (reviewer), Model | low | med (semantic) |
| Govern model change (cascade) | As a dev, I want a change to the declared model to propagate to dependents under control, so the invariant evolves coherently | CascadeProposal / CascadeStage | Architecture (SA), operator approval | low | med-high |

**Architectural signal for the SA:** this is the **mostly-missing "living" leg.** It introduces a *new
dependency on static code analysis* (derive actual structure), which nothing else needs. Structural drift =
deterministic (graph diff); intent drift = reviewer-adjudicated; both **feed Build as work**. **Resolved
(Phase 3): cascade / model-change governance lives in Architecture** (SA owns contracts → owns changing them);
Reconciliation handles only code↔model drift, and — per the greenfield-only scope — is scoped to build-time
drift, not project evolution (yet).

### Suite: Evaluation (the measurement face)

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Collect run metrics | As a dev, I want metrics collected from a completed run, so quality is measured not vibed | eval manifest → metrics store | reads run artifacts | low | low |
| Define eval criteria | As a dev, I want to define what "good" means (criteria/judges), so measurement is meaningful | eval configs | Model (what to measure) | low | med (metric design) |
| Run evals / judge | As a dev, I want evals run (incl. LLM-judge), so outputs are scored | eval results | Agent Runtime (judges) | batch / offline | med (judge reliability) |
| Track quality over time | As a dev, I want metrics tracked across runs, so regressions surface | historical metrics | metrics store | low | low |
| Feed enforcement / heuristics | As a dev, I want eval signal to tune enforcement thresholds, so the system learns | feedback | Enforcement, heuristics | low | med |

**Architectural signal for the SA:** **read-mostly / offline** — observes runs and scores them; not in the
critical build path. Depends on Agent Runtime (judges) + reads Model + run artifacts. Distinct from
Reconciliation (eval = "is the output/architecture *good*?"; reconciliation = "does code *match* the declared
model?"). The synthetic-operator simulator ties in here for workflow testing.

### Suite: Operator Experience (the EDGE)

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Render board / state views | As a dev, I want data-rich views of tickets/agents/spec/events, so I can see what's happening | reads stores via WS | daemon / ws_server, stores | real-time, **persona density** | low |
| Present gates & collect approvals | As a dev, I want gates that show what's being decided and collect structured approval/override, so I drive (tenet 5) | prompt req/reply, override reasons | daemon, analytics | interactive | med (gate UX = thinking tool) |
| Drive the interview | As a dev, I want to conduct discovery through the UI, so authoring happens here | prompt flow | Discovery suite, daemon | stateful/interactive | med |
| Issue commands / concierge | As a dev, I want slash commands + a concierge helper, so I can act and ask | command envelopes | daemon command registry | interactive | low |
| Persona-conditioned rendering | As a dev/founder, I want views/gates at the right density for my persona | persona config | persona setting | **the variation point** | low (must stay localized) |

**Architectural signal for the SA:** the **EDGE** — a thin client (TUI) over a daemon API; depends on engines
through a thin API, **never their internals** (containment). It is where the **persona variation points are
localized** (the 10–20%); persona-awareness leaking past here is the smell. Daemon/client split already
exists.

### Phase 2.3 — COMPLETE

All seven suites decomposed to architectural resolution (user story + facets).

## Phase 3 — Architecture (proposed)

Architect's proposal for Jig itself, grounded in the 7 suites, the orchestrator decomposition, and the
evaluability drivers. Suites → modules is **not** 1:1.

### Modules by layer (allowed dependencies point DOWN only)

**CORE** (pure, no I/O)
- **Model** — Living-Invariant entities (Project, Persona, Journey, Capability, Suite, Boundary, Contract,
  Module, Ontology, Trace) + the 5 invariants as pure functions; Ticket / Thread domain types.
- **Pipeline core (`decide`)** — the pure per-ticket state machine `decide(state, event) -> (next, actions)`.
  Pure → directly testable; Build-owned but dependency-free.

**SUBSTRATE** (dep: Model)
- **Store** — JSONL persistence behind the `project://` URI authorities `{spec, arch, design, plan, store}`.
- **Bus** — typed message stream (events, not magic strings).

**RUNTIME** (dep: Model, Store, Bus)
- **Agent Runtime** — spawn/stream agents (SDK + sandbox + per-agent MCP). **The substitutable seam:** one
  `RunAgent` contract, implementations = real / recorded / fixture. (Evaluability driver lands here.)

**ENGINES** (dep DOWN on Model/Store/Bus/Runtime; **never sideways** — coordinate via Store authorities + Bus)
- **Discovery** — interview engine; writes intent to `project://spec/...`.
- **Architecture** — SA engine; reads `project://spec/...`, writes `project://arch/...`; runs the SA↔operator
  loop. **Owns cascade** (model-change propagation).
- **Visual Design (VD)** — frontend-architecture + visual-artifacts engine; **parallel to Architecture**;
  reads `project://spec/...`, writes `project://design/...` (wireframes, screens, design system, frontend
  stack).
- **Build** — the factory; wraps *Pipeline core (decide)* + *dispatch/effects shell* + *Supervisor*;
  orchestrates pipeline + review loop; invokes Enforcement + Agent Runtime.
- **Enforcement** — checks library (mechanical + reviewers); invoked by Build.
- **Reconciliation** — derive-actual-from-code + diff vs declared; new static-analysis dep; feeds Build.
- **Evaluation** — offline measurement; drives Build / review-loop headless via the seam.

**EDGE** (dep: engines via a thin daemon API, never internals)
- **Operator Interface** — daemon + WS + TUI + CLI; persona variation localized HERE.

> **God-object fix, concretely:** `orchestrator.py` → *Pipeline core (decide)* + *dispatch/effects shell* +
> *Supervisor*, all inside Build. `cli.py` / `ws_server.py` / TUI → the EDGE thin client. The scattered
> `schemas/` + `ticket.py` + `thread.py` → CORE Model.

### Key contracts (the boundaries that carry weight)

1. **Store authorities (URI scheme)** — inter-engine coordination: engines don't call each other; they
   read/write the `project://` authorities `spec` / `arch` / `design` / `plan` / `store` + emit Bus events.
   Writer ownership: Discovery→spec, Architecture→arch, VD→design, PM/Build→plan.
2. **`RunAgent` seam** — `spawn_context -> stream/result`; real vs recorded vs fixture. THE evaluability seam.
3. **Build↔Enforcement** — `review(diff, invariant_context) -> findings`; Build orchestrates the fix-loop.
   Must be **headless-invocable** (review-loop eval driver).
4. **Typed Bus events** — what the state machine consumes / the supervisor emits (kills magic-string topics).
5. **Daemon API** — EDGE↔engines (command / event / snapshot protocol).

Evaluability drivers concentrate on #2, #3, and a headless Build entry point.

### SA ↔ operator loop — RESOLVED (Phase 3 complete)

Operator decisions (loop run and closed):
- **Orchestrator** → **narrowed to the Build coordinator** (wires decide() core + dispatch/effects shell +
  supervisor). Term survives with a precise scope; no longer a synonym for everything.
- **Cascade governance** → **Architecture** (SA owns contracts → owns changing them). Reconciliation handles
  only code↔model drift.
- **Scope** → **greenfield-only for now.** Ongoing / existing-project journeys deferred; Reconciliation scoped
  to build-time drift, not project evolution (yet).
- **VD / frontend-architecture** → **include a VD module** (ENGINES layer, parallel to Architecture; owns
  `project://design/...` — frontend stack, wireframes, screens, design system).

Spikes registered for build-time (don't block approval — bounded explorations, not operator questions):
- Can `decide()` be genuinely pure given the SDK's async/streaming nature?
- Static-analysis approach for Reconciliation (validate drift detection on Jig itself).
- Agent record/replay for fixture-based evals (the `RunAgent` fake).

> **Phase 3 APPROVED → requirements-gathering + architecture complete.** Ready to build (PM build-plan +
> factory loop = Phase 4).

## Phase 4 — Build (PM build-plan + factory loop)

**Brownfield reality:** Jig is **not** greenfield — the code exists (the god objects). So Phase 4 for Jig is a
**migration toward the approved architecture**, not a from-scratch build. (The greenfield-only scope decision
was about what Jig *builds for others*, not Jig's own codebase.) The build plan decomposes the **gap** between
current code and target.

### Epics (≈ a module-cluster of work), in dependency order

1. **CORE Model** — extract the Living-Invariant entities + 5 invariants into a pure, I/O-free module;
   **dedupe `OntologyTerm`**, consolidate `schemas/` + `ticket.py` + `thread.py`. Foundational.
2. **Substrate** — Store behind the `project://` authorities; Bus with **typed events** (kill magic-string
   topics).
3. **Agent Runtime seam** — the `RunAgent` contract + real / recorded / fixture impls (unlocks evaluability).
4. **Build engine** — split `orchestrator.py` → **decide() core** (pure) + **dispatch/effects shell** +
   **supervisor** (single-writer rule). The god-object fix.
5. **Enforcement** — checks library (mechanical + reviewer federation) as a module Build *invokes*.
6. **Authoring engines** — Discovery, Architecture, VD behind their `project://` authorities.
7. **Reconciliation** — derive-actual-from-code + drift→work (new).
8. **Evaluation** — headless harness driving Build + review-loop on fixture corpora.
9. **EDGE** — daemon / WS / TUI / CLI as a thin client over the daemon API; persona variation localized here.

### Layered progression (bones → MVP → final)

- **Bones (walking skeleton — all epics' tracer bullets first):** stand up the target **boundaries + seams**
  and flow **one thin happy path end-to-end** through the new structure (logic shimmed to old code where
  needed). **Bones acceptance = the evaluability driver:** the code-pipeline *and* review loop run **headless
  on a fixture, without the daemon/TUI**, using fake agents. Proving the seams compose early is the point.
- **MVP (per epic):** migrate real logic behind each boundary; real enforcement checks; real interview.
- **Final (per epic):** edge cases, full reviewer federation, reconciliation drift, full eval corpus.

### Spikes (slot before the relevant epic's MVP)

- **pure `decide()` under the async SDK** → before Build MVP.
- **static-analysis approach for Reconciliation** → before Reconciliation.
- **agent record/replay for fixtures** → before Agent Runtime seam / Evaluation.

### Where the dogfood lands

Bones proves the architecture against Jig's own hardest case: if the refactored pipeline runs headless on a
fixture and the review loop scores it, the Living Invariant's central bet (an evaluable, seam-substitutable
factory) is validated **on Jig** before it's sold to anyone else. After the plan: execution = the factory loop
(Coordinator PM dispatching tickets; agents building each epic layer) — Jig building Jig.
