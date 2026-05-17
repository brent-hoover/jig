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
error-handling, performance, security) — test-adequacy is excluded there because it already ran
post-test and the test files aren't in the dev changeset under review.

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


def _route_one(workflow, blocked_phase_idx, comment):
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
        for idx in range(blocked_phase_idx - 1, -1, -1):
            phase = workflow.phases[idx]
            if _matches_any(comment.file, phase.writes):
                return idx, f"writes-glob {phase.name}"

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

## Interfaces

- **Workflow YAML (`jig/defaults/workflows/*.yaml` and `.jig/workflows/*.yaml`):** phase entries
  gain optional `writes: list[str]`. Phases with `role: review` gain a required
  `reviewers: list[str]` field listing which reviewers fire there.
- **Reviewer role YAML (`jig/defaults/roles/reviewer_*.yaml`):** no schema change. Membership is
  now declared on the workflow side, not the reviewer side.
- **`ReviewerComment` model:** new optional `target_role: str | None` field.
- **Orchestrator internals:** `_find_fix_phase` → `_route_blocking_comments`. Same call site, broader
  return shape (phase index + reason string). No public API impact.

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

- [ ] What if a workflow has multiple phases with the same `role` (e.g. two `dev` phases for a
      multi-stage implementation)? `_most_recent_phase_with_role` returns the most recent, which
      seems right; confirm no scenario wants the first or all-of.
- [ ] Should `target_role` accept `operator` (or similar) to mean "no automated fix — escalate
      immediately"? Composes with `phase-failure-escalation` if so; today we'd just route to dev
      and let the loop hit `max_fix_cycles`.
- [ ] How are `writes:` globs interpreted relative to the worktree vs. the project root? Comments'
      `.file` field convention needs to be confirmed — likely worktree-relative paths from the
      reviewer's perspective, which should match the project root for our purposes.
- [ ] Does the `reviewer-test-adequacy` prompt need any change to function pre-dev? Its current
      prompt mentions "the diff" generically; confirm during implementation that the diff scope it
      receives at `review-tests` (test phase's commits only) is sufficient context. (`grep` confirms
      the prompt is already scoped to "tests exercise the new behavior" — no implementation-side
      assumptions, so this should be safe.)
- [ ] Diff scope for the end-of-ticket `review` phase: the dev-only changeset (test files absent),
      or the full ticket diff up to that point (test files present)? The design assumes dev-only —
      that's what makes excluding `reviewer-test-adequacy` from the end-of-ticket phase coherent.
      Verify how `dispatch_with_llm_spawn` actually scopes the diff today and align.

## Change log

- 2026-05-17: Initial draft (brent)
- 2026-05-17: Drop cadence-on-reviewer concept; phase declares its `reviewers:` list as the single
  source of truth. Clean up redundant `writes: []` boilerplate from review/validate phases. Enumerate
  the end-of-ticket reviewers in the example YAML so `reviewer-test-adequacy`'s exclusion is
  explicit. Add diff-scope open question.
