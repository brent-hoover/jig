---
title: Jig Architecture — Living Invariant
type: reference
status: draft
owner: brent-hoover
created: 2026-06-21
updated: 2026-06-26
---

# Jig Architecture — Living Invariant

> Working capture of an ongoing architecture-scoping effort. Low ceremony on purpose — **not** the
> `problem/design/plan` format. We append as we think.

## Thesis

Jig's job is to maintain a **Living Invariant**: a durable, enforced model of the system being built — its
intent and its structure — that keeps independently-spawned agents coherent across hundreds of runs.

The Living Invariant has four faces:

1. **Authored** — elicited from the operator (the interview) and written as intent + structure.
2. **Enforced** — agents are held to it; violations are blocked or surfaced (mechanical where possible).
3. **Reconciled** — continuously checked against the real code; drift becomes work.
4. **Measured** — evals prove enforcement is actually working ("real evals, not vibes").

A spec that is only authored rots. A map that is only reconciled can't constrain. The point is all four.

## The parallel track (dogfooding)

This is two questions answered by one mechanism:

- **What is Jig's own architecture?** (Jig as a built system)
- **How does Jig express and enforce architecture for the projects it builds?** (the product capability)

They're the same thing. Jig should express and enforce its *own* architecture using the very machinery it
offers users. Jig is the hardest realistic test case we have, sitting right here. Every invariant violation
the model surfaces in Jig's own code is either a real defect in Jig **or** a gap in the model's
expressiveness. That signal is unfakeable.

## Drivers (why this work)

- **Comprehension + testability** (the original pains): hold the system in your head; isolate pieces to verify.
- **Evaluable code-pipeline.** A primary motivation: the Build / code-pipeline must run **in isolation —
  without the daemon/TUI/whole app — so it can be eval'd.** This is a first-class architectural requirement (a
  fitness function), and it retro-justifies the Build decomposition: a pure `decide()` state machine + a
  substitutable **Agent Runtime** seam are the *precondition* for headless pipeline evals (the code-side
  analog of the synthetic-operator simulator). See `jig-instance.md` → Build suite signal #4.
  - Applies equally to the **code-review loop** (reviewers + fix-loop): feed `diff + invariant context` →
    `findings` → fix → re-review, headless, with substitutable reviewer/fix agents, against a **fixture corpus
    of labeled diffs → expected findings** (reviewer precision/recall) plus a fix-loop **convergence** metric.
    Cross-suite: **Build** orchestrates the loop, **Enforcement** judges, **Agent Runtime** supplies the
    (substitutable) agents — so the requirement lands on all three boundaries.

## Files

Text:

- `model.md` — the entity model, the invariants, the two-ladder structure, the lifecycle.
- `interview.md` — the operator interview that authors the intent half (Ask / Produces / Locks-in).
- `jig-instance.md` — the dogfood: Jig's own pitch, pillars, personas, principles as we generate them.
- `plan.md` — the migration plan: 9 epics, bones-first, absorbing all feature-work problem statements.

Diagrams (open in [excalidraw.com](https://excalidraw.com) — read in this order):

- `jig-target-architecture.excalidraw` — **target state**: 5 layers, 7 engines with authority
  annotations, Build decomposition, RunAgent seam with 3 impls, contracts between layers.
- `jig-code-structure.excalidraw` — **current state**: actual module files with line counts,
  color-coded by target layer. Red = god-objects. Gray = target modules that don't exist yet.
- `jig-living-invariant.excalidraw` — **theory diagram**: two-ladder convergence, four-faces
  cycle, layered architecture overview.
- `jig-architecture-sketch.excalidraw` — original hand sketch (archive).

## Absorbed feature-work docs

The migration `plan.md` supersedes and absorbs these `feature-work/` problem
statements — their requirements are now explicit tasks in the plan's epics:

- `larger-projects/problem.md` → Epic 6 (SAU unification, TB planning, graph tools, derisker, TracerSpec, medium eval)
- `sa-architect/problem.md` → Epic 6 (grounded decisions preserved in SAU)
- `architecture-skeleton/problem.md` → Epic 6 (full artifact set)
- `module-boundaries/problem.md` → Epic 5 (mechanical boundary + vocabulary enforcement)
- `medium-l0-l3-pipeline/problem.md` → Epic 6 (Discovery interview replaces v1/v2 split)
- `project-onboarding/problem.md` → Epic 6 (onboarding extracted behind `project://spec/`)
- `jig-init-process/problem.md` → Epic 6 (init conversation becomes Discovery interview)

Each is marked `status: superseded` with `superseded_by: ../../architecture/plan.md`.

## Fragments this unifies

The territory already exists in the codebase, scattered across features that grew **without an organizing
theory**. The clearest fossil: `OntologyTerm` is defined **twice** (`jig/schemas/arch.py` and
`jig/schemas/po.py`) — the same concept forked across the two columns. This effort is the missing parent.

Existing `feature-work/` fragments that should hang under this theory:

- `project-onboarding`, `jig-init-process`, `brief-for-analyzer` — the interview / authoring side
- `architecture-skeleton`, `sa-architect`, `module-boundaries` — the technical-architecture side

### Anchors — existing top-level docs this theory defers to, does NOT duplicate

- `TENETS.md` — the apex: **correctness is the bar**, reached via **coherence** ("bite-sized work, coherent
  whole"). Simplicity/clarity *serves* these; it doesn't outrank them. Also: Jig's twin "why" — make agents
  better at small tasks, make humans better at thinking/driving.
- `FIRST_PRINCIPLES.md` — law 3, "the product spec is one living, breathing doc that AGENTS work off of," **is
  the Living Invariant, already stated.** This effort makes it enforced + reconciled.
- `ontology.md` — canonical home for **Jig's own** vocabulary (meta-ontology), distinct from the per-project
  **Ontology** entity. Two ontologies; don't conflate.

Existing schema material (already richer than the sketch):

- Technical: `Architecture`, `ContractsFile` (`DataContract`, `BehavioralContract`, `ExposedAPI`,
  `EmittedEvent`, `SharedContract`), `BoundariesFile`, `Module` — `jig/schemas/arch.py`
- Intent: `Journey`, `Suite`, `Persona`, `Capability`, `StructuredSpec` (`UserStory`, `Behavior`,
  `GivenWhenThen`) — `jig/schemas/po.py`, `jig/spec_schema.py`
- Enforcement fossils: `boundary_rules.py` (import deny-lists), `CascadeProposal`/`CascadeStage`
  (governed contract change), `eval/` + Metrics (measurement)

## Open questions / parked

- **Technical-ladder entry point.** The interview so far is all intent (user column). The technical column
  (project *shape*: CLI/service/library; hard constraints: language, runtime, greenfield-vs-existing) needs a
  defined entry point. It can't stay out forever or the intent model builds in a vacuum.
- **Persona split.** "developers and founders" may be two personas with different needs (a founder wants the
  thing to *exist*; a developer wants control/quality). Resolve at the persona step.
- **Reconciliation mechanics.** Drift detection = derived-from-code (structure) + reviewer-adjudicated
  (intent) + agent-asserted (hint only). The "make trace a first-class graph" move collapses most intent
  checks into deterministic graph queries — invest there.
- **Pushback smell taxonomy.** "Pushback within reason" needs a taxonomy of weak-input smells (vague persona,
  outcome-adjective-without-mechanism, no contrast, unbounded scope) so it's systematic, not vibes. Some are
  mechanically detectable.
- **Doc consolidation (future — NOT yet).** Collapse the scattered canon into two coherent homes: a
  **principles** doc (`TENETS.md` is already the strong base; fold in `FIRST_PRINCIPLES.md` — only law 4,
  QA/Dev code isolation, is not already subsumed) and an **ontology** doc (`ontology.md`, jig's own
  vocabulary; the per-project `Ontology` entity is separate). Deferred until the model is settled — don't
  merge canon prematurely.
