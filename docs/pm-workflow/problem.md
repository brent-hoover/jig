---
title: PM Workflow — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-01
---

# PM Workflow — Problem Statement

## Context

The multi-level spec (`docs/multi-level-spec/`) defines how a product gets specified — pitch, discovery, suites, suite
briefs. The SA design (`docs/sa-architecture/`) defines how the architecture gets specified — modules, contracts,
integration AC, risks. Together they produce a fully described system: what to build (PO) and how it fits together (SA).

Neither tells you how to actually *do* the work. Today, ticket creation in jig is light: the brief becomes a flat list
of tickets, dispatched roughly in the order they appear. There is no concept of:

- Decomposing a capability into multiple tickets at appropriate granularity.
- Estimating tickets.
- Deciding which tier of dev/reviewer agent should handle each.
- Sequencing work across modules to validate integration before fleshing out features.
- Managing escalations from dev agents back to SA / operator.
- Tracking which tickets are in flight, blocked, or done.

In a well-resourced human team, these are PM responsibilities. We've already established the PO/PM split (PO owns
brief+specs; PM owns issues+plans). This doc designs the PM half.

## Problem

Two related problems show up when scaling agentic development beyond a single-feature project:

**1. BDUF (Big Design Up Front) breaks agentic dev.**

Specifying everything upfront and dispatching agents to build it all in parallel produces, in practice, "8 hours of
agent churn that yields a lot of code that passes tests and does nothing." Each agent works in isolation; nothing has
been exercised end-to-end; integration only gets tested at the very end, when divergence is most expensive to fix.

The empirically-observed alternative — building thin happy-path slices end-to-end first ("tracer bullets" / "walking
skeletons"), then iteratively fleshing them out — produces dramatically better results. But there's no current mechanism
in jig to enforce that ordering. A flat ticket list dispatches features in whatever order they were authored; nothing
ensures the integration spine gets built first.

**2. No layer between "design is done" and "agents implement."**

The SA produces contracts. The PO produces behavior AC. But there's a gap between those artifacts and the actual stream
of work: someone has to decide *which tickets exist*, *how big they are*, *which agent tier handles each*, *which
reviewers run on each*, and *what order they get done in*. Without that intermediate plan, every ticket is treated
equally — equal priority, equal tier, equal reviewer set — which is wasteful for simple work and dangerous for
foundational work.

In particular, the tier mismatch problem is real: running every PR through an SA-tier reviewer is too expensive to ship;
running every PR through a standard-tier reviewer misses architecturally important issues. Without per-ticket tiering,
jig defaults to one or the other, and both are wrong most of the time.

## Constraints

- **Must compose with PO and SA workflows.** PM consumes their outputs; doesn't replace them. PM fires after SA
  completes (or after SA delta).
- **Must scale with project size.** A 3-ticket project shouldn't need a build plan with bones/MVP/final layers; a
  50-ticket project does. Same scale-judgment principle as SA.
- **AI-driven authorship.** The Planner PM is an agent. Operator confirms; doesn't author the plan from scratch.
- **Must support iteration.** The build plan is living, not write-once. As tickets surface contract gaps or risks
  materialize, the plan adjusts.
- **Tenet 1 — bite-sized + coherent.** PM's job is precisely to produce the bite-sized pieces *and* sequence them so
  they add up to a coherent system. Tracer bullets are how the "coherent" half gets enforced.
- **Tenet 2 — exact context.** Each ticket carries only the context its dev agent needs. PM's tier decisions and
  reviewer-set selection drive that.

## Requirements

- A **build plan** artifact that describes the work to do at multiple completeness layers (bones / MVP / final), not
  just a flat ticket list.
- A **ticket creation workflow** that decomposes capabilities into appropriately-sized tickets, estimates effort,
  assigns dev tier, selects reviewer set, and links to risks.
- A **tracer-bullet ticket type** distinct from standard tickets — deliberately cross-module, deliberately incomplete in
  scope (one happy path), explicitly intended to validate integration.
- A **build-plan ordering rule**: bones layer of all epics before MVP layer of any epic, before Final layer.
- A **role split** between strategic planning (slow, heavy context, runs in passes) and tactical coordination
  (continuous, light context, mostly mechanical routing).
- An **escalation routing path** from dev agents → SA (for contract gaps) or operator (for product decisions), handled
  by the Coordinator PM.
- **Scale-down behavior** so a small project doesn't trigger the full machinery.

## Non-goals

- Project management at a human-team scale (sprint planning ceremonies, capacity tracking across people, retrospectives).
  The "sprint" concept here is just a unit of dispatched work, not a human ritual.
- Full requirements traceability matrices and the like. The spec/contract URI scheme already gives us the traceability
  we need.
- Replacing the operator's ability to steer. PM proposes; the operator approves and can reorder, retier, or rework
  anything.
- Multi-team coordination. Single-team / single-operator assumed.
- Time estimation in wall-clock units. Estimation is relative-effort (S/M/L) and tier-aware.

## Success criteria

- For a medium-size project (15-25 capabilities, 4-6 suites), the Planner PM produces a build plan with epics aligned to
  suites or capabilities, tracer-bullet tickets for each foundational module, and standard tickets to fill out features
  after the bones complete.
- Bones layer (all tracer bullets) completes before MVP layer of any epic begins. Operator can see this enforced in the
  build plan.
- Each ticket is dispatched at an appropriate dev tier, with an appropriate reviewer set, with the right contracts in
  context — none of it manually configured.
- Dev agent escalations route automatically to SA (contract gaps) or operator (product decisions); the Coordinator PM
  doesn't drop them.
- For a small project (1 suite, ~3 tickets), the Planner PM produces a flat ticket list with no bones/MVP/final layering
  and skips the Coordinator PM entirely; the SA hands tickets directly to the orchestrator.
- The build plan is living: surfacing a contract gap or realizing a risk has materialized triggers a Planner PM delta
  pass.
