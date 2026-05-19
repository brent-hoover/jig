---
title: Fix-loop context — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-05-19
updated: 2026-05-19
design: ./design.md
---

# Fix-loop context — Implementation Plan

## Overview

Eight ordered PRs, each independently mergeable and testable. Foundation
first (store + ID resolver), then the MCP tool surface, then prompt
plumbing for dev side, then reviewer side, then the auto-reraised
write that closes the audit loop, then the read surfaces (`jig story`
extension + TUI), then an end-to-end test that exercises the whole
thing. Each step ships behind no new flag — the feature activates
incrementally as each piece lands, but no piece changes externally
observable behavior on its own until the dev-side prompt section is
wired up (step 3).

## Preconditions

- [x] `feature-work/fix-loop-context/problem.md` approved.
- [x] `feature-work/fix-loop-context/design.md` approved.
- [x] develop is current (review-routing PRs #48–#51 merged).
- [ ] All steps follow TDD: failing test, then implementation, then
      green.
- [ ] Each step is a separate PR opened against develop with
      conventional-commit title.

## Steps

### 1. Foundations — schema, ordered read, store, ID resolver

**What:**
- Add `ReviewCommentsStore.for_ticket_chronological(ticket_id)` that
  walks the in-memory `_docs` dict (insertion-ordered by Python dict
  semantics) and filters by `ticket_id`. The legacy `for_ticket` goes
  through a set-backed index and is non-deterministic across runs —
  the new method gives stable append order without depending on a
  timestamp tiebreaker.
- Add `created_at: datetime` field to `ReviewerComment`
  (`jig/reviewers/comment.py`) with a **static default**
  (`default=datetime.min`), not a `default_factory`. A factory would
  stamp the current time on legacy rows at load time — we want the
  sentinel value to survive loads. Update
  `reviewer_mcp.handle_reviewer_post_comment` to inject
  `created_at = datetime.now(UTC).isoformat()` into the payload
  before `ReviewerComment.model_validate`, so new writes carry the
  real timestamp. Reviewer agents don't supply the field
  themselves. Used for display/story timestamps only; stable-ID
  ordering uses insertion order.
- New module `jig/store/finding_acks.py` with `FindingAck` pydantic
  model and `FindingAcksStore` (mirrors `ReviewCommentsStore` shape:
  `Collection`-backed, indexed on `ticket_id` and `finding_id`).
- New helper module `jig/finding_ids.py` exposing
  `compute_finding_ids(comments: list[ReviewerComment]) -> dict[signature, str]`
  — pure function over chronologically-ordered comments, returns the
  `signature → "RC-N"` mapping. Signature key is `(reviewer, type,
  file, line)`. Also exposes a companion
  `find_by_id(comments, finding_id) -> ReviewerComment | None`.
- No orchestrator wiring yet. Store stands alone with no consumers.

**Why:** Establishes the data substrate everything else depends on.
Pure functions and append-only store are the simplest pieces to
ship and test in isolation.

**Verify:**
- `tests/test_review_comments_chronological.py`: confirms
  `for_ticket_chronological` returns rows in insertion order
  regardless of `created_at` values (e.g., a later-inserted row with
  an earlier timestamp still appears later in the result). Confirms
  determinism across multiple loads by checking same order after a
  `load() / for_ticket_chronological()` round-trip on a seeded JSONL.
- `tests/test_reviewer_comment_created_at.py`: confirms MCP write
  path stamps `created_at` for new rows and that legacy rows without
  the field load with `datetime.min` (sentinel) — specifically test
  that loading the same row twice does NOT shift its `created_at`
  forward (catching the `default_factory` regression this work
  exists to avoid).
- `tests/test_store_finding_acks.py`: round-trip, indexed query,
  append-only semantics.
- `tests/test_finding_ids.py`: signature grouping (re-phrased comments
  collapse), insertion-order stability (adding a later-cycle comment
  doesn't renumber prior IDs), edge cases (missing `file`/`line`
  degrades to category-level signature).

### 2. Two MCP tool handlers

**What:**
- New module `jig/finding_ack_mcp.py` with
  `handle_mark_finding_addressed` and `handle_mark_finding_resolved`.
  Both validate `finding_id` against current `ReviewCommentsStore`
  contents (via `find_by_id`), short-circuit on idempotent same-cycle
  same-author re-calls, and write a `FindingAck` row.
- Wire both into the MCP server tool registry. Permissions: both tools
  must be opt-in per role via `allowed_tools`. (Role config wiring is
  step 4; this step just makes the tools exist.)

**Why:** Tools must exist before any agent can call them, but no
agent is yet enabled to call them so no behavior changes.

**Verify:**
- `tests/test_finding_ack_mcp.py`: writes record, rejects unknown ID,
  idempotency, short prose validation (max 500 chars), `author`
  attribution derived from agent context.

### 3. SpawnReason.FIX_LOOP_RETRY + dev-side prompt section + orchestrator wiring

**What:**
- Add `SpawnReason.FIX_LOOP_RETRY` to `jig/spawn_reason.py` (or
  wherever the enum lives).
- New helper `_blocking_findings_section(bundle)` in
  `jig/prompt_builder.py`. Renders the "## Blocking Findings" section
  with per-finding RC-N, file:line, severity, prose, prior ack
  history. Hard cap at 30 findings; overflow renders a single
  "N more — see `.jig/store/review_comments.jsonl`" line.
- `build_initial_prompt` gains a `fix_loop_bundle` parameter. When
  present, renders the new section between the ticket section and
  the instructions section. `_instructions_section` also gets a new
  `FIX_LOOP_RETRY` branch instructing the agent to call
  `mark_finding_addressed` per finding before commit + update_ticket.
- `Orchestrator._route_blocked_phase` builds the fix_loop_bundle:
  compute RC-N IDs, filter findings whose routing target matches the
  chosen phase (reuse the writes-glob logic already in `reviewer_routing.py`),
  attach prior acks per finding ID. Pass through `agent.py`'s spawn
  context.

**Why:** This is the load-bearing step. After it lands, the dev sees
findings in its prompt for the first time. Step 4 mirrors this for
the reviewer side.

**Verify:**
- `tests/test_prompt_builder_findings.py`: section renders correctly
  with N findings, cap at 30, prior acks rendered inline.
- `tests/test_orchestrator_fix_loop_bundle.py`: simulated block →
  routed-back spawn carries the right findings (target-matched,
  latest cycle), with prior acks attached.

### 4. Reviewer-side prompt section + verify_bundle wiring + role config updates

**What:**
- New helper `_verify_findings_section(bundle)` in
  `jig/prompt_builder.py`. Renders the "## Previous Cycle Findings"
  section with the two-task structure: "verify" first (per-finding
  RC-N + original prose + dev's addressed claim), "find new issues"
  second.
- `Orchestrator._run_review_phase_federation` computes the
  verify_bundle on cycle 2+ (i.e., whenever `FindingAcksStore` has
  any acks for this ticket) and passes it to each reviewer's spawn
  context. The bundle includes all findings for the ticket + all
  acks, not just the latest cycle, because the reviewer needs the
  full history to verify.
- Role config updates: add `mark_finding_addressed` to `allowed_tools`
  for `dev`, `test`, `document`, `validate`. Add `mark_finding_resolved`
  to `allowed_tools` for all six judgment reviewers:
  `reviewer-pattern-conformance`, `reviewer-error-handling`,
  `reviewer-test-adequacy`, `reviewer-architectural`,
  `reviewer-performance`, `reviewer-security`.

**Why:** Closes the loop. Reviewers can now confirm or reject the
dev's claims, producing the two-sided audit trail.

**Verify:**
- `tests/test_prompt_builder_verify.py`: section renders correctly,
  two-task structure intact, ack history accurate.
- `tests/test_orchestrator_verify_bundle.py`: cycle-2+ reviewer
  spawn receives the bundle; cycle-1 spawn does not.

### 5. Auto-reraised ack

**What:**
- After `_run_review_phase_federation` completes, the orchestrator
  scans the cycle's new comments. For each comment whose signature
  matches a prior cycle's signature AND that prior signature has at
  least one `addressed` ack without an intervening `resolved` ack,
  write a `FindingAck(kind="reraised", prose=<new comment's prose>,
  author=<reviewer role>)`.
- Pure-function helper `compute_reraised_acks(prior_acks,
  prior_comments, new_comments)` so this is unit-testable without
  spinning up an orchestrator.

**Why:** Completes the audit trail. The operator inspecting the JSONL
sees "raised → addressed → reraised" without needing to interpret
silence vs. intent.

**Verify:**
- `tests/test_finding_reraised.py`: pure-function tests with
  scenarios (new signature → no reraise, re-flagged signature with
  prior addressed → reraise written, re-flagged signature with prior
  resolved → no reraise).
- `tests/test_orchestrator_reraised_integration.py`: end-to-end
  through a simulated two-cycle ticket.

### 6. Extend `jig story` to include findings

**What:**
- `jig/story.py`: add `StorySource.finding`. New helper
  `_iter_finding_events_for_ticket(project_path, ticket_id)` walks
  `ReviewCommentsStore` (emits `finding_raised` events) and
  `FindingAcksStore` (emits `finding_{addressed|resolved|reraised}`
  events), one `StoryEvent` per row, timestamp from the source row.
- `build_story` interleaves these events with the existing
  thread + log events, sorted by timestamp.
- `cli.py story` command picks up the new events automatically (no
  new flags).

**Why:** Operators can autopsy a failed ticket with a single command.
Eval harnesses get a chronological view alongside the JSONL.

**Verify:**
- `tests/test_story_findings.py`: synthetic ticket with mixed
  thread + log + finding events, all sorted, all rendered.
- Manual: `jig story <ticket-id>` on a real ticket shows the events.

### 7. TUI Findings section in tickets detail pane

**What:**
- `jig/daemon_protocol.py` (or wherever snapshots live): add a
  `findings_snapshot` message type. Daemon emits it on startup and
  on every `FindingAcksStore` write. Payload: list of
  `{ticket_id, finding_id, file, line, severity, reviewer, status,
   acks: [...]}` records — the join of `ReviewCommentsStore` and
  `FindingAcksStore` so the TUI doesn't compute it.
- `jig/tui/screens/tickets.py`: render a "Findings" section in
  `_render_detail` when the ticket has any findings. Color-coded by
  severity, status shown inline.
- New finding-detail modal/screen (similar to `event_detail_modal.py`)
  that shows full prose + ack history when a finding row is selected.

**Why:** Operator sees the audit trail in real time, not just
post-mortem. Closes the operator-visibility gap from the problem
statement.

**Verify:**
- `tests/test_tui_findings_panel.py`: rendering with synthetic data.
- Manual: run the hn-cli eval, watch findings appear as reviewers
  post them.

### 8. End-to-end integration test

**What:**
- New test file `tests/test_fix_loop_context_integration.py`. Builds
  a fake project with a two-phase workflow (implement + review),
  primes `ReviewCommentsStore` with a blocking finding, mocks
  `dispatch_with_llm_spawn` to return scripted comments per cycle
  (cycle 1: blocking finding; cycle 2: same finding still present,
  scripted reviewer doesn't resolve it; cycle 3: dev fixes; reviewer
  confirms). Drives the orchestrator through a full fix-loop cycle.
  Asserts on:
  - `mark_finding_addressed` called by dev with the right ID + prose.
  - `mark_finding_resolved` called by reviewer in cycle 3.
  - `FindingAcksStore` rows in the expected sequence.
  - `jig story <tid>` output shows the events interleaved.

**Why:** Confirms the whole feature works end-to-end. Without this,
the per-step tests can be individually green but the system can be
silently broken at the seams.

**Verify:**
- The integration test passes.
- Manual: re-run the hn-cli eval ticket that failed previously
  (`be2459b8` or equivalent), confirm it converges to RESOLVED.

## Rollback

The feature is additive. To roll back at any point:

- New stores (`FindingAcksStore`) — leaving the JSONL file in place
  is harmless; nothing else reads it.
- New MCP tools — removing from `allowed_tools` in role configs
  disables them; agents that try to call them get the standard
  unknown-tool error.
- Prompt sections — `fix_loop_bundle is None` / `verify_bundle is
  None` short-circuits rendering. Pre-existing PHASE_PRIMARY behavior
  is unchanged.
- The auto-`reraised` write is the only thing that writes without an
  explicit agent action; reverting that step is a one-commit revert
  of the orchestrator block that calls `compute_reraised_acks`.

No destructive operations. No schema migrations. Each step's PR can
be reverted in isolation if the next step exposes a bug.

## Out of scope for this plan

- Termination-policy refinements (diminishing severity bar,
  no-new-findings termination, explicit "good enough" signal).
- Mechanical-reviewer integration.
- Cross-ticket finding IDs.
- CLI top-level `jig findings` subcommand.
- Batch `mark_findings_addressed` tool.
- TUI dedicated findings screen (the detail-pane extension is the
  scoped surface).
- Replay over historical tickets.
- Eval harness changes beyond the integration test.

## Change log

- 2026-05-19: Initial draft (brent)
- 2026-05-19: Roll job-9 review fixes into step 1 (add `created_at` +
  `for_ticket_chronological`) and step 4 (expand reviewer list to all
  six) (brent)
- 2026-05-19: Job-10 review fix — `for_ticket_chronological` walks
  `_docs` directly (insertion-ordered) instead of sorting by
  `created_at`, eliminating the set-iteration tie-breaker problem
  for legacy rows. Test expectations updated to match (brent)
- 2026-05-19: Job-11 review fix — `created_at` uses `default=datetime.min`
  (static default) plus write-path stamping in `reviewer_mcp`, not
  `default_factory`. The factory variant stamps legacy-on-load with
  the current time, rewriting history. Test added for the
  load-twice-stable-timestamp invariant (brent)
