---
title: Test Rigor — Problem Statement
type: problem
status: active
owner: brent
created: 2026-05-22
updated: 2026-05-22
---

# Test Rigor — Problem Statement

## Context

Jig's per-ticket workflow looks roughly like:

```
test (write tests) → review-tests (judge adequacy) → dev (impl) → review (judge quality) → validate (run checks)
```

Each phase declares an `acceptance_criteria` string in the workflow YAML (e.g. the `implement` phase
in `feature-s.yaml` says `acceptance_criteria: "All tests pass"`). Phases are spawned, the agent runs,
the agent self-reports success or failure, the orchestrator moves to the next phase.

The hn-cli eval run on 2026-05-22 (`hn-cli-20260522T151548Z`) completed all 8 tickets but the
post-run analyzer caught a substantive correctness defect that escaped every review phase: the
`--type ask` and `--type show` filters were implemented and tested against synthetic fixtures that
encoded a fake API model. All tests passed. Against the live HN Firebase API both flags would
silently return zero results. The spec-generator had flagged the underlying API-semantic ambiguity
as an advisory; nobody resolved it; the test fixtures perpetuated the wrong assumption; the
implementation matched the wrong fixtures; reviewers couldn't see the gap because the tests *did*
pass.

This is one instance of a broader class: jig has the right phase boundaries but doesn't enforce
discipline at the boundaries. The acceptance-criteria string is prose the agent reads, not a
machine check. Three distinct gaps:

## Problem

### 1. Dev agent's "I'm done" is unverified

The dev phase declares `acceptance_criteria: "All tests pass"`, but the orchestrator advances on
agent self-report. An agent that gives up with tests still red, an agent that forgets to run the
tests, and an agent that finishes with everything green all produce indistinguishable outcomes
(`result.status == "success"`). The reviewer-generalist catches some of this at the next phase as a
review finding — but that's a round-trip after the orchestrator already considered the implement
phase complete, not a bounce that keeps the agent in the same phase until it actually finishes the
job.

**Mechanism exists, not wired.** `run_handoff_gate` (`jig/handoff_gate.py:50`) reads
`PhaseConfig.automated_checks` from the workflow YAML and bounces failing handoffs back to the
agent. But no shipped workflow declares any `automated_checks` and jig ships no check catalog. The
gate runs against an empty check list and returns pass-by-default for every phase.

### 2. Test phase doesn't enforce RED discipline

The test phase fires before dev. The test agent commits tests. Currently there's no check that the
*new* tests in the diff actually *fail* at commit time. Consequences:

- A test that's GREEN at commit time is either testing existing behavior dressed up as new, or
  asserting on shape / mock-self instead of real behavior. The test author has nothing forcing them
  to confront this.
- The `--type ask/show` failure mode fits here: had the test author been required to *see the test
  fail*, they would have had to think about what "fail" looks like — and might have surfaced the
  API-semantic ambiguity instead of papering over it with synthetic fixtures.

This is a mechanical check (did this test fail?), not a judgment call. Belongs in the same gating
machinery as concern (1).

### 3. No reviewer owns test code quality or fixture correctness

`reviewer-test-adequacy` is explicitly scoped to "does each AC bullet have a covering test?" and
explicitly disclaims style, error-handling tests, and impl-side concerns — handing those off to
other reviewers (per its prompt). The handoffs are aspirational: `reviewer-pattern-conformance` is
general-purpose code style and doesn't drill into pytest idioms; no reviewer owns fixture
correctness against external-system behavior (the `--type ask/show` case).

Concrete gaps observed on hn-cli:

- `_run_cli` helper duplicated across `test_cli.py` and `test_filtering.py`. Reviewer flagged as
  advisory, not actioned, fix not enforced.
- Fixtures for `type: "ask"` and `type: "show"` encoded a fake API model. No reviewer
  cross-referenced against spec-generator advisories.

## Simplest possible solution

Wire the gating machinery that already exists. Ship a check catalog and declare the right checks
on each workflow phase, scoped to the *current ticket's diff* (not the whole project) for the
phase-level gates — so the foundation works on a 100-test CLI and a 10,000-test platform alike.

| Phase | `automated_checks` | Scope |
|---|---|---|
| `test` | `pytest-new-tests-fail` | Only the new test functions added in this ticket's diff. RED — must all fail. |
| `implement` (dev) | `pytest-diff-tests` | Only the tests in this ticket's test diff. GREEN — must all pass. |
| `validate` | `pytest-all`, `ruff-check`, `mypy-strict` | Full suite + lint + mypy. Catches regressions and integration breaks. |

The test and implement gates run a focused subset; the validate gate runs everything. This means
per-phase gates stay fast on large projects (the dev's worktree contains only its own changes;
running just the new tests is `pytest <handful-of-files>`) while regressions still get caught at
the end-of-ticket boundary where running the full suite is the right cost.

This closes the dev-self-report loophole and adds RED-GREEN discipline at the test phase. Both via
mechanical checks the agent has to actually pass — no new judgment reviewer needed.

Add a focused `reviewer-test-quality` role at the end-of-ticket review phase for the judgment
concerns adequacy explicitly punts: test code quality (duplication, conftest usage), fixture
correctness vs. spec-generator advisories, pytest idiom conformance. This is a separate, smaller
PR layered on top of the foundation.

## Complications considered

- **Scale**: gate runs per phase per ticket. The phase-level gates (`test`, `implement`) run only
  on the diff's tests — `pytest <few-files>` is fast even on multi-thousand-test projects. The
  full-suite cost lands at `validate`, once per ticket, which is the right place for it. The
  alternative is paying that cost later as a round-trip review finding that triggers an LLM-driven
  retry — which is more expensive in time, tokens, and operator surprise.

- **Diff scope**: each ticket runs in its own git worktree. The phase-level checks inspect
  `git diff` against the ticket's branch base to identify added/modified test functions. A small
  helper that parses `pytest --collect-only` output against the diff gives the exact node-ids to
  pass to pytest as positional args. (Implementation detail for design.md.)

- **Concurrency**: handoff gate already serialises through the orchestrator's existing per-ticket
  state machine; this work doesn't change concurrency invariants.

- **Failure modes**: a check that itself fails to run (catalog entry references a missing binary,
  worktree state is wrong) should fail the gate, not silently pass. `evaluate_handoff_gate` already
  treats "missing required check result" as a fail — that semantics carries.

- **Cross-cutting policies**: N/A — touches no PII, auth, or secrets. Check execution runs inside
  the existing bwrap sandbox boundary.

- **RED-discipline edge cases**:

  - Some new tests legitimately pass at commit if the dependency they exercise was scaffolded in an
    earlier ticket. The check needs an opt-out marker (e.g. `# tdd-baseline` comment in the test
    function, or per-test exemption in the test author's commit message body). Without an explicit
    marker the check fires.

  - Parametrized tests, fixtures, conftest helpers — the check operates on test *functions* in the
    diff, not test *files*. A `conftest.py` addition that adds a fixture but no test is not
    subject to red-discipline. A `test_*.py` file with new `def test_*` functions is.

- **Custom workflows / operator override**: profiles already copy workflow YAMLs into
  `.jig/workflows/` so operators can edit them per-project. Operators who don't want red-discipline
  for some workflow can drop the check from their copy. The default is opt-in via the shipped
  workflow YAMLs.

- **Profile interaction**: project-profiles already chooses which workflow YAML governs each ticket
  size. The `small` profile's `feature-s.yaml` gets the basic checks; `medium`'s `feature-s-full.yaml`
  and `default.yaml` get the same checks (and could declare additional ones — e.g. coverage
  thresholds — without breaking the small path).

## Out of scope

- **Per-ticket coverage thresholds.** Possible later; the catalog framework allows declaring a
  threshold check, but choosing the threshold is a separate conversation.

- **Reviewer-side enforcement of TDD.** RED discipline is a check, not a review. The mechanical
  check is the right place.

- **Adequacy reviewer rewrite.** It already does its job; the gaps it punts are filled by the new
  quality reviewer (layer 3), not by changing adequacy.

- **Architecture-finalize integration for sa_mvp.** Orthogonal feature in flight elsewhere.

## What this does NOT change

- The existing `acceptance_criteria` prose strings on each phase. They remain as guidance for the
  agent's prompt, complementing the machine check rather than replacing it.

- The dev or test role prompts. Behavior changes via the gate mechanism, not via prompt edits to
  the agents.

- `reviewer-test-adequacy` — stays as it is. Layer 3 (quality reviewer) is additive.

- Existing eval projects. They re-init from brief and pick up the new shipped workflows + catalog.

## Done when

- Shipped check catalog includes `pytest-all`, `pytest-diff-tests`, `pytest-new-tests-fail`,
  `ruff-check`, `mypy-strict` entries.
- `feature-s.yaml`, `feature-s-full.yaml`, and `default.yaml` declare `automated_checks` on the
  `test` (`pytest-new-tests-fail`), `implement` (`pytest-diff-tests`), and `validate`
  (`pytest-all`, `ruff-check`, `mypy-strict`) phases.
- A run that completes the `implement` phase with failing diff-tests gets *bounced* back to dev
  (`rejection_reason` set on the handoff), not advanced to review.
- A test agent that commits new tests passing immediately gets bounced back to test
  (`rejection_reason` cites the green-at-commit tests).
- The phase-level checks scope to the current ticket's diff so a 10,000-test project doesn't pay
  full-suite cost per phase.
- New e2e test exercises both bounce paths end-to-end against the fake agent runner.
- (Layer 3, follow-up PR) `reviewer-test-quality` role exists and runs at the `review` phase.

## Change log

- 2026-05-22: Initial draft (brent)
