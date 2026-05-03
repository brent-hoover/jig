---
title: Agent-Driven Testing — Problem Statement
type: problem
status: deferred
owner: brent
created: 2026-05-01
---

# Agent-Driven Testing — Problem Statement

## Context

Traditional test frameworks assume human authors and human readers: developers write tests, CI runs them, humans read
failure output and debug. The whole shape of the testing discipline — unit/integration/e2e split, mocking conventions,
stack-trace-as-failure-output, manual coverage analysis — is built around what humans can write and read at human speed.

Agents change every one of those assumptions. They can write tests at scale, infer invariants from spec, simulate users,
read structured failure reports better than prose. The opportunity is not "make agents do what testers do today,
faster"; it's "build a testing discipline that exploits what agents can do that humans can't."

The agent-leverage doc (`docs/agent-leverage/problem.md`) names synthetic operator simulation as one commitment. This
doc names the broader category and defers the full design.

## Problem

Today's jig has a small, hand-written test suite that exercises specific code paths. It tells us code works in the cases
the human who wrote it remembered. It says nothing about:

- **Whether the workflow design itself works** under operators the author didn't imagine.
- **Whether the spec is internally consistent** before any code is written against it.
- **Whether contracts hold** under combinations of inputs no human enumerated.
- **Whether known failure scenarios re-recur** after fixes.
- **Whether AC are actually testable** as written.

These are testing concerns that are uneconomical for humans to do well — the combinatorics defeat hand-written coverage,
the spec consistency check requires reading everything at once, the "would a different operator break this?" question
requires inhabiting different mindsets. All of them are tractable for agents.

## Simplest possible solution

For each kind of testing below: **a single agent reads the relevant artifacts and emits a structured report of what it
found.** No persistent infrastructure, no test harness, no runner — just an agent that reads X and produces Y. That's
the floor.

For workflow simulation specifically: a single LLM-driven operator runs through one scripted scenario, reports what
broke. One persona, one scenario, one run.

If that floor works for any of the categories below, complexity gets earned by need.

## Complications considered

- **Scale**: number of scenarios × number of personas × number of project shapes can explode. Forces: scenario library
  design, coverage metrics, parallelism. Probably matters once we have >5 scenarios; not at v2.
- **Concurrency**: multiple test agents running against the same project state. Forces: isolation per run (sandbox,
  fresh worktree, separate event store). Real but solvable with the existing sandbox primitives.
- **Failure modes**: test agent itself misbehaves (false positives, false negatives, hallucinates a failure that didn't
  happen). Forces: test-of-the-test (does the regression suite catch known-broken commits?). Critical for trust.
- **Cross-cutting policies**: the test framework itself emits analytics events; tests reference AC by URI; failure
  reports carry contract URIs they exercise. Forces: agent-first output format (structured, not stack traces).

## Six kinds worth pulling apart

These overlap in implementation but solve distinct problems:

| Kind                        | What it tests                                                                                                       | When it runs                                                  |
|-----------------------------|---------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| **Workflow simulation**     | The PO/SA/PM/dev/review workflow itself, end-to-end, with synthetic operators                                       | Pre-ship: validates workflow design before real-operator pain |
| **Pre-code spec testing**   | Spec internal consistency: do contracts compose? Do AC contradict? Are journeys covered by capabilities?            | After PO/SA pass, before tickets dispatch                     |
| **Property inference**      | Agent reads spec + AC, infers invariants ("if ticket resolved → must have merged commit"), generates tests for them | Continuous; tests live alongside code                         |
| **Adversarial fuzzing**     | Hostile operator simulator tries to break the orchestrator with malformed inputs, contradictions, edge cases        | CI; nightly; pre-release                                      |
| **Living regression suite** | Past failure scenarios automatically replayed                                                                       | On every commit; bugs literally don't repeat                  |
| **Test-as-spec-feedback**   | Test-generator agent flags "I can't write a test for this AC because it's ambiguous"                                | During PO authoring; surfaces spec gaps before SA             |

The last one is the kicker: **testing as a discovery loop, not just a verification activity.** Same shape as the
PO/SA/PM discovery loops; different layer. The act of trying to write a test for an under-specified contract surfaces
the under- specification before it locks into implementation.

## Why the framework itself should be agent-first

Today's test frameworks are designed for human readers. Ours doesn't have to be. Concrete consequences:

- **Failure reports are structured**, not stack traces. A failed test emits a pydantic record with: which AC failed,
  which contract URI was exercised, what input was given, what was expected, what happened.
- **Tests carry traceability to spec.** Every test has the AC URIs it covers in metadata. Reviewer agents can answer
  "which AC have no test coverage?" mechanically.
- **Test failures emit analytics events.** Linking test failure to ticket history to operator actions — full causal
  chain.
- **Tests are consumed by reviewer agents as evidence**, not just executed by CI.

This is its own design space, deferred but flagged.

## Why deferred

Same reasoning as `docs/learnings/`:

- The simple workflow-simulation slice (synthetic operator from agent-leverage) needs to ship first to inform what the
  broader framework should look like.
- Designing the full framework now risks building for testing patterns we *imagine* are needed rather than ones we
  *observe* are missing.
- The other five kinds (pre-code spec testing, property inference, adversarial fuzzing, regression suite, test-as-
  feedback) each have non-trivial design questions of their own that benefit from real data.

## What lands in v2 anyway (raw material capture)

- **Synthetic operator simulator** (per agent-leverage doc) — the one commitment from this category that ships with v2.
  Built early as testing infrastructure for the workflow design itself.
- **AC have stable URIs** — already in the spec design. Means future tests can cite them without retrofit.
- **Contract URIs are addressable at sub-contract granularity** — already an SA design commitment. Means future tests
  can exercise specific contract clauses.
- **Analytics events for test runs** — when test capture lands, emit `TestRun` and `TestFailed` events as a new event
  type category. Cheap; preserves data future test-framework design will consume.

## When to revisit

Trigger: **synthetic operator simulator has run against the v2 workflow for a meaningful corpus (10+ project
lifecycles), AND at least one operational gap surfaces that one of the other five kinds would catch.** Whichever
specific kind earns its way in first dictates the design priority.

## Constraints

- Must coexist with existing pytest suite. Not a replacement.
- Must respect tenet 2 — test agents have bounded context, same as dev/reviewer agents.
- Must not make the operator's life worse — test failures should surface in TUI in the same way as other agent outputs.

## Requirements (for the v2 simulator only)

- Multiple operator personas (3-5 to start).
- Scripted scenario library, growable.
- Per-run isolation (separate sandbox, separate event store).
- Coverage metrics — which workflow paths the simulator exercised vs which it never touched.
- Realism budget tracking — explicit queue of real-world operator behaviors that surprised the simulator; grow the
  simulator to cover them.

## Non-goals

- Replacing pytest or other code-level testing.
- Performance testing / load testing.
- Penetration testing / security fuzzing as such (different expertise; different threat model).
- Continuous integration tooling proper (we use what's there).
- Full-blown test-framework design before the simulator teaches us what's needed.

## Success criteria

- Within 6 months of synthetic operator landing: at least one workflow design choice has been validated or invalidated
  by the simulator before reaching a real operator.
- Within 12 months: at least two of the other five testing kinds have moved from this doc into their own designs,
  motivated by observed gaps.

## Open questions

- [ ] What's the right artifact format for a "scenario"? YAML script of operator actions? Free-form prose the simulator
  agent interprets? Both?
- [ ] How does the simulator handle non-determinism in agent outputs? Same scenario may behave differently on rerun.
  Statistical pass/fail rather than binary?
- [ ] Where do test failures surface — TUI? Separate report? CI artifact? All of the above?
- [ ] Are property-inferred tests *trusted* by reviewers, or treated as candidates needing human approval? (Same
  candidate/validated split as facts in `learnings/`.)
- [ ] Does the test-as-spec-feedback kind block PO finalization, or just surface as advisory?

## Change log

- 2026-05-01: Initial placeholder (brent + claude). Names six kinds of agent-driven testing, frames the agent-first
  framework opportunity, defers full design pending data from v2 synthetic operator. Commits raw-material capture (AC
  URIs, contract URIs, test analytics events) so the eventual design has data to work from.
