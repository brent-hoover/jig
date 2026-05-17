---
title: Review Routing — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-17
updated: 2026-05-17
---

# Review Routing — Problem Statement

## Context

The default workflow runs `spec → test → implement(dev) → review → validate → document`. The `test` phase
writes test files which then become locked TDD red-phase artifacts; the `implement` phase writes production
code to make those tests pass and is structurally forbidden from modifying anything under `tests/`.

The `review` phase runs the multi-reviewer federation (`run_review_federation`) once at end-of-ticket
against the full diff. When any reviewer raises an `important` or `critical` finding, the orchestrator's
fix loop routes the ticket back to the most recent phase whose role is in `_write_roles = {"dev"}`
(`jig/orchestrator.py:2589`). After `max_fix_cycles` blocked outcomes (default 4), the ticket is marked
FAILED.

Reviewer comments are persisted as typed records in `.jig/store/review_comments.jsonl`
(`ReviewCommentsStore`) and already carry a `.file` field pointing at the target path.

## Problem

The current routing is role-blind and end-of-ticket-only, which produces an unrecoverable failure mode any
time a reviewer flags a finding in a file the dev role cannot touch:

1. Federation runs at end-of-ticket, sees the full diff including `tests/**` (written by the `test` role,
   locked thereafter).
2. A reviewer (in our observed case `reviewer-pattern-conformance` and `reviewer-test-adequacy`) raises an
   `important` finding rooted in a test file. The finding includes the file path and often a one-line
   `suggested_diff`.
3. The fix loop ignores `comment.file` and routes unconditionally to the most recent dev phase.
4. The dev agent cannot legally modify the offending file. It thrashes — typically attempting workarounds
   in adjacent files (e.g. broadening `pyproject.toml` ruff exclusions to silence the lint complaint).
5. Each new cycle, the same reviewer raises the same finding. After 4 cycles the ticket is marked FAILED
   with reason "phase review blocked 4 times — giving up".

Observed example: ticket `240db21f` in the `hn-cli` eval project. Five `important` findings across cycles
0–4, all targeting `tests/test_filter_flags.py` or a `pyproject.toml` workaround for that test file. The
reviewer literally supplied the two-line fix as a `suggested_diff`; the dev role had no path to apply it.

The same architectural mismatch will recur for any reviewer that has a legitimate opinion about test
quality, since:

- Test-quality issues should be caught **before** dev runs, not after — dev wastes its work writing
  against tests the system already considers defective.
- Even when test issues surface late, they belong with the test author, not the dev.

## Simplest possible solution

Two small changes that compose:

1. **Add a `review-tests` phase between `test` and `implement`.** Run only the test-focused reviewers (at
   minimum `reviewer-test-adequacy`) against test files. Block routes back to `test`.
2. **In the end-of-ticket fix loop, route by `comment.file` → owning role**, not by hard-coded
   `_write_roles = {"dev"}`. Group blocking comments by owning role (derived from the file path against
   the workflow's phase→writes-paths map) and re-run each owning phase.

Move test-focused reviewers out of the `end_of_ticket` cadence so they fire only at the new phase.
Non-test reviewers continue to run end-of-ticket.

## Complications considered

- **Scale**: N/A — bounded by number of phases (≤10 in practice) and number of reviewer comments per
  cycle (federation already caps this).
- **Concurrency**: N/A — phase loop is per-ticket single-writer; review federation already runs reviewers
  in parallel and merges results before routing.
- **Failure modes**: A reviewer that targets a file with no clearly-owning role (e.g. cross-cutting
  config like `pyproject.toml`) needs a fallback owner. Default to `dev` preserves current behavior;
  must be explicit so this case fails predictably, not silently.
- **Cross-cutting policies**: N/A — no PII / auth / audit dimensions.
- **Backwards-compatibility**: the default workflow (`jig/defaults/workflows/default.yaml`) ships the
  new phase. User-authored workflow YAMLs need to gain it; they don't get the routing fix for free if
  they omit it, which is correct — any workflow that locks test artifacts but skips post-test review is
  re-introducing the exact failure mode this work fixes. Workflows that legitimately have no `test`
  phase (e.g. `canonicalize.yaml`, `docs.yaml`) skip the new phase naturally and are unaffected.
- **Reviewer scope ambiguity**: a single reviewer may produce findings spanning test files and src files
  (`reviewer-pattern-conformance` did this in cycle 1). Per-finding routing (option from §"Simplest", #2)
  handles this cleanly; per-reviewer scoping does not.

## Constraints

- TDD red-phase invariant: once the `test` phase commits its output, files under `tests/**` are not
  editable by later phases. This is enforced by convention + role configuration; the routing change must
  not weaken it.
- Workflow phases are configured in YAML (`.jig/workflows/<name>.yaml`, falling back to
  `jig/defaults/workflows/default.yaml`); any new phase must work within that schema.
- Reviewer cadence is configured per-reviewer (`end_of_ticket` today). Moving a reviewer to a different
  cadence must not break reviewers that legitimately want end-of-ticket scope.
- `ReviewerComment.file` is already present on stored comments; routing must consult it rather than
  reinventing a routing key.

## Requirements

- A new workflow phase that runs a subset of reviewers against the just-written test files, between
  the `test` phase and the `implement` phase. Mandatory for any workflow that has a `test` phase whose
  output is treated as locked from later phases — i.e. the default TDD flow. Workflows without a `test`
  phase don't need it.
- Fix-loop routing in the end-of-ticket review must select the target phase per-finding using
  `comment.file`, not via a global write-role list.
- When the new test-review phase blocks, the fix loop routes back to the `test` role's most recent phase.
- A finding whose `file` has no owning role falls back to the current behavior (route to most recent
  dev phase) with an explicit log line so the case is observable.
- The `_find_fix_phase` change must produce a deterministic re-run target (single phase) per cycle; if
  multiple roles are implicated, choose the earliest phase that owns at least one blocking finding so the
  flow walks forward in phase order on each retry.

## Non-goals

- The cul-de-sac / cascade-failure handling — when a ticket is marked FAILED, downstream tickets blocked
  on it stay deferred forever. This is a separate problem (`_on_ticket_failed` doesn't propagate failure
  to dependents) and gets its own feature.
- Operator discoverability of reviewer findings — `jig story` doesn't surface
  `.jig/store/review_comments.jsonl`. Separate feature.
- Reworking the federation itself (which reviewers exist, how they vote, severity calibration). This work
  only changes *when* reviewers fire and *where* their findings route.
- Cross-ticket review routing (e.g. spec-phase reviewers commenting on dependent tickets).

## Success criteria

- A ticket that previously failed because every `important` finding targeted a test file now either
  (a) passes after the test-review phase rejects + re-runs the test phase, or
  (b) routes its end-of-ticket test-file findings back to `test` rather than `dev`, and converges in
      ≤ 2 cycles per role.
- For the observed `240db21f` failure pattern, replaying the same reviewer findings against the new
  routing produces a successful resolution (or fails on a *different* reason — not on dev-can't-edit-tests
  thrash).
- No regression in tickets that have no test-targeted findings: end-of-ticket routing to dev is
  preserved for findings against `src/**` and root-level config files.

## Open questions

- [ ] Which reviewers move to the new test-review phase vs. stay at end-of-ticket?
      `reviewer-test-adequacy` is the obvious mover. `reviewer-pattern-conformance` produced findings on
      both test files and `pyproject.toml` in our observed case — split it, run it at both cadences with
      different file-scopes, or leave it end-of-ticket and rely on routing?
- [ ] Where does the file→role mapping live? Per-workflow (each phase declares `writes: [glob...]`), or
      a central convention (`tests/** → test`, `src/** → dev`, etc.)? Per-workflow is more flexible;
      central is one less thing to configure.
- [ ] Fallback owner for unowned files (`pyproject.toml`, `README.md`, top-level configs): always dev,
      configurable per-workflow, or refused outright with a clear error to the operator?
- [ ] When a blocking finding targets a file under both `test` and `dev` ownership (e.g. shared
      fixtures), what role owns it? Likely test, but worth confirming.

## Change log

- 2026-05-17: Initial draft (brent)
