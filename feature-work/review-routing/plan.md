---
title: Review Routing — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-17
updated: 2026-05-17
design: ./design.md
---

# Review Routing — Implementation Plan

## Overview

Eight ordered steps, each shippable on its own. The order is dependency-driven: data-model and
config schema changes ship first (no behavior change), then mechanism (worktree provenance,
reviewer dispatch refactor), then the routing change that ties everything together, then the
caller switch that activates it. Each step has its own tests; the integration test that proves the
original `240db21f` failure now converges lives in step 8 (caller migration).

Per TDD discipline, every step starts with a failing test and ends with the test passing. Steps 1–4
can ship in any order relative to each other (they're independent); the dependency edges are
captured per-step.

## Preconditions

- [ ] Problem statement approved (`feature-work/review-routing/problem.md` merged).
- [ ] Design doc approved (`feature-work/review-routing/design.md` merged).
- [ ] All five design open questions resolved in the design doc (status as of `034d899`).
- [ ] Working tree clean; develop branch up to date.
- [ ] Sibling problem docs landed or accepted — `phase-failure-escalation`, `cascade-failure`, and
      `reviewer-finding-discoverability` are *not* prerequisites for this work, but the assumptions
      this design makes about them (operator escalation handling failure cases; cascade closing the
      graph; discoverability surfacing reviewer prose) need to remain valid.

## Steps

### 1. Add `ReviewerComment.target_role` field

**What:** Add `target_role: str | None = None` to `ReviewerComment`
(`jig/reviewers/comment.py`). Note the model uses `extra="forbid"`, so backward-compat is asymmetric:
newer daemons reading older records (without the field) work fine — the field defaults to `None`.
Older daemons reading newer records that *populate* `target_role` will raise
"Extra inputs are not permitted". Acceptable for this single-deployment environment; document it
explicitly so future mixed-version scenarios don't surprise.

**Why:** Unblocks step 7's routing logic, which checks `target_role` before file-glob match. Shipping
this first lets reviewers start populating the field (if their prompts encourage it) before the
routing change uses it.

**Verify:**
- Unit test: serialize + deserialize a comment with `target_role` set; round-trip equality.
- Unit test: deserialize a comment payload without `target_role`; field is `None`.
- Unit test: existing review-comments JSONL records load without errors.
- Manual: load an existing project's `.jig/store/review_comments.jsonl` and verify no warnings.

**References:** N/A (no REQ ids yet).

### 2. Add `writes:` and `reviewers:` to workflow phase schema

**What:** Update the workflow YAML model (likely in `jig/workflow.py` or wherever Pydantic-models
the phase). Phase entries gain:
- `writes: list[str] = Field(default_factory=list)` — repo-relative globs.
- `reviewers: list[str] = Field(default_factory=list)` — required when `role == "review"`,
  ignored otherwise. Validate that all listed names resolve to existing reviewer role YAMLs at load
  time; fail loud if any are missing.

**Why:** Schema foundation for both default-workflow updates (step 6) and routing (step 7). No
behavior change yet — readers that don't consume these fields keep working.

**Verify:**
- Unit test: parse a workflow YAML with `writes:` and `reviewers:`; fields are populated.
- Unit test: parse a workflow YAML without either field; defaults are empty lists.
- Unit test: parse a workflow with `role: review` and an unknown reviewer name in `reviewers:`;
  raises a clear error naming the missing reviewer.
- Unit test: parse `jig/defaults/workflows/default.yaml` (still unmodified at this point) — must
  still load cleanly.

**References:** N/A.

### 3. Rewrite `reviewer-test-adequacy` role YAML

**What:** Replace `jig/defaults/roles/reviewer_test_adequacy.yaml` with the rewritten version
embedded in `design.md` §"`reviewer-test-adequacy` prompt rewrite". Key differences from the
current file: phase-aware framing (pre-impl), AC-anchored mechanics, `file`/`line` guidance points
at the test file, drop `graph_consumers_of` from `allowed_tools`.

**Why:** Standalone change with no dependencies on schema or code. Can ship before, during, or
after steps 1-2. Shipping early lets us validate the rewrite against historical eval runs (would
this prompt have produced the same findings against pre-recorded test diffs?).

**Verify:**
- Unit test: role config loads without error.
- Unit test: `phase_prompt` mentions "review-tests" phase and "AC" (light sanity check — the
  rewrite framing didn't get lost).
- Manual: spawn the reviewer against a known test diff (e.g. `tests/test_filter_flags.py` from
  the hn-cli eval) with `ticket://description` context; confirm findings are sensible.

**References:** N/A.

### 4. Worktree provenance: context file + `prepare-commit-msg` hook

**What:**
- `jig/worktree.py`: add `install_commit_msg_hook(worktree_path)` that writes an executable
  `prepare-commit-msg` script into `.git/hooks/`. The script reads `.jig/worktree.context`
  (key=value lines) from the worktree root and appends `Phase: <phase>` and `Agent: <agent>`
  trailers if not already present.
- `jig/orchestrator.py`: at phase start (around the existing `_run_agent_with_analytics` call
  site), write `.jig/worktree.context` with current `phase=<name>` and `agent=<role>` lines.
  Overwrite per phase.
- Call `install_commit_msg_hook` from worktree creation (`_run_agent` setup path).

**Why:** The hook + context file together produce the `Phase:` / `Agent:` trailer that the routing
tie-break (step 7) reads. Shipping this before routing lets the trailers accumulate on commits
landing during the changeover, so when routing turns on the data is already there.

**Verify:**
- Unit test: `install_commit_msg_hook` writes an executable file with the expected contents.
- Unit test: hook script appends both trailers when both `phase=` and `agent=` are present in the
  context file.
- Unit test: hook script appends only the present trailer when the context file is partial
  (only `phase=` or only `agent=`). Missing field → that trailer is skipped, no empty `Phase:` /
  `Agent:` line written.
- Unit test: hook script is idempotent — running it on a commit message that already has the
  trailers does not duplicate them.
- Unit test: hook script handles missing context file (no-op, exit 0).
- Integration test: create a worktree via the production path, write a context file, run a
  `git commit -m "..."` in it, assert the resulting commit has the trailers.
- Manual: confirm `git log --pretty=format:%(trailers:key=Phase) -- <file>` returns the expected
  phase for a test commit.

**References:** N/A.

### 5. Reviewer dispatch reads phase's `reviewers:` list

**What:** `jig/reviewers/dispatch.py`'s `dispatch_with_llm_spawn` (or its callers) gains a
parameter that takes the current phase's `reviewers:` list. Dispatch now invokes exactly the
reviewers in that list — no implicit "all reviewers" fallback when the list is empty for a review
phase (validation in step 2 makes empty-list-on-review illegal). Drop the `cadence` argument from
the call signature; the phase identity is the dispatch context now.

**Why:** Activates the per-phase reviewer scoping. Depends on step 2's schema field existing.
Step 6's default-workflow update populates the lists this code now consumes.

**Verify:**
- Unit test: dispatch called with `reviewers=["reviewer-test-adequacy"]` invokes exactly that
  one reviewer.
- Unit test: dispatch called with `reviewers=[]` on a non-review phase is a no-op.
- Unit test: dispatch on a non-existent reviewer name raises an error (defense-in-depth — step 2
  validates at workflow-load, this catches dispatch-time mismatches if a workflow was modified
  in-place).
- Regression test: existing federation tests that asserted "all reviewers ran" now assert against
  an explicit list, not implicit-all.

**References:** N/A.

### 6. Update default workflow YAML with new phase + populated `writes:` / `reviewers:`

**What:** Edit `jig/defaults/workflows/default.yaml` to:
- Insert a `review-tests` phase between `test` and `implement` with
  `reviewers: ["reviewer-test-adequacy"]`.
- Add `writes:` declarations on `spec`, `test`, `implement`, `document` per the design's example
  YAML (`writes: ["docs/spec/**", "docs/decisions/**"]` for spec; `["tests/**"]` for test; etc).
- Add `reviewers:` list on the existing end-of-ticket `review` phase enumerating the remaining
  five reviewers (pattern-conformance, architectural, error-handling, performance, security).
- Audit `jig/defaults/workflows/project.yaml`, `docs.yaml`, `refactor.yaml`, `migration.yaml`,
  `canonicalize.yaml` — add `writes:` where applicable; add `reviewers:` to any review phases.
  Workflows without a test phase don't need `review-tests`.
- **User-authored workflows are the operator's responsibility.** Custom workflows under
  `.jig/workflows/` need their own `writes:` and `reviewers:` additions; this plan does not modify
  them. Without those additions, routing on those workflows falls through to the unowned-finding
  fallback (most-recent dev) — degraded but not broken. A future `jig workflow lint` subcommand
  (out of scope here; mentioned in design §Risks) would surface workflows missing these
  declarations.

**Why:** Activates the actual behavior change for users on default workflows. Depends on step 2's
schema and step 5's dispatch.

**Verify:**
- Unit test: each updated workflow YAML loads.
- Unit test: default workflow's phase sequence is
  `spec → test → review-tests → implement → review → validate → document`.
- Integration test: a fresh project init using the default workflow shows the new phase in the
  TUI / `jig story`.
- Smoke test: run a small ticket through the default workflow end-to-end with the new phase
  enabled; observe `review-tests` invokes `reviewer-test-adequacy` only.

**References:** N/A.

### 7. Implement `_route_blocking_comments`, `_route_one`, `_last_touching_phase`

**What:** New code in `jig/orchestrator.py` (or a sibling routing module if it bloats the file):
- `_last_touching_phase(worktree_path, file) -> str | None` — runs
  `git log --pretty=format:%(trailers:key=Phase,valueonly) -- <file>` and returns the first
  non-empty line.
- `_route_one(workflow, blocked_phase_idx, comment, worktree_path) -> (int | None, str)` — the
  per-comment router described in `design.md` §"Fix-loop routing" (target_role → glob → trailer
  tie-break for multi-match → unowned-finding fallback to most-recent dev).
- `_route_blocking_comments(workflow, blocked_phase_idx, comments, worktree_path)` — top-level
  router that picks the earliest target across blocking comments.

`_find_fix_phase` stays in the codebase for now (not deleted) — step 8 swaps the call site.

**Why:** The routing logic itself. Depends on steps 1, 2, 4 (target_role field, writes globs,
trailer mechanism). Doesn't change behavior on its own — nothing calls it yet.

**Verify:**
- Unit test: `_route_one` with `target_role` set to a valid role → returns that role's most-recent
  phase.
- Unit test: `_route_one` with `target_role` set to an unknown role → falls through to glob
  routing, logs a warning.
- Unit test: `_route_one` with single glob match → returns that phase.
- Unit test: `_route_one` with multi-glob match + parseable trailer → returns the trailer-matched
  phase among candidates.
- Unit test: `_route_one` with multi-glob match + no parseable trailer → returns workflow-earliest
  candidate with `block_reason="ambiguous-ownership"`.
- Unit test: `_route_one` with no glob match → returns most-recent dev phase with
  `block_reason="unowned-finding"`.
- Unit test: `_route_blocking_comments` with comments routing to multiple phases → returns the
  earliest.
- Unit test: `_route_blocking_comments` with no routable comments → returns `None`.

**References:** N/A.

### 8. Switch the caller from `_find_fix_phase` to `_route_blocking_comments`

**What:** In `jig/orchestrator.py` around line 1550 (the existing `_find_fix_phase` call), gather
the cycle's blocking comments from `ReviewCommentsStore` and call `_route_blocking_comments`
instead. Post a structured thread Note naming the chosen phase and route reason (per design
§"Fix-loop routing"). Delete `_find_fix_phase` after the switch is verified.

**Why:** Activates the routing change end-to-end. Depends on step 7.

**Verify:**
- Unit test: caller passes the right arguments through; routing target ends up in the phase loop's
  `phase_idx` re-assignment.
- Unit test: thread Note is posted naming the route reason.
- Integration test (the key one): replay the `240db21f` scenario — a ticket where the reviewer's
  findings target test files. Assert the routing sends it back to the `test` phase (not `implement`)
  and the ticket converges within a small number of cycles rather than failing after 4.
- Manual: run a fresh small ticket through the default workflow; observe routing decisions in
  daemon logs.

**References:** N/A.

## Rollback

The work is layered so each step is independently revertable:

- Steps 1, 2: schema additions. Revert restores prior behavior; no data migration needed because
  new fields are optional and unused fields are tolerated.
- Step 5: dispatch code refactor. Revert is a code rollback (restore the prior dispatch signature
  + caller). No data migration involved.
- Step 3: role-config rewrite. Revert restores the prior YAML; no other code paths depend on the
  rewritten language.
- Step 4: worktree hook + context file. Revert removes the hook (already installed hooks become
  inert no-ops on subsequent worktree operations because the orchestrator no longer writes the
  context file). Existing commits retain their trailers — harmless data.
- Step 6: default workflow YAML. Revert restores the prior phase sequence. In-flight tickets that
  passed through the new phase need manual inspection (was the test-review's commit applied? if
  so, the prior workflow won't know how to schedule against it). Mitigated by not rolling back
  while tickets are in flight against the new phase.
- Steps 7, 8: routing change. Step 8 is the activation; reverting step 8 (restore the
  `_find_fix_phase` call) restores prior routing exactly. `_route_blocking_comments` becomes
  unused code, safe to leave or remove.

If a rollback is needed mid-feature, prefer reverting steps in reverse order (8 → 7 → 6 …)
because dependencies flow downward.

## Out of scope for this plan

- The `phase-failure-escalation` feature — separate `feature-work/phase-failure-escalation/`. The
  design of review-routing assumes a future escalation mechanism handles the residual
  fix-cycle-exhausted cases; this plan does not implement that.
- The `cascade-failure` feature — separate `feature-work/cascade-failure/`. Tickets that still
  fail despite the routing fix will still strand their dependents until that work lands.
- The `reviewer-finding-discoverability` feature — separate plan. `jig story` continues to omit
  review-comments after this plan ships.
- Updating reviewer system prompts beyond `reviewer-test-adequacy`. Pattern-conformance and the
  others may benefit from prompt revisions that emit `target_role` for cross-cutting findings —
  follow-up work driven by observed behavior, not part of this plan.
- A `jig workflow lint` subcommand to flag missing `writes:` declarations on user-authored
  workflows. Mentioned as a mitigation in the design; deferred unless practice shows it's needed.
- Enforcing diff scope at the dispatch boundary (injecting `base_ref` into the reviewer's prompt
  context). The design notes this as a possible follow-up; this plan accepts today's "reviewer
  chooses" diff scope.

## Change log

- 2026-05-17: Initial draft (brent)
- 2026-05-17: Drop step 9 (backfill hook on existing worktrees) — no legacy projects to migrate
  in this environment. Also remove the corresponding risk note from design.md.
- 2026-05-17: Address review #3 minor items. Step 4 verify list gains a partial-context-file test
  case. Step 6 adds an explicit note that user-authored workflows are the operator's
  responsibility (degraded-but-not-broken without `writes:`). Step 5's rollback wording split
  from steps 1/2 since the "fields are optional" rationale doesn't apply to a dispatch refactor.
