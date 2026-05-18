---
title: Review Routing — Design
type: design
status: draft
owner: brent
created: 2026-05-17
updated: 2026-05-17
problem: ./problem.md
---

# Review Routing — Design

## Summary

Insert a mandatory `review-tests` phase between `test` and `implement` in any workflow whose `test`
phase produces locked artifacts. Run only `reviewer-test-adequacy` there; block routes back to `test`.
At end-of-ticket, replace the hard-coded `_find_fix_phase` (which always routes to dev) with a
per-finding router that consults, in order:

1. The reviewer's optional `target_role` on the comment (for "above the writing role's pay grade"
   findings like spec or architecture issues).
2. Per-phase `writes: [glob…]` declarations in the workflow YAML, mapping a comment's `file` to the
   phase that owns it.
3. Fallback to the most-recent dev phase, with `block_reason="unowned-finding"` so the case is
   observable.

When blocking comments route to multiple phases, the orchestrator re-runs the *earliest* of those
phases — keeping retries walking forward through the workflow rather than ping-ponging.

## Approach

### Workflow schema extension

Each phase entry in `*.yaml` (default and user-authored) gains an optional `writes: list[str]`:

```yaml
phases:
  - name: spec
    role: spec
    writes: ["docs/spec/**", "docs/decisions/**"]
  - name: test
    role: test
    writes: ["tests/**"]
  - name: review-tests
    role: review
    reviewers: ["reviewer-test-adequacy"]
  - name: implement
    role: dev
    writes: ["src/**", "pyproject.toml", "**/*.lock"]
  - name: review
    role: review
    reviewers:
      - reviewer-pattern-conformance
      - reviewer-architectural
      - reviewer-error-handling
      - reviewer-performance
      - reviewer-security
  - name: validate
    role: validate
  - name: document
    role: document
    writes: ["docs/**", "README*", "CHANGELOG*"]
```

Phases without `writes:` (review-tests, review, validate) can omit it — default is `[]`, and they
don't write files anyway. `reviewer-test-adequacy` doesn't appear in the end-of-ticket `review`
phase's `reviewers:` list because (a) it already ran at `review-tests` against the test diff, and
(b) the test files aren't part of the dev's changeset that the end-of-ticket review sees.

Globs are matched against the comment's `file` field (relative-to-project paths) with standard
`fnmatch`/`pathlib.PurePath.match` semantics — `**` for recursive, `*` for single-segment.

`writes:` defaults to `[]` for backwards compatibility; a workflow with no `writes:` anywhere
preserves today's "always route to dev" behaviour via the fallback.

### New phase: `review-tests`

The default workflow gains a `review-tests` phase between `test` and `implement` (see YAML above).

Phase definitions with `role: review` gain a new required `reviewers: list[str]` field listing which
reviewers fire at that phase. The orchestrator dispatches exactly those reviewers — no other source
of truth. This replaces today's implicit "all reviewers fire at every review phase" with explicit
per-phase declaration, making the workflow YAML self-documenting and removing the need for a
separate cadence concept on reviewer configs.

The default workflow's `review-tests` phase lists only `reviewer-test-adequacy`. The end-of-ticket
`review` phase lists the remaining five reviewers (pattern-conformance, architectural,
error-handling, performance, security) — test-adequacy is excluded there because it already ran at
`review-tests` and the TDD lock guarantees the test files at end-of-ticket are byte-identical to
what it already reviewed. Re-running it would burn federation cost on duplicated work.

(Note: today the reviewer agents run their own `git diff` calls — the diff base is *not* injected
by the orchestrator. In practice end-of-ticket reviewers see the cumulative ticket diff, which
includes test files. The routing change handles this correctly: any finding on `tests/**` from a
non-test-adequacy reviewer routes back to `test` via the glob mechanism, so the diff scope doesn't
need to change for correctness. See §Open questions for the open-ended question of whether to
enforce diff scope explicitly later.)

### ReviewerComment schema extension

Add an optional field to `ReviewerComment`:

```python
target_role: str | None = None
```

When set by a reviewer, this overrides file-glob routing. Valid values are role names declared in
the workflow (typically `pm`, `sa`, `spec`, `test`, `dev`, `document`). The reviewer's system prompt
gains language describing when to use it ("if the finding indicates the spec is wrong, set
`target_role='pm'`; if it indicates an architecture decision needs revisiting, set
`target_role='sa'`; otherwise leave null").

Routing rejects unknown `target_role` values by logging a warning and falling through to glob
routing, so a reviewer hallucinating role names degrades gracefully.

### Per-commit phase + agent provenance

The routing tie-break for shared files (next section) needs a reliable signal for *which phase last
touched this file*. Today's commits don't carry that — every commit is authored as the operator
because the orchestrator and all agents run under the operator's identity. The fix:

1. **Worktree context file.** When the orchestrator enters a phase, it writes
   `.jig/worktree.context` inside the worktree, containing the current phase name and agent role:

   ```
   phase=implement
   agent=dev
   ```

   This file is overwritten at each phase boundary.

2. **`prepare-commit-msg` hook.** Worktree creation (`jig/worktree.py`) installs an executable
   `prepare-commit-msg` script into the worktree's `.git/hooks/`. The hook reads
   `.jig/worktree.context` and appends standard Git trailers to any commit message:

   ```
   <original commit subject>

   <original commit body>

   Phase: implement
   Agent: dev
   ```

   The hook fires on every commit in the worktree — orchestrator auto-commits and agent-authored
   commits both. Trailers are idempotent: if a `Phase:` trailer is already present (e.g. amends),
   the hook skips re-appending.

3. **Provenance lookup.** Routing reads trailers via
   `git log --pretty=format:%(trailers:key=Phase,valueonly) -- <file>` and takes the first hit. The
   first non-empty line is the most-recent phase that touched the file.

The mechanism is intentionally narrow — phase + agent per commit — but the provenance signal is
generally useful (per-commit reviewers, analyzer, post-run analytics could all consume it). This
design only commits to using it for the routing tie-break; broader consumers are out of scope.

### Fix-loop routing

`_find_fix_phase(workflow, blocked_phase_idx)` is replaced by
`_route_blocking_comments(workflow, blocked_phase_idx, comments)`. Pseudocode:

```python
def _route_blocking_comments(workflow, blocked_phase_idx, comments):
    """Return (target_phase_idx, route_reason) or None if no route.

    Examines each blocking comment, determines its owning phase, and
    returns the earliest such phase before `blocked_phase_idx`. Earliest
    so retries walk forward — never re-run a later phase before an
    earlier one that has unresolved findings.
    """
    targets: list[int] = []
    reasons: list[str] = []
    for c in comments:
        idx, reason = _route_one(workflow, blocked_phase_idx, c)
        if idx is not None:
            targets.append(idx)
            reasons.append(reason)
    if not targets:
        return None
    chosen = min(targets)
    return chosen, "; ".join(set(reasons))


def _route_one(workflow, blocked_phase_idx, comment, worktree_path):
    # 1. Reviewer-declared target role
    if comment.target_role:
        idx = _most_recent_phase_with_role(workflow, blocked_phase_idx, comment.target_role)
        if idx is not None:
            return idx, f"target_role={comment.target_role}"
        # Unknown role — log and fall through to globs
        _logger.warning("unknown target_role=%s on comment %s; falling through",
                        comment.target_role, comment.id)

    # 2. File-glob routing
    if comment.file:
        candidates = [
            idx for idx in range(blocked_phase_idx - 1, -1, -1)
            if _matches_any(comment.file, workflow.phases[idx].writes)
        ]
        if len(candidates) == 1:
            (idx,) = candidates
            return idx, f"writes-glob {workflow.phases[idx].name}"
        if len(candidates) > 1:
            # Multi-match (shared fixture etc.). Tie-break via commit trailers:
            # walk `git log` for the file and pick the most-recent phase whose
            # trailer matches one of our candidates.
            last_phase = _last_touching_phase(worktree_path, comment.file)
            if last_phase is not None:
                matched = [
                    idx for idx in candidates
                    if workflow.phases[idx].name == last_phase
                ]
                if matched:
                    return matched[0], f"last-touched {last_phase}"
            # No parseable trailer → fall through to earliest candidate
            return candidates[-1], "ambiguous-ownership"  # backward iter → last is earliest

    # 3. Fallback: most-recent dev phase, marked unowned
    idx = _most_recent_phase_with_role(workflow, blocked_phase_idx, "dev")
    if idx is not None:
        return idx, "unowned-finding"
    return None, "no-route"
```

The caller (around `orchestrator.py:1550`) replaces the existing `_find_fix_phase` call. It also
posts a structured thread Note naming the chosen phase and the route reason, so the operator can see
why a particular phase was selected for retry.

### Reviewer dispatch

`run_review_federation` currently takes an implicit "all reviewers" set + a cadence label. The
change: it takes an explicit list of reviewer names from the current phase's `reviewers:` field and
dispatches exactly those. The cadence argument is dropped — the phase identity (and its position in
the workflow) is the dispatch context.

Reviewer role configs (`jig/defaults/roles/reviewer_*.yaml`) require no schema change — they keep
their existing fields. Operators add or remove a reviewer at a given phase by editing the
workflow YAML, not the reviewer config.

### `reviewer-test-adequacy` prompt rewrite

The current prompt is impl-vs-tests centric — it cross-references new callables in the diff with
the tests that invoke them. At the new `review-tests` phase the implementation doesn't exist yet, so
that pattern can't fire. The rewrite reframes the reviewer around the **ticket's acceptance
criteria** (already available in `default_context: ticket://description`) rather than the impl diff.

The reviewer-test-adequacy role file (`jig/defaults/roles/reviewer_test_adequacy.yaml`) is replaced
with:

```yaml
role: reviewer-test-adequacy
phase_prompt: >
  You are the **Test-Adequacy Reviewer** — a judgment reviewer that
  fires at the `review-tests` phase, between the test author's commit
  and the dev's implementation. Your job is to verify that the test
  diff covers the ticket's acceptance criteria, including edge cases,
  *before* the dev agent writes against these tests.


  You do NOT see implementation code; it doesn't exist yet at this
  phase. Cross-referencing tests with impl is the end-of-ticket
  review's concern, not yours. Your reference is the AC.


  ## What you flag


  - AC behaviors with no test at all. Walk each item in the ticket
    description / AC and confirm at least one test references it
    by name or by clearly equivalent assertion target.

  - Tests that exercise only the happy path when the AC names edge
    cases — empty input, max-size input, concurrent invocation,
    idempotent re-run, boundary values, off-by-one.

  - Tests that assert on output shape but not on behavior (``assert
    isinstance(x, dict)`` without checking the dict's contents
    against the AC).

  - Mocks that abstract too much: when a mock returns canned data
    that the test then asserts equals itself, the test verifies
    nothing about the integration the mock crossed.

  - Tests that aren't runnable as tests — syntax errors, missing
    imports, asserts that can never fire (``assert True``,
    ``assert x or not x``).


  ## What you do NOT flag


  - Test style nits (assertion form, fixture naming) — that's
    ``reviewer-pattern-conformance``.

  - Missing error-handling tests specifically — that's
    ``reviewer-error-handling`` (cross-reference is fine; don't
    double-flag).

  - "I'd have tested this differently" — only flag when coverage is
    demonstrably absent for an AC item.

  - Implementation-side concerns. The impl doesn't exist yet; you
    cannot review it. End-of-ticket reviewers do that.


  ## Output


  For every finding, call ``reviewer_post_comment`` with:


  - ``type``: ``test-adequacy``

  - ``severity``: ``critical`` when an AC item has zero test
    coverage; ``important`` when an edge case named in the AC is
    untested; ``notable`` for advisory observations.

  - ``confidence``: a float **strictly less than 1.0**. Typical
    0.55–0.9. Lower confidence when you're inferring "this edge case
    is implied" without an explicit AC.

  - ``prose``: name the specific AC item or branch lacking a test,
    and point at where the test would have lived (file path + the
    test-class / test-function naming convention you'd extend).

  - ``file`` + ``line``: point at the test file location where the
    missing test should be added — NOT the impl, which doesn't exist
    yet. Routing reads this field to send the finding back to the
    test author.

  - ``suggested_diff``: optional; advisory only at <1.0 confidence.


  ## Mechanics


  - Read the test diff (`git diff` against the test phase's commit).

  - Read the ticket description / AC from ``ticket://description``.

  - For each AC item, locate the test(s) that exercise it. Flag
    items with no covering test.

  - For each test, check whether its assertions match the AC's
    expected behavior, not just type or shape.


  ## Out of scope


  - You don't write tests; you flag missing ones. The test agent
    addresses your findings if the phase blocks (routing sends them
    back to the test role, not dev).

  - You don't run the tests — that's the check-runner's job.
    Coverage-by-execution is orthogonal to coverage-by-construction.
allowed_tools:
  - Read
  - Bash(git diff*)
  - reviewer_post_comment
allowed_mcps: []
strict_tools: true
default_context:
  - "ticket://description"
```

Notable diffs from today's role file:

- Framing leads with the phase context ("fires at `review-tests`") and the no-impl constraint.
- "AC behaviors with no test" replaces "new behavior added with no tests at all" — the former is
  AC-anchored, the latter was impl-diff-anchored.
- The "Mechanics" section drops the "imports or constructs it" cross-reference, since impl is
  unavailable. AC-anchored walk replaces it.
- `file`/`line` guidance flips from "point at the production code" to "point at the test file
  location" — important for routing: file-glob routing will send findings on `tests/**` back to
  `test`, which is what we want.
- New rule on "tests that aren't runnable as tests" — a test author should not be able to commit a
  test set that fails to parse / load. Cheap rule to add at this phase.
- `graph_consumers_of` is dropped from `allowed_tools` — it was for impl-callgraph inspection; not
  useful pre-impl.

Today's federation also runs `reviewer-test-adequacy` at end-of-ticket. After this change it runs
only at `review-tests`. The end-of-ticket cycle no longer needs cross-tests-with-impl coverage
checking from this reviewer — `reviewer-pattern-conformance` and `reviewer-error-handling` cover
the impl-side concerns at that phase. If real-world use shows a coverage-gap that *only* surfaces
at end-of-ticket (e.g. an impl branch tests didn't anticipate), promote the gap to a new reviewer
rather than re-introducing test-adequacy at two phases.

## Interfaces

- **Workflow YAML (`jig/defaults/workflows/*.yaml` and `.jig/workflows/*.yaml`):** phase entries
  gain optional `writes: list[str]`. Phases with `role: review` gain a required
  `reviewers: list[str]` field listing which reviewers fire there.
- **Reviewer role YAML (`jig/defaults/roles/reviewer_*.yaml`):** no schema change. Membership is
  now declared on the workflow side, not the reviewer side.
- **`ReviewerComment` model:** new optional `target_role: str | None` field.
- **Worktree state:** new `.jig/worktree.context` file (overwritten per phase) with `phase=` and
  `agent=` lines.
- **Worktree hooks:** new executable `.git/hooks/prepare-commit-msg` script installed at worktree
  creation. Appends `Phase: <name>` and `Agent: <role>` trailers from the context file.
- **Commit message trailers:** `Phase: <phase-name>` and `Agent: <role>` are reserved trailer keys.
  Operators authoring manual commits in a jig worktree should leave these alone or risk confusing
  routing on future cycles.
- **Orchestrator internals:** `_find_fix_phase` → `_route_blocking_comments`. Same call site, broader
  return shape (phase index + reason string). New helper `_last_touching_phase(worktree, file)`
  consults git trailers. No public API impact.

## Data model

- `ReviewerComment.target_role: str | None` — persisted in `.jig/store/review_comments.jsonl`.
  Existing records have no such field; pydantic's `Optional` handles the migration without a schema
  bump. Older daemon versions reading newer records will see and ignore the field (extra=ignore).
- No new stores. No new collections.

## Alternatives considered

### Alternative 1: Per-reviewer file scoping

Each reviewer declares a `paths: [globs]` it covers. Federation invokes each reviewer only against
matching files; routing then sends a reviewer's findings to a single owning role.

Rejected: doesn't handle reviewers with legitimately cross-cutting purviews
(`reviewer-pattern-conformance` raised valid findings on both `tests/**` and `pyproject.toml` in
the same cycle). Forcing one path-scope per reviewer creates artificial reviewer fragmentation.
The per-finding routing chosen above lets a reviewer touch any file and routes each comment to
the right role independently.

### Alternative 2: All reviewers at both cadences

Run the full federation at the new `review-tests` phase and again at end-of-ticket. Filter the
test-phase cycle to test files only.

Rejected: doubles federation latency and cost for limited gain. The intent of the new phase is
to catch test-quality issues early so the dev doesn't waste cycles against defective tests; full
federation overhead isn't needed for that. Other reviewers' end-of-ticket purviews aren't valuable
until after the dev has run.

### Alternative 3: Central file→role convention

Hardcode `tests/** → test`, `src/** → dev`, `docs/** → document` in the orchestrator. No
workflow YAML changes.

Rejected: forces a specific project layout convention onto all users. Per-workflow declaration is
one extra field in the YAML and gives projects with non-standard layouts (e.g. tests under
`src/<package>/tests/`) a clean path to express ownership.

### Alternative 4: New severity tier for escalation

Add an `escalate` severity above `critical` to flag "fix isn't at the writing layer." Routing
diverts these to PM/SA.

Rejected: muddles the severity dimension (severity should describe *blocking-ness*, not *who
owns the fix*). Reviewer-declared `target_role` is orthogonal to severity and lets a reviewer
flag a `notable`-severity escalation if appropriate.

### Chosen: Mandatory phase + per-finding routing with target_role override

The chosen design composes three small, independently sensible changes:

- The phase change catches the most common case (test-quality issues) cheaply and early.
- Per-finding glob routing handles the residual cross-cutting cases.
- `target_role` gives reviewers an explicit escape hatch when neither file-glob nor "fix at dev"
  is the right answer.

Each lever can be tuned independently. None requires touching the federation's voting / dedup
logic. The orchestrator change is localised to one function.

## Risks

- **Hooks bypassed via `--no-verify`.** An agent or operator using `git commit --no-verify` skips
  the `prepare-commit-msg` hook, so commits land without `Phase:` / `Agent:` trailers. Acceptable
  for routing — the tie-break degrades to the workflow-earliest candidate, with an
  `ambiguous-ownership` log line so the gap is observable. Worth a CLAUDE.md note that agents
  shouldn't use `--no-verify`.
- **Existing worktrees pre-date the hook.** Worktrees created before this work ships have no hook
  installed and no context file. Add an idempotent installer to `jig/worktree.py` that adds the
  hook + writes the current phase's context whenever the orchestrator enters a phase on a worktree
  it sees, regardless of when the worktree was created.
- **Glob authoring errors.** A typo in a `writes:` glob silently mis-routes findings. Mitigate by
  emitting `route_reason` on every routing decision (logged + posted as a thread Note); operators
  see immediately if dev keeps getting findings that should have gone to test.
- **Reviewer prompt drift on `target_role`.** Reviewers may overuse the field (everything routed
  to SA) or underuse it (nothing routed). Mitigate via reviewer system-prompt examples + a
  metric on routing-reason distribution per project so calibration drift is visible.
- **Earliest-phase ambiguity.** When findings span phases (e.g. tests/ and src/) we route to the
  earliest. The reviewer of `src/` issues won't get its fix applied until the test re-review
  passes. Defensible — fixing tests first is the correct order anyway — but adds latency.
- **Backwards compatibility for user workflows.** Workflows that omit `writes:` fall back to the
  global "route to dev" behaviour. Operators must opt in by adding `writes:` declarations. Default
  workflow ships with them; user workflows get a doc update + a `jig workflow lint` warning
  flagging missing `writes:`.
- **`reviewer-test-adequacy` running pre-dev sees no implementation.** Its current prompt assumes
  full diff visibility. Verify it produces useful output against only the test phase's diff (which
  it should — its job is test quality, not implementation review).
- **The `review-tests` phase becomes a new dead-end candidate.** If `reviewer-test-adequacy`
  blocks the same way the end-of-ticket federation did today (loops back to `test`, never
  converges, eventually FAILS), we've moved the cul-de-sac, not fixed it. The
  `phase-failure-escalation` work handles this; this design assumes that lands or that
  `max_fix_cycles` for the new phase is tuned (perhaps lower) to surface failure quickly.

## Out of scope

- The cascade-failure problem (`_on_ticket_failed` propagation). Composed-with, not solved-here.
- The phase-failure-escalation problem (operator NEEDS_INFO escalation on give-up). Composed-with.
- Reviewer-finding discoverability via `jig story`. Separate feature.
- Migrating reviewer system prompts beyond the minimum needed to introduce `target_role`. Prompt
  calibration is a follow-up if practice shows it's needed.
- New reviewer types. Federation membership unchanged.
- A `jig workflow lint` subcommand that flags missing `writes:` declarations — mentioned as a
  mitigation but not part of this design's required deliverables.
- `_handle_schedule`'s gate on `dep.status != RESOLVED` — that's the cascade-failure problem's
  concern.

## Open questions

- [x] What if a workflow has multiple phases with the same `role` (e.g. two `dev` phases for a
      multi-stage implementation)? **Resolved: most-recent wins.** Keeps the routing simple and
      matches the natural "fix the latest broken thing first" intuition. If a real workflow surfaces
      a case where the *first* or *all-of* matching phases is correct, revisit then.
- [x] Should `target_role` accept `operator` (or similar) to mean "no automated fix — escalate
      immediately"? **Resolved: no, for now.** Keeping the routing target-set restricted to roles
      that participate in the workflow lets us observe how far agents get figuring out cross-cutting
      findings themselves. Revisit once data shows a class of findings repeatedly hitting
      `max_fix_cycles` despite valid routing — at that point `target_role: operator` (composing with
      the `phase-failure-escalation` work) is the natural next lever.
- [x] How are `writes:` globs interpreted relative to the worktree vs. the project root? **Resolved:
      same path.** A git worktree is a checkout of the same tree — `src/foo.py` is `src/foo.py`
      regardless of which worktree you're in. `ReviewerComment.file` is documented as "Repo-relative
      path of the offending file" (`jig/reviewers/comment.py`), which equals worktree-relative-from-
      its-own-root. No normalization needed; `writes:` globs match the stored string directly.
- [x] Does the `reviewer-test-adequacy` prompt need any change to function pre-dev? **Resolved:
      yes, moderate rewrite needed.** Today's prompt is impl-vs-tests centric (cross-references
      callables in the diff with tests that invoke them). At `review-tests` the impl doesn't exist
      yet. The rewrite reframes the reviewer around the **ticket AC** (already in
      `default_context`) rather than the impl diff. Full proposed YAML below.
- [x] Diff scope for the end-of-ticket `review` phase. **Resolved: today's behavior is cumulative
      diff against `main`**, determined by each reviewer's own `git diff` invocation rather than
      orchestrator-injected scope. The design previously rationalised excluding test-adequacy at
      end-of-ticket as "test files aren't in the diff" — that's wrong; they *are*. The correct
      reason for the exclusion: tests are byte-identical to what was reviewed at `review-tests`
      (TDD lock guarantees dev can't modify them), so re-running test-adequacy is duplicated work.
      Routing handles findings on `tests/**` from other reviewers via the glob mechanism, so the
      diff scope doesn't need to change for this design's correctness. Whether to enforce diff
      scope explicitly (inject a base_ref into the reviewer's prompt context, e.g.
      "diff against `<prev-phase-sha>`") is a follow-up consideration — out of scope here.

## Change log

- 2026-05-17: Initial draft (brent)
- 2026-05-17: Drop cadence-on-reviewer concept; phase declares its `reviewers:` list as the single
  source of truth. Clean up redundant `writes: []` boilerplate from review/validate phases. Enumerate
  the end-of-ticket reviewers in the example YAML so `reviewer-test-adequacy`'s exclusion is
  explicit. Add diff-scope open question.
- 2026-05-17: Resolve shared-fixture tie-break via commit trailers. Add `prepare-commit-msg` hook +
  `.jig/worktree.context` file mechanism for per-commit phase+agent provenance. Rewrite
  `_route_one` to use `_last_touching_phase` for multi-glob-match disambiguation.
- 2026-05-17: Resolve `target_role: operator` open question (no — `phase-failure-escalation` covers
  that lever instead) and `writes:` glob path interpretation (same path in worktree as in repo;
  `ReviewerComment.file` is already repo-relative). Embed the rewritten
  `reviewer-test-adequacy` role YAML so the plan inherits it ready to drop in.
- 2026-05-17: Resolve last two open questions: multi-phase-same-role uses most-recent; diff scope
  is cumulative today but routing handles the consequences via globs. Correct the rationale for
  excluding test-adequacy at end-of-ticket (TDD-lock invariance, not diff-scope exclusion).
