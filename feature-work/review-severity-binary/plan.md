---
title: Binary review severity & failed-ticket dead-ends — Implementation Plan
type: plan
status: draft
owner: brent-hoover
created: 2026-06-12
updated: 2026-06-12
design: ./design.md
---

# Binary review severity & failed-ticket dead-ends — Implementation Plan

## Overview

Seven steps, ordered so each lands a complete, independently shippable behavior change and the two production
defects die first. Steps 1–2 remove the eval-killing defects (notable blocking; silent dead-end). Step 3 closes
the eval-signal loop so subsequent steps are measurable. Step 4 (two-pass) is independent churn reduction. Steps
5–6 split persistence counting into observe-only then behavior-flip, so the SA adjudication lands against a
counter whose numbers we've already seen in real runs. Step 7 validates end-to-end against the original hn-cli
scenario. Each step is one PR.

## Preconditions

- `problem.md` and `design.md` approved (done, 2026-06-12).
- Worktree `feat/review-severity-binary` on `.worktrees/review-severity-binary` (exists). Steps land as separate
  PRs; branch per step off `develop`, or stack on this branch if review cadence allows — decide per step.
- No new dependencies.

## Steps

### Step 1 — Binary gate: notables never block; notables become proposed issues; templates tell the truth

**What:**
- Delete the unacked-notable gate in `_run_review_phase_federation` (the in-workflow phase gate,
  `jig/orchestrator.py:1034-1064` — NOT `_run_review_federation`, the separate post-resolve gate at :752); delete
  the notable-only-block routing branch in `_route_blocked_phase` (`jig/orchestrator.py:3320-3341`, the PR #162
  routing — dead once notables never block; the `else` branch simplifies to the check-failure fallback); delete
  `_unacked_notables`, `_unacked_notable_finding_ids` (orchestrator.py), `_notable_is_satisfied` and the
  notable-routing branch in `build_fix_loop_bundle` (`jig/fix_loop_bundle.py:112-150`); delete the
  notable→coordinator-deferred branch in `apply_severity_disposition` (`jig/reviewers/disposition.py:172-180`)
  and its coordinator-arg plumbing in orchestrator.py:846-855. Keep `FindingAcksStore`, `signature_of`,
  `compute_finding_ids`, `compute_reraised_acks`, ack MCP tools.
- Add notable→issue conversion at federation end: for each notable in the cycle, create a `proposed` ticket
  (`labels=["review-notable"]`, `created_by="review-federation"`, link to source ticket, prose+file/line in
  description), deduped by `signature_of` against existing `review-notable` tickets linked to the source ticket.
- Update all seven `jig/defaults/roles/reviewer_*.yaml` with the consequence block from the design; replace the
  generalist's "ticket-passes-with-deferred" text.
- Analytics: `NotableIssueFiled` event.
- Remove/rewrite tests pinning notable blocking: `tests/test_orchestrator_notable_gate.py`, notable cases in
  `tests/test_fix_loop_bundle.py`, `tests/test_reviewers_disposition.py:230-274`.

**Why:** kills the death-spiral class observed in the 2026-06-12 hn-cli run (RC-3/RC-4); gives reviewers the
non-blocking outlet; makes templates truthful in the same change so reviewer behavior and gate semantics never
disagree.

**Verify:** new tests — notable-only cycle passes the gate in *both* federation paths; `_route_blocked_phase` no
longer special-cases notables; notables file deduped proposed issues; a `review-notable` `proposed` ticket is
never picked up by `_start_ready_tickets`/`find_ready` (pin the dispatch gate explicitly — these tickets are
created by the orchestrator, not the front door). `uv run pytest tests/ -q`,
`uv run ruff check jig/ && uv run ruff format --check jig/`. Grep gate: no remaining caller of removed helpers.

### Step 2 — Failure cascade + stuck watchdog + failure counts

**What:**
- `_on_ticket_failed` (`jig/orchestrator.py:2313`): transitive walk of `blocks`; mark each non-terminal,
  not-running dependent `FAILED` with `block_reason="dependency-failed"`, emitting `ticket_failed` each.
- `_handle_schedule`: if any dependency is `FAILED`, cascade-fail the ticket immediately (late-created tickets).
- `_maybe_run_analyzer` payload: add `tickets_failed` count to `project_complete`.
- Watchdog: new `_check_stuck` called per reconcile tick from `_reconcile_external_tickets` (the work site —
  `_run_reconcile_loop` at orchestrator.py:1393-1411 is just the sleep wrapper); `_stuck_since` state lives on the
  orchestrator. Condition (no running, no ready, no needs_info, ≥1 non-terminal) held for `STUCK_GRACE_SECONDS=120`
  → emit `project_stuck` once per stuck ticket-set, WARNING log. Analytics: `TicketCascadeFailed`, `ProjectStuck`
  events.
- Plan-level check from design: audit consumers of `block_reason` — none may classify a ticket by that field
  without consulting status (`rg -n "block_reason" jig/`).

**Why:** a failed ticket currently strands dependents as `open` forever; `project_complete` never fires; runs
idle silently. This is the structural defect that turned one ticket failure into a killed hour-long run.

**Verify:** new tests — fail a mid-chain ticket → transitive dependents `FAILED(dependency-failed)` →
`project_complete` fires with accurate resolved/failed counts → analyzer runs; ticket created after the failure
with a failed dep cascades at schedule time; watchdog emits `project_stuck` on a synthetic cycle-dep dead-end and
does NOT fire while a ticket is `needs_info`. Full suite + ruff (both).

### Step 3 — Eval outcome classification

**What:** `jig/eval/runner.py`: `EvalOutcome` gains `COMPLETED_WITH_FAILURES` and `STUCK`. `_watch_completion`
reads `tickets_failed` from `project_complete` → map 0 → `SUCCESS`, >0 → `COMPLETED_WITH_FAILURES` (manifest
still collected, tracer still run and recorded). `project_stuck` event → `STUCK` (stall detector unchanged as
catch-all). Exit-code mapping in the CLI command updated accordingly.

**Why:** the hn-cli run was recorded as `stall/bus_silence` — an infrastructure label for a product outcome.
Until this lands, eval data can't distinguish "orchestrator hung" from "project failed cleanly", and steps 4–6
can't be judged.

**Verify:** unit tests for the outcome mapping (fake event streams → expected outcome), including: a
`project_stuck` frame terminates the completion watch promptly with `STUCK` — it must not hang until the stall
detector fires (that fall-through is exactly the bug being fixed). Full suite + ruff.

### Step 4 — Review invocation shaping: multi-pass first round, delta-scoped re-review

**What:**
- First round: `PhaseConfig.review_passes: int = 1`; pass loop in `_run_review_phase_federation` (snapshot prior
  comments once before pass 1; `compute_reraised_acks` once after final pass); informed pass-N prompt includes
  cycle findings so far with add-coverage instruction; `jig/defaults/workflows/feature-s.yaml` review phase sets
  `review_passes: 2`.
- Re-review rounds: track last-reviewed commit (worktree HEAD at final-pass completion) in the phase loop next to
  `fix_counts`; on re-entry to the review phase after a blocked round, pass it as `base_ref` to
  `dispatch_with_llm_spawn` (existing plumbing, orchestrator.py:939 → dispatch.py:836 → reviewers'
  `git diff base_ref..HEAD`) instead of the default branch; spawn prompt lists the prior round's blocking
  findings (verify each / review the delta / do not re-review unchanged code); re-review rounds are single-pass
  regardless of `review_passes`. Missing tracked commit (daemon restart) falls back to the full-diff base.

**Why:** front-loads discovery into one comprehensive round and makes later rounds pure convergence — a new
finding on untouched code becomes structurally impossible on re-review (the RC-4 class), while fix-introduced
regressions stay reviewable (they're in the delta). Cuts fix-round churn at the source; also makes step 5's
persistence matching more reliable (re-review findings are predominantly re-raises).

**Verify:** new tests — `review_passes: 2` spawns two sequential reviewer runs in the first cycle, pass-2 prompt
contains pass-1 findings, gate evaluates the union; `review_passes` default 1 leaves first-round behavior byte-
identical (existing federation tests unchanged); re-review cycle receives `base_ref` = last-reviewed commit and a
prompt carrying the prior blocking findings; re-review runs exactly one pass even with `review_passes: 2`;
missing tracked commit falls back to full-diff base. Full suite + ruff.

### Step 5 — Persistence counting (observe-only)

**What:** persistence key `(reviewer, type, file)`; `survival_counts` tracked in the phase loop next to
`fix_counts` with the increment/absence-reset rules from the design. **No behavior change**: the cap still fails
the ticket as today. Emit `ReviewFindingPersisted` analytics events (key, survival count, cycle).

**Why:** separates the counter's mechanics (testable deterministically) from the behavior flip, and produces
real-run survival distributions before the SA threshold goes live — so `SURVIVAL_THRESHOLD=2` is checked against
data, not vibes.

**Verify:** unit tests — same key across consecutive blocked rounds increments; absent-then-reappearing key
resets to zero; fresh keys at zero; counts reset per phase entry. Analytics event emitted. Full suite + ruff.

### Step 6 — SA adjudication (behavior flip)

**What:**
- `FindingAck`: `kind="dismissed"` + optional `persistence_key: str | None`.
- New SA-only MCP tool `sa_adjudicate_finding(finding_id, verdict, rationale, guidance)` (registered only for
  adjudication spawns; finding_id ∈ escalated RC-Ns; verdict enum enforced).
- Escalation triggers: `survival_counts[key] >= SURVIVAL_THRESHOLD(2)` → adjudicate that key; cap trip →
  adjudicate all outstanding blocking keys (replaces direct fail).
- Adjudication spawn: profile `sa_role`, bundle with per-cycle prose history, addressed acks, fix commits; verdict
  handling per design (`dismissed` → binding gate filter by persisted key; `uphold_guidance` → guidance in fix
  bundle, survival reset, fix_counts NOT reset; `uphold_fail` → ticket FAILED). `MAX_SA_ESCALATIONS=2` per phase,
  then direct fail. SA errors fail closed (ticket fails, loud log).
- SA role template gains the adjudication section. Analytics: `SAAdjudication` event.

**Why:** removes the reviewer's unilateral kill power — the problem's core requirement — with bounded worst-case
rounds (≤ max_fix_cycles + MAX_SA_ESCALATIONS).

**Verify:** new tests — persistent key past threshold triggers exactly one adjudication spawn; each verdict path
(dismissed binds across cycles and across a simulated restart via the persisted ack; uphold_guidance grants one
round and resets survival only; uphold_fail fails ticket and cascade from step 2 applies); cap-trip escalates
instead of failing; escalation budget exhausts to direct fail; SA spawn error fails closed. Negative state-
lifetime test: after a simulated restart, `survival_counts` is zero but the dismissal *still binds* — catches an
implementation that wrongly keys the gate filter off in-memory state instead of the persisted ack. Full suite +
ruff.

### Step 7 — End-to-end validation against the original scenario

This step is a manual/observational gate, not a blocking automated one — a live LLM eval run is non-deterministic;
the deterministic coverage lives in steps 1–6. What it validates is the *integration*, against the run that
motivated the feature.

**What:** rerun `jig eval run hn-cli` (small profile). Inspect: review rounds with notables file issues and never
block; any ticket failure produces `project_complete` + analyzer + `COMPLETED_WITH_FAILURES` rather than
stall/bus_silence; analytics stream contains the new events. If the run surfaces defects in steps 1–6, fix
forward within this step's PR or file follow-ups in `deferred.md`.

**Why:** the whole feature exists because of one observed run; the success criteria in problem.md are written
against replaying it.

**Verify:** eval outcome ≠ `stall/bus_silence` for ticket-failure scenarios; `jig eval list hn-cli` shows the run
with the new outcome taxonomy; manual read of `.jig/store/messages.jsonl` + analytics for the new events. Capture
results in the eval run notes.

## Step → design traceability

| Step | Design component |
|------|------------------|
| 1    | §1 Binary gate, §2 Notables→issues, §3 Templates, Analytics (NotableIssueFiled) |
| 2    | §6 Cascade + watchdog, Interfaces (project_complete payload), Analytics (TicketCascadeFailed, ProjectStuck) |
| 3    | §7 Eval outcome classification |
| 4    | §4 Review invocation shaping (multi-pass first round + delta-scoped re-review) |
| 5    | §5 Persistence counting (counting only), Analytics (ReviewFindingPersisted) |
| 6    | §5 SA adjudication + Data model (FindingAck), Interfaces (MCP tool), Analytics (SAAdjudication) |
| 7    | Problem success criteria |

## Change log

- 2026-06-12: Initial draft (brent-hoover)
- 2026-06-12: Plan-review fixes: added _route_blocked_phase notable-branch removal; corrected gate method name to
  _run_review_phase_federation; pinned proposed-issue dispatch gate, project_stuck watch termination, restart
  state-lifetime test; named _check_stuck insertion point; marked step 7 observational (brent-hoover)
- 2026-06-12: Step 4 extended per operator decision 6: delta-scoped single-pass re-review rounds with
  prior-finding verification (brent-hoover)
