---
title: Larger Projects — Problem Statement
type: problem
status: superseded
owner: brent-hoover
created: 2026-06-18
updated: 2026-06-26
superseded_by: ../../architecture/plan.md
---

# Larger Projects — Problem Statement

> **Superseded.** This problem statement is absorbed into
> `architecture/plan.md` (Epic 6 — Authoring engines). The SAU unification,
> tracer-bullet planning phase, graph tools, derisker ordering, TracerSpec
> integration, and medium eval scenario are all explicit tasks in Epic 6 MVP.
> See the "Absorbed feature-work docs" section of `architecture/plan.md`.

# Larger Projects — Problem Statement

## Context

Jig has proven it can build small CLI projects end-to-end — the `hn-cli` eval runs
clean from brief through completion. The workflow works: PO shapes the brief, SA
plans the architecture, PM slices tickets, Dev implements, Reviewers verify. Each
agent knows its job; the pieces compose; the project finishes.

We now want to scale up to larger projects. Multi-module, multi-integration systems
where "larger" means things like an ATS-sized project (jobs, applicants,
interviewers, interviews, evaluations), and eventually self-hosting (Jig building
Jig itself).

The current architecture was designed around small-project assumptions. At small
scale the gaps don't show — small projects can be planned as single slices and the
SA role surface doesn't need much. At larger scale several cracks surface at once.

## Problem

Four related cracks, each blocking different aspects of scaling up.

### 1. Fragmented SA role surface

Three SA-family roles exist with overlapping responsibilities and stranded
features:

- `sa.yaml` — v1 flat spec. Small projects. Grounded tech decisions (Context7 + WebFetch) from Phase 1.
- `sa_mvp.yaml` — v2 full discovery loop. L1/L2/L3 artifacts, per-module contracts, behavioral + data contracts + risks.
  Large projects.
- `sa_v2.yaml` — "bones scope" label. Carries a dependency-graph toolset (`graph_get_impact`, `graph_tracers_for`,
  `graph_neighbors`, etc.) that neither of the others has, plus a minimal-architecture exit.

The three roles share operator-confirmation protocols, artifact writes,
strict-tools allow-lists, and adjudication wiring. Changes to the SA contract land
in three places. Any graph-tool feature is stranded in `sa_v2` only. Onboarding
hardcodes `sa_mvp`; medium profile flips on `sa_role: sa_mvp` vs `sa`.

Scaling up to ATS-sized work requires one coherent SA role that handles small and
large without routing gymnastics.

### 2. No first-class execution planning phase

SAU will unify architecture planning: contracts, schemas, boundaries, data
ownership, grounded decisions. That covers what the system *is*. It does not cover
how to *execute* the build.

Today jig jumps from architecture to PM ticket slicing to Dev implementation with
no explicit tracer-bullet plan in between. That works at small scale, where the
whole project is one slice. At larger scale it breaks:

- Agents outrun their headlights. Building horizontally (full Applicant module, then full Interviewer module) hides
  incompleteness — the agent looks busy but the system doesn't run end-to-end.
- Schema and domain errors surface late, after weeks of build-in-the-dark, instead of on the first slice.
- Discipline erases. Without a plan that says "slice 1 ends with: can add an applicant and schedule an interview," the
  agent claims progress that isn't progress. ("Go from working to working.")

Tracer-bullet planning is a first-class output missing from jig entirely. TBs are
thin vertical slices that cut across modules, ordered so each ends with running,
testable software. TBs apply to new / not-yet-understood work — not retrofitting
existing code that already runs (that's the separate onboarding surface, #119).

Jig already has the eval-layer schema (`TracerSpec` in `jig/schemas/tracer.py`)
for runnable smoke tests with a `bones_ticket_id` backlink. That's the validation
half. SAU needs the planning half: an ordered list of what to build first,
what each slice proves, what depends on it.

### 3. SAU needs both phases to compose

If SAU only owns Phase 1 (architecture) and TB planning lives elsewhere, the
outputs can't align — PM needs a single upstream source for both the
module-boundary map AND the slice plan to produce coherent tickets. If the two
phase outputs come from different places, coordination drift.

TB planning depends on architecture output (you plan slices across modules you
already understand from the architecture). Architecture output is wasted if you
can't turn it into a buildable execution plan. They form one job.

So SAU has two phases: **Architecture** (what is the system?) and **Planning**
(how do we execute it?). Distinct outputs, shared role, one SA run per project.

### 4. Self-hosting is the hard target

Jig building Jig itself is the proof that larger-project support actually works.
Jig is a multi-module Python project with non-trivial architecture. If Jig can
plan TBs against its own codebase, it can plan against any ATS-class project.

Self-hosting is not in scope for this feature. It's the load-bearing success
criterion we should design against, however. SAU's TB planning primitive should
make self-hosting possible, even though we're implementing it for greenfield first.

## Simplest possible solution

Delete sa_mvp and sa_v2, route everything through sa.yaml. Done. Maintenance
problem solved.

That fails on every other requirement. sa.yaml is the v1 flat-spec SA; it has
no discovery loop, no per-module contracts, no graph tools, no TB planning.
Small projects would keep working; larger ones would regress to whatever the
v1 flat spec could express.

The more complete solution below has to earn its complexity on the requirements
list. If it doesn't, simplest wins.

## Complications considered

- **Scale**: N/A — SAU runs once per `jig init`. Bounded by single invocation. SA unification does not introduce new
  scaling dimensions.
- **Concurrency**: N/A — SAU is sequential, single-operator.
- **Failure modes**: SA crash mid-plane already covered by init resume loop (`classify_resume` handles `arch_finalize`
  since #137). TB-plan absence is an observable state — fails loud, not silent.
- **Cross-cutting policies**:
  - `jig/onboard_workflow.py` hardcodes `load_role(project_path, "sa_mvp")` — must be re-routed.
  - `jig/defaults/profiles/medium.yaml`, `small.yaml` — routing updates.
  - `jig/defaults/roles/pm.yaml` prompt references `sa` / `sa_mvp` — must update.
  - Tests (`test_sa_adjudication.py` parametrizes over 3 roles; `test_sa_v2_registration.py`,
    `test_sa_incremental_registration.py` pin role file existence) — must rewrite.
  - Existing `TracerSpec` eval-layer smoke tests must not regress. SAU's planning-layer TB plan must integrate with
    them, not replace them.

## Constraints

- **Existing artifacts must keep working.** hn-cli eval stays green. Medium-profile scenarios stay green.
- **Design doc required.** Jig convention is problem → design → plan. This is problem. Design follows.
- **TBs are a principle that propagates.** SAU plans the slices. PM consumes them for ticket slicing. Dev implements
  within slice boundaries. Review gates TBD (downstream). TBs are jig-wide discipline, not SA-only work.
- **TBs apply to new / not-understood work.** Existing-running code uses the onboarding path (#119), not TB-planned
  slices. The two surfaces must stay cleanly separated.
- **TB ordering: do the derisker first.** The TB that, if proven wrong or unworkable, invalidates the most downstream
  work. Not "easiest first" or "cheapest first." The first slice should test the assumptions the rest of the project
  leans on most heavily.

## Requirements

- Single SA role, routed by all existing profiles. No `sa` vs `sa_mvp` vs `sa_v2` branching in routing.
- SAU Phase 1 (Architecture): produces full architecture artifacts — contracts, schemas, boundaries, grounded tech
  decisions, data ownership. Existing sa_mvp scope preserved; sa_v2's graph tools now first-class in every SAU call;
  sa's grounded-decision protocol preserved.
- SAU Phase 2 (Planning): produces ordered tracer-bullet execution plan. Slices are thin vertical cross-module; each
  ends with working, testable software. Plan is a parseable, machine-readable artifact — not prose. Observability and
  testability at the handler boundary. Order follows the derisker-first principle: highest-downstream-impact slices
  first.
- SAU planning-layer TB plan composes with existing eval-layer `TracerSpec` smoke test schema. Link via
  `bones_ticket_id` or equivalent.
- Graph tools (`graph_get_impact`, `graph_neighbors`, `graph_consumers_of`, `graph_tracers_for`,
  `graph_changed_interfaces`) functional in SAU role, allow-listed under strict_tools.
- hn-cli eval stays green.
- A medium-sized eval scenario (ATS-class, or Jig-within-Jig if SAU gets far enough) uses SAU and produces a valid TB
  plan.
- TB planning primitive supports greenfield work today; brownfield support is possible design target, not a requirement.

## Non-goals

- **Pair-programming experiment.** SAU enables it (graph tools + TB plan feed directly into SA-alongside-dev models),
  but this doc does not scope or design it. Separate eval experiment after SAU + TB planning are stable.
- **Onboarding workflow internals.** SAU is the SA role onboarding invokes; the onboard workflow itself is a separate
  problem doc (#119). We must not break it when unifying SA routing, but we also don't rework it here.
- **PM / Dev / Review workflow changes.** TB planning produces an artifact PM consumes and Dev implements. How PM slices
  tickets against it, how Dev stays within slice scope, whether Review gates TB edges — those are downstream design
  questions, not in this doc's scope.
- **Changes to PM ticket schema.** PM's ticket model is not changed by this feature. TB plans are upstream of tickets.
- **Brownfield TB retrofitting.** Existing-running code is onboarded, not TB-planned. TB slice applies only to new /
  not-understood work.
- **New eval framework.** Existing eval harness (PR #177, #180) plus TracerSpec smoke tests suffices for SAU validation.
- **SAU deciding between greenfield and brownfield.** SAU receives a "what kind of project is this" signal upstream of
  planning; the decision is not SAU's job.

## Success criteria

- One SA role YAML in `jig/defaults/roles/`. All three old files collapse.
- All existing tests pass: profile loader, init medium e2e, incremental registration, sa adjudication parametrize,
  onboard workflow, sa incrementals. Test suite rewritten where it pinned the now-removed role names.
- SAU produces two distinct, observable outputs: architecture (Phase 1) and tracer-bullet plan (Phase 2).
- TB plan is parseable and machine-readable.
- Graph tools functional in SAU.
- hn-cli eval stays green.
- Medium-sized eval scenario (when it lands) uses SAU and produces valid TB plan.
- Existing TracerSpec smoke tests (eval-layer) continue to run and validate systems; no regression from SAU TB planning.

## Open questions

- **TB plan output format.** New SAU artifact (e.g. `.jig/spec/tracer_bullets.yaml`), a new section in
  `architecture.yaml`, or embedded in `suites.yaml`?
- **TB plan granularity / "derisker" heuristic.** The ordering principle is "do the derisker first" — but the
  *heuristic* for computing "most derisking" is unresolved. External integrations first? Module coupling spine?
  Technology choices under load? Some combination? Does the operator confirm / override the ordering?
- **Profile routing inside SAU.** Does SAU keep a small/medium behavioral distinction (same role, different modes) or
  collapse to behavioral-identical? Small projects produce fewer TBs, not no TBs — that's size-graded, not
  profile-graded.
- **SAU's TB-plan confirmation.** Does the operator get to review/override the TB plan alongside the existing
  architecture confirmation, or is TB planning SAU's unilateral?
- **Onboarding SAU dispatch.** `onboard_workflow.py` today always uses `sa_mvp`. SAU replaces it. Is it a clean
  replacement, or does onboarding need additional signals to route to the right SAU mode (greenfield-vs-brownfield)?
- **Self-hosting target.** Jig building Jig is the long-horizon proof. Is it the success criterion or just inspiration?
  When does it become the actual test?
- **SAU / onboarding coupling.** Onboarding (#119) is the prerequisite for self-hosting (Jig must ingest Jig first). SAU
  supports both greenfield and the brownfield path via onboarding. Are these two features or one?

## Change log

- 2026-06-18: Initial draft (brent-hoover)
