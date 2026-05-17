---
title: Phase Failure Escalation — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-17
updated: 2026-05-17
---

# Phase Failure Escalation — Problem Statement

## Context

The orchestrator's phase loop has several terminal paths that mark a ticket FAILED and abandon it
without further interaction. The dominant case is `max_fix_cycles` exhaustion
(`jig/orchestrator.py:1538-1547`): when the same phase returns `blocked` more than the configured cycle
budget (default 4), the loop logs a warning and transitions the ticket to FAILED via `_on_ticket_failed`.
Other paths with the same shape:

- "phase blocked but no fix phase found" (`orchestrator.py:1567-1574`).
- `_auto_commit_worktree` failure (`orchestrator.py:1513-1518`).
- Review-federation crash on both initial + retry (`_fail_with_federation_error`, line 845).
- Generic unrecoverable phase result (line 1573).

The orchestrator already has a well-tested operator-interaction primitive: `TicketStatus.NEEDS_INFO`
plus `_wait_for_resume`. Phase agents use it to ask clarifying questions and pause until the operator
responds. Resume is initiated by an operator reply on the message bus, after which the phase re-runs.

## Problem

When the orchestrator decides it has exhausted its automated options, it silently transitions the ticket
to FAILED. The operator has no signal except the ticket's status and a one-line `_logger.warning` — no
prompt, no context, no actionable next step surfaced to the TUI. Recovery requires the operator to
notice the failure, dig into logs and the review-comments store on their own initiative, then manually
edit ticket state or open a new ticket to retry.

Two distinct problems flow from this:

1. **Silent abandonment.** A FAILED ticket emits a `ticket_failed` event but no `needs_info` prompt.
   Anything subscribed only to the operator-interaction channel (TUI question pane, etc.) never sees the
   ticket. Cul-de-sac scenarios (every downstream ticket blocked on a failed ancestor — covered as a
   separate problem) become indistinguishable from "nothing to do."
2. **No human override path mid-flow.** Even when the orchestrator's decision is correct (4 cycles really
   couldn't converge), the operator often *can* break the impasse — by amending the spec, manually
   editing the worktree, vetoing a single reviewer comment, or simply approving the dev's last attempt as
   "good enough." Today, there is no in-flow mechanism to do any of this; the operator must either let
   the ticket fail and start a new one, or reach into the JSONL stores by hand.

The "give up and fail" outcome is appropriate as the *operator's* decision, not the orchestrator's
default.

## Simplest possible solution

Replace each "give up → FAILED" transition with an operator escalation: post a structured `needs_info`
question to the ticket, transition the ticket to `NEEDS_INFO`, and call `_wait_for_resume`. The reply
schema offers the operator at least three actions:

- **retry** — re-run the same phase with current state (gives the operator a chance to manually edit the
  worktree or amend a spec first, then resume).
- **fail** — explicitly mark the ticket FAILED (preserves today's behavior, but as an explicit
  choice).
- **skip** — accept the phase's last result and advance to the next phase (operator's "good enough"
  override).

The escalation message must carry enough context for the operator to act without spelunking: the phase
name, the cycle count, the structured blocking findings (file, line, prose, suggested_diff) from the
last cycle's `ReviewCommentsStore` query, and a pointer to the worktree path.

This is a localised change. The primitives (`NEEDS_INFO`, `_wait_for_resume`, structured operator
prompts) already exist; only the trigger points and the action vocabulary are new.

## Complications considered

- **Scale**: N/A — escalations fire at most once per ticket-phase failure, not per cycle.
- **Concurrency**: A ticket in `NEEDS_INFO` already serialises operator interaction on a single
  question-id per ticket; nothing new here. Multiple tickets in `NEEDS_INFO` concurrently is the existing
  norm and works.
- **Failure modes**:
    - Operator never responds — ticket sits in `NEEDS_INFO` indefinitely. The `unanswered_needs_info`
      stall signal (`StallDetector`, 30 min default) already covers this; the verdict surfaces to logs
      but doesn't auto-FAIL. Sufficient — the operator chose to walk away.
    - Operator responds with an unparseable action — fail loud (re-prompt). No silent acceptance.
    - Daemon restart while waiting — `_wait_for_resume` is resumable from store state today; verify the
      same holds for these escalations.
- **Cross-cutting policies**: N/A — same telemetry/audit surface as existing `needs_info` flows.
- **Determinism for tests**: The escalation must be opt-out for non-interactive runs (CI, evals,
  scenario tests) so they don't hang waiting for an operator. Existing pattern: a config flag or env var
  short-circuits to `FAILED` directly. Reuse, don't reinvent.

## Constraints

- Must compose with the existing `_wait_for_resume` mechanism — the phase loop is structured around
  resume-after-needs_info already, and the change should slot in without restructuring.
- The reply action vocabulary must be small and explicit. No free-text "what should I do?" — the
  operator picks one of the defined actions.
- Eval / simulator / CI runs must be able to opt out (default behaviour configurable; opt-out turns
  escalations back into FAIL).
- Telemetry already in place — analytics events, structured logging, message bus — should record both
  the escalation and the operator's chosen action.

## Requirements

- Every current "fail the ticket" terminal in the phase loop and federation path becomes "escalate to
  operator", except where an explicit configuration says otherwise (eval/CI).
- The escalation prompt is structured (not free-text): includes phase name, cycle count, blocking
  findings, worktree path, and the available actions.
- The operator's chosen action is logged + emitted on the bus as a typed event for analytics.
- Resume after an escalation re-enters the phase loop in the correct phase (retry → same phase; skip →
  next phase; fail → terminal as today).
- An opt-out path (config flag) preserves today's silent-fail behaviour for non-interactive runs.

## Non-goals

- The cascade-failure / `_on_ticket_failed` dependent-propagation bug — separate problem, separate fix.
- The review-routing change (move test-review to its own phase, route end-of-ticket findings per file)
  — separate problem (`feature-work/review-routing/`). The two compose: review-routing reduces the
  *frequency* of dead-end fix loops; escalation handles the residual cases.
- New operator UI affordances — the TUI's existing `needs_info` rendering should be sufficient as long
  as the prompt body is well-structured. Surface improvements are a follow-up if practice shows they're
  needed.
- Retrying with modified parameters (e.g. "retry with a different reviewer set"). Phase 1 is just
  retry-as-is; richer overrides are a follow-up.

## Success criteria

- A ticket that today fails after 4 review cycles instead lands in `NEEDS_INFO` with a structured prompt
  showing the unresolved findings and the available actions. The operator can choose to retry, skip, or
  fail.
- The opt-out configuration produces today's behaviour exactly (silent FAIL) for eval / CI runs, so
  existing tests and simulator scenarios continue to pass without modification beyond setting the flag.
- The federation-crash and auto-commit-failure paths produce the same kind of structured escalation,
  with phase-appropriate context.
- A passing integration test that drives a ticket to the max_fix_cycles trigger, asserts it transitions
  to `NEEDS_INFO`, and verifies each of the three reply actions produces the correct downstream state.

## Open questions

- [ ] Action vocabulary final list — retry / skip / fail is the minimum. Should there be an
      "edit-and-retry" verb that pauses for the operator to modify the worktree first, with an explicit
      "ready" signal? (`_wait_for_resume` already supports the wait; the question is whether to make the
      verb explicit.)
- [ ] Where does the opt-out live — `.jig/config.yaml` (`orchestrator.escalate_on_failure: bool`),
      env var, or both? Eval framework already reads config; env var helps one-off runs.
- [ ] Should `_fail_with_federation_error` (double-crash) escalate or stay FAIL? An exception that
      defies retry is a different kind of failure than "blocked by reviewers and dev can't converge" —
      defensible to fail-fast here. Worth deciding now to keep escalation scope honest.
- [ ] Do we escalate `_auto_commit_worktree` failures? Those usually indicate environmental problems
      (disk full, git config broken) where retry is unlikely to help without operator action — escalation
      may be exactly right, but the action vocabulary fits worse.

## Change log

- 2026-05-17: Initial draft (brent)
