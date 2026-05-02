---
title: Learnings — Problem Statement
type: problem
status: deferred
owner: brent
created: 2026-05-01
---

# Learnings — Problem Statement

## Context

Jig agents are stateless: each spawn starts from zero. The system accumulates state about a project (briefs, contracts,
tickets, decisions, change logs) but those are *facts about what to build*, not *judgments about how to build it well*.
Real human teams develop a third kind of accumulated knowledge over time: opinions formed through experience, the kind a
senior dev applies before they consciously think about it.

This doc names the gap and explicitly defers the design.

## The two tiers worth distinguishing

There's a real distinction between what the system needs to remember:

**Facts and workarounds** — things you can write down. "Library X has bug Y until v3.2." "Auth uses JWT in cookie." "We
chose Postgres because compliance constraints." These have a clear truth value and a clear shelf life. Validation:
operator confirms once. Consumption: pull on demand (search "is there a known issue with X?"). Substantively addressed
elsewhere — the SA architecture doc covers project-level facts; the PM workflow doc covers candidate facts captured by
dev agents during implementation.

**Heuristics and opinions** — judgments formed through accumulated experience. "Prefer plain functions over classes when
there's no state." "IoC is good at module boundaries but unwieldy for internal types." "Don't put validation logic in
both API and DB layers — pick one." These are context- dependent, can be falsified, and have a different shelf life.
Validation requires holding up across N applications. Consumption is push into role prompts (shapes default judgment)
because a heuristic nobody consults is dead.

This second tier — accumulated wisdom — is the harder problem and the bigger differentiator.

## Why this matters for jig specifically

Most agent harnesses are either fully stateless ("each task starts from zero") or have shallow memory (per-thread
context windows, occasionally a project notes file). What's mostly missing across the space is *the agent equivalent of
mentorship
+ accumulated experience*: the judgment a senior developer
applies before they think about it.

For jig to be more than yet-another-agent-harness, it needs a real story for how the system gets better at building
software over time — not just at executing already-decided tasks. A heuristics layer would provide the path: discovered
patterns → validated heuristics → injected into junior dev agent prompts → improved default behavior. That's the
difference between a tool that runs and a tool that learns.

## Why deferred

Designing this layer now risks building for patterns we *imagine* will appear rather than patterns we *observe*. We
don't yet have the operational corpus — completed projects, real reviewer rationales, repeated escalations, falsified
heuristics — to know which kinds of judgments actually emerge, which generalize, and which fail in practice. Premature
schema choices here calcify badly: a wrong heuristic shape will quietly shape behavior in ways that are expensive to
undo later.

The right time to design heuristics:

- After 3+ medium-sized projects have run through the full workflow (PO + SA + PM + dev + reviewers) so we have real
  retrospective data.
- After the per-project facts/workarounds system has been exercised enough to surface what it's missing.
- When operators report "I keep teaching the agents the same lesson across projects" — that's the empirical signal that
  the heuristics infrastructure would pay off.

## What lands in v1 anyway (raw material capture)

The pieces of this conversation that should land in v1 without waiting for the full heuristics design — they cost
nothing now and they preserve the data we'll need to design the system later:

- **Structured rationale on reviewer comments.** Senior reviewer agents' comments include the WHY (not just the WHAT) —
  already in the PM workflow doc. These rationales are the raw material future heuristics will be mined from. Capturing
  them structurally means we don't lose the data before there's a system to consume it.
- **Retrospective triggers at milestones.** Bones complete, epic complete, project complete — even if all that happens
  initially is the operator typing `/retro` and a thin agent summarizing the work history into a flat findings list, the
  data point exists. The full retro → heuristic pipeline can come later; the historical record can't be created
  retroactively.
- **`.jig/learnings.jsonl` for facts and workarounds** (per PM workflow doc). This provides a substrate the heuristics
  system can sit alongside later without competing for the same conceptual space.
- **Tagged escalations and contract amendments.** Already in scope; these are signal-rich events that future analysis
  will want.

## When to revisit

Trigger: **3+ medium-sized projects shipped through jig, OR a recurrence pattern in operator-tagged feedback** ("I've
explained this same lesson 5 times across projects"). Whichever comes first.

When it does, this doc should be expanded into a full problem.md + design.md pair in the same shape as the other design
docs in this directory tree.

## Risks of letting this slide too long

- **Raw material rots.** If reviewer rationales aren't captured structurally from day one, recovering them later is
  impossible. The v1 capture work is non-negotiable even if the heuristics consumer is deferred.
- **Operators teach the same lesson repeatedly.** This is exactly the friction point that justifies the system.
  Tolerable for 3 projects; intolerable past that.
- **The "yet another agent harness" risk.** If jig ships and doesn't have a real story for accumulated judgment, it
  competes only on workflow plumbing — a crowded space. The heuristics layer is a meaningful differentiator we're
  choosing to defer, not abandon.

## Change log

- 2026-05-01: Placeholder created (brent + claude). Articulates the facts vs heuristics distinction; defers heuristics
  design with explicit revisit trigger; specifies the v1 capture work that preserves raw material for the eventual
  design.
