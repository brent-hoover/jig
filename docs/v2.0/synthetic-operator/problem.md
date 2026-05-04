---
title: Synthetic Operator Simulator — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-03
---

# Synthetic Operator Simulator — Problem Statement

## Context

The v2 design corpus describes a workflow with a lot of moving parts: PO discovery (L0→L4 levels), SA contracts + risks
+ spikes, VD wireframes, PM build plans with cycles + bones/MVP/final layers, federated reviewer agents, auto-escalation
thresholds, cascade-after-impossible-spike workflows, dev environment provisioning, and dozens of operator decision
points (gate confirmations, overrides, deferred-queue triages, plan unblocks).

The natural way to validate a workflow design is to run real projects through it and see what breaks. That works but
it's slow (a real project takes days to weeks), expensive (real LLM costs, real operator attention), and the operator's
specific style biases what gets exercised. Edge cases — operators who scope-creep mid-discovery, who type bad inputs,
who skip gates, who pivot, who never read the briefings — only surface in real projects when they happen to occur, by
which time the design has often already been built around the assumption they don't.

The agent-leverage doc commits to a synthetic operator simulator as a v2 v2-parallel deliverable specifically to get
this validation cheaply, repeatably, and across edge cases real operators may not exhibit on the projects we happen to
dogfood.

## Problem

Without a synthetic operator simulator, every v2 workflow design choice gets validated only by:

- **Intuition** ("this seems right"). Useful but unreliable; the design corpus is full of times we've changed our minds
  about something that "seemed right."
- **A handful of real runs.** Slow feedback (days per project), high noise (operator style varies, real-world friction
  injects unrelated failures), low coverage (you only exercise paths the operator happens to take).
- **Code review of the design docs.** Catches inconsistency in the docs; doesn't catch interactions between components
  only visible at runtime.

Specific kinds of bugs we won't catch without the simulator:

- **Workflow gate ordering bugs** — Planner PM expects SA-done before it fires, but a particular operator path arrives
  at PM with SA still in flight. Real operators rarely do this; an "ambivalent" persona might.
- **Cascade interaction bugs** — Spike A returns `confirmed_impossible` while Spike B is still in flight; the SA delta
  passes overlap; cascade proposals merge-conflict.
- **Auto-escalation false positives** — Coordinator force-escalates a dev agent that's actually progressing because the
  threshold is poorly calibrated; only visible across many ticket runs.
- **Operator confusion patterns** — operator gives ambiguous gate confirmation ("yeah, fine") and the system interprets
  it differently than they meant; only surfaces when an operator's natural language doesn't match what the agent
  expected.
- **State machine deadlocks** — agent A waits on agent B which waits on operator who's waiting on agent A. Real
  operators interrupt; the simulator might not, surfacing the deadlock.
- **Resume after interruption** — operator session ends mid-discovery; daemon restarts; PO is supposed to resume
  cleanly. A live-coded test catches the cases we wrote tests for; the simulator catches the cases we didn't.

The v2 designs assume parallel dispatch, mid-stream re-planning, multi-cycle conversations, cascade workflows, and a
dozen other things that interact non-trivially. We can't ship them with confidence without exercising the interactions.

## Simplest possible solution

**A single LLM-driven operator that runs through one scripted scenario, reports what broke.** One persona, one scenario,
one run. Driver invokes jig programmatically through the daemon API, reads what jig surfaces, decides what the operator
would say back based on the scenario script, sends responses back. Captures whether the scenario reached its expected
end state plus any errors thrown along the way.

That's the floor. About a day's engineering. Useful immediately for regression-testing one specific workflow path. Not
useful for: discovering edge cases (only one scenario), characterizing failure-mode distributions (only one persona), or
validating the design holistically (only one path through the system).

## Complications considered

- **Scale**: scenarios × personas × project shapes can explode quickly. A 5-persona × 10-scenario matrix × 3 project
  shapes is 150 runs per validation pass. Forces: scenario library design (categorize by what they exercise), parallel
  run infrastructure, coverage metrics so we can drop redundant runs.
- **Concurrency**: multiple simulator runs against the same daemon would interfere catastrophically. Forces: per-run
  isolation — separate `.jig/` state, separate daemon instance OR fresh project namespace per run. Existing
  dev-environment design's namespace isolation primitive applies here too.
- **Failure modes**: simulator hallucinates a failure that didn't happen (false positive); simulator misses a real
  failure because its persona's behavior doesn't trip the bug (false negative); simulator's success-criteria are too lax
  and miss subtle wrong-but-not-failed outcomes. Forces: test-of-the-test (does the simulator catch known broken
  commits?), explicit assertions per scenario, per-failure-mode regression scenarios.
- **Cross-cutting policies**: simulator output (analytics events from the simulated runs) must be tagged so it doesn't
  pollute real-project analytics corpora — `simulator: true` field on every event from a sim run. Forces: analytics
  emitter aware of "I'm running in simulator mode."

Other complications:

- **False confidence (the headline risk)**. Easy to build a simulator that passes scenarios real operators trivially
  break, then ship the system thinking it's validated. Forces: explicit "realism budget" — every time a real operator
  surprises the system in a way the simulator wouldn't have, log it and grow the simulator to cover it. Track
  simulator-vs-real divergence as a metric.
- **Authoring cost**. Hand-writing scenarios is tedious and biased toward what the author can imagine. Forces: scenario
  library grows over time; LLM-generation of scenarios from project archetypes is a v2.x extension; v2 ships with
  hand-curated initial set.
- **Persona definition is squishy**. "Methodical operator" and "ambivalent operator" are intuitive but not
  deterministic. Forces: each persona has a structured behavior profile (response patterns, gate-confirmation policies,
  override probabilities, etc.) the simulator-LLM treats as a system prompt, not just a vibe.

## Constraints

- **Must run in CI.** Scenarios need to be runnable as part of the automated test suite, not just operator-driven
  manually. Forces: deterministic enough output for assertions; per-run isolation is automated, not manual; cost per run
  is bounded.
- **Must use the same agent infrastructure as real runs.** A simulator that bypasses the daemon, the agents, the
  reviewers — running its own mock pipeline — wouldn't validate the real thing. The simulator drives the real daemon
  end-to-end; only the operator is synthetic.
- **Must respect cost.** Running 150-scenario validation passes nightly would cost real money. Forces: tiered scenarios
  (smoke / full / nightly), small models for the simulator-operator role, aggressive output caching.
- **Must produce actionable failure reports.** When a scenario fails, the operator needs to know what went wrong fast —
  which step failed, what the simulator expected, what jig actually did. Forces: per-step assertion results, structured
  failure objects, links into the run's analytics event stream.
- **Tenet 2 (exact context).** The simulator-operator agent operates against jig the same way a real operator would —
  limited context, no privileged access to internal state. Validates the operator-facing surface, not internals.

## Requirements

- A **scenario format** on disk — YAML scripts describing project shape + persona + scripted turns + assertions.
- A **persona library** — initial 3-5 personas with structured behavior profiles (methodical, fast-and-shippy,
  scope-creeper, ambivalent, hostile).
- A **simulator driver** — Python module that spawns or attaches to a daemon, runs a scenario, captures outcomes, tears
  down.
- **Per-run isolation** — each scenario gets a fresh `.jig/` state and dedicated daemon instance (or namespace-isolated
  within a shared daemon, leveraging the dev-environment isolation primitives).
- **Success-criteria framework** — per-scenario assertions covering workflow gates, artifact shape, expected outcomes,
  plus negative assertions (system should NOT do X).
- **Coverage metrics** — instrumented from analytics events so we can answer "across all sim runs, which workflow paths
  got exercised vs which never trip."
- **Realism budget tracking** — explicit queue of real-operator behaviors that surprised the simulator; periodic growth
  of the persona library to cover them.
- **Analytics tagging** — every event from a simulator run carries `simulator: true` so it doesn't pollute real corpora;
  analytics consumers filter accordingly.
- **CI integration** — smoke scenarios run on every PR, full-suite scenarios run nightly, nightly scenarios run weekly
  (cost-tiered).

## Non-goals

- **Replacing real operator testing.** Simulator catches what it knows to catch; operator-with-real-project catches what
  we didn't think of. Both required. Real-operator data feeds the realism budget; simulator catches regressions and
  pre-validates designs before the real-operator pain.
- **Generating scenarios from text descriptions.** Hand-curated initial library; LLM-generation is v2.x (eventually
  agent-driven testing as a whole — see `docs/agent-testing/`).
- **Performance / load testing.** Simulator focuses on correctness of workflow + interactions, not throughput or latency
  under load.
- **Simulating multi-operator collaboration.** Single-operator assumption per simulator run (matches v2 design's
  single-operator assumption).
- **Simulating non-operator failure modes** (network partitions, LLM API outages, etc.). Resilience testing is a
  separate concern.
- **Replacing pytest unit tests** for jig's own code. Simulator validates workflow-level behavior; pytest validates
  module-level correctness.

## Success criteria

- **Within v2 build phase**: simulator runs the bones-layer of the workflow against at least one synthetic project,
  catches at least one workflow design bug before it surfaces in a real run.
- **At v2 ship**: scenario library covers the canonical happy paths for each workflow stage (PO L0 / L1 / L2 / L3, SA
  discovery / cascade, VD wireframes, PM planning + dispatch, dev + reviewer cycles) plus 1-2 deliberate edge cases per
  stage. Smoke suite under 10 minutes; full suite under 1 hour; nightly under 8 hours.
- **3 months post-v2 ship**: realism budget shows steady growth (real operator behaviors flowing into simulator
  personas) AND simulator catches at least one design-bug-per-month before it reaches a real run. If neither — simulator
  isn't earning its keep; revisit.
- **No regression**: any v2 design change that breaks a passing scenario blocks merge until the design is fixed or the
  scenario is updated with rationale.

## Open questions

- [ ] **Simulator interaction surface — daemon API or TUI?** Driving through the daemon's command API is faster and
  easier; driving through the actual TUI (typing into the Composer) is more realistic but harder to script. Lean: daemon
  API by default with optional TUI-driving mode for scenarios that specifically test TUI behavior.
- [ ] **Persona behavior — scripted or policy-driven?** Pure scripts are deterministic but tedious; policy-driven ("this
  persona accepts gates 80% of the time") is more flexible but less predictable. Probably both — scripts for regression
  suites, policies for exploratory runs.
- [ ] **Per-run isolation level** — fresh daemon per run vs namespace-isolated within shared daemon? Fresh is cleaner
  but slower; namespace-isolated is faster but risks cross-run pollution if isolation has gaps. Probably start
  fresh-per-run, optimize to shared-with-namespacing when speed matters.
- [ ] **Where do scenarios live?** In jig's repo (`tests/scenarios/`)? In a separate scenario repo? Probably in-repo for
  v2 — operator iterates on them alongside the design.
- [ ] **Cost ceiling for simulator runs**. Setting a hard cap (e.g. "no scenario costs more than $X") or
  soft-warning-only? Probably hard-cap for individual scenarios with operator-overridable; aggregate budget per-day for
  nightly.
