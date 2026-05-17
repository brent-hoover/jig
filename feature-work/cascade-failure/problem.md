---
title: Cascade Failure Propagation — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-17
updated: 2026-05-17
---

# Cascade Failure Propagation — Problem Statement

## Context

Tickets express dependencies via two symmetric fields: `blocked_by: [ids]` (this ticket can't run until
those resolve) and `blocks: [ids]` (resolving this ticket unblocks those). When a ticket resolves, the
orchestrator calls `_unblock_dependents(completed_id, ticket)` (`jig/orchestrator.py:1925`), which walks
the resolved ticket's `blocks` list and re-runs `_handle_schedule` on each dependent.

`_handle_schedule` then re-checks dependency status:

```python
# orchestrator.py:1194
if dep is None or dep.status != TicketStatus.RESOLVED:
    _logger.info("ticket %s blocked by %s (status=%s), deferring", ...)
    return
```

The scheduling gate only releases a dependent when its parent is exactly `RESOLVED`. `FAILED` is not
`RESOLVED`; the dependent stays deferred.

`_on_ticket_failed` (`orchestrator.py:1903`) is the symmetric counterpart to `_on_ticket_completed` but
does **not** walk `blocks`. It emits the `ticket_failed` event, removes the ticket from
`_running_tickets`, and calls `_start_ready_tickets()` (which finds nothing because the dependents are
still blocked) plus `_maybe_run_analyzer()`. The analyzer's terminal check
(`orchestrator.py:1991-1996`) treats `OPEN` as non-terminal, so when descendants of the failed ticket
remain `OPEN` with `blocked_by` populated, the analyzer never fires either.

## Problem

When a ticket fails, its dependents are stranded. Concretely, against `240db21f`'s failure in the
`hn-cli` eval:

```
240db21f  →  failed
21f01a38  →  open,  blocked_by=[240db21f]   (parent failed, gate looks for RESOLVED, stays deferred)
4e4d30f5  →  open,  blocked_by=[21f01a38]   (cascades behind 21f01a38)
```

The orchestrator logs "no ready tickets in queue" and idles indefinitely. There is no signal that
progress is structurally impossible:

- No `needs_info` prompt — descendants are `OPEN`, not awaiting input.
- No stall verdict — the watcher's heartbeat / wall-time / unanswered-needs-info signals only fire on
  *in-flight* agents, not on graph-level deadlock.
- No analyzer summary — terminal-state check sees `OPEN` and treats the project as still working.
- No `ticket_blocked_indefinitely` event — that concept doesn't exist.

The operator sees a quiet daemon and has to discover the cause manually. From outside the system the
state looks identical to "everything is fine, just nothing scheduled right now."

Two distinct problems:

1. **No propagation rule.** Failure is an absorbing state for *this* ticket but is not transmitted to
   the dependency graph. The graph still treats the failed ticket as something to wait on.
2. **No deadlock observation.** Even if propagation were intentional ("we want to keep dependents alive
   in case the parent gets retried"), there's no surface that surfaces "all reachable tickets are
   structurally blocked." The system silently waits.

## Simplest possible solution

In `_on_ticket_failed`, after emitting the existing event, walk `ticket.blocks` and either:

**Option A — cascade-fail** (closes the graph cleanly):
For each dependent `d` in `ticket.blocks` that is currently `OPEN` or `BLOCKED`, mark `d` as `FAILED`
with `block_reason="upstream-failed"`, post a structured note on `d`'s thread naming the failed
ancestor, then recursively walk `d.blocks`. The recursive walk terminates because each visited ticket
transitions to a terminal state.

**Option B — cascade-block** (keeps options open for retry):
For each dependent `d`, transition `d` to `BLOCKED` with `block_reason="upstream-failed"` and post the
same note. `BLOCKED` is still non-terminal so retrying the upstream ticket can revive the chain. This
requires the scheduling gate (`orchestrator.py:1194`) to also release on `BLOCKED → RESOLVED` (or to
clear `block_reason` when the upstream is retried successfully).

Either option fixes the silent-halt: `FAILED` tickets count as terminal and let `_maybe_run_analyzer`
fire; `BLOCKED` tickets at least name their root cause and surface in the TUI's blocked-tickets view.

The two options compose with the separate phase-failure-escalation work: if escalation is in place, a
ticket only reaches `_on_ticket_failed` because the operator explicitly chose "fail." In that case the
operator has already accepted the consequences, and cascade-fail (A) is the simpler default. Without
escalation, cascade-block (B) is more forgiving but leaves more surface area.

## Complications considered

- **Scale**: A diamond / fan-out dependency graph could visit the same descendant multiple times during
  the walk; bound visits per ticket (track visited set) so the walk is `O(V+E)` in the dependency
  subgraph. In practice fan-out is small (single-digit per ticket) and this is a non-issue.
- **Concurrency**: The walk happens inside `_on_ticket_failed`, which is already serialised per ticket.
  Tickets being created concurrently with the walk would be missed; if the dependency graph is mutated
  while a cascade is in progress, treat the walk as a snapshot — new dependents created after the walk
  starts are subject to the normal scheduling gate, which will re-check the failed parent's status and
  defer correctly.
- **Failure modes**:
    - Cycle in the `blocks` graph — the visited-set guard prevents infinite recursion. Log a warning
      because dependency cycles are a separate bug.
    - Dependent already in `RESOLVED` / `CLOSED` / `FAILED` — skip; don't reanimate or re-fail.
    - Dependent has multiple parents and only one failed — the dependent is still validly blocked on
      other parents. Cascade-block (B) suits this; cascade-fail (A) would over-fail.
- **Cross-cutting policies**: Audit / analytics — emit a typed `dependent_failed_by_cascade` event per
  visited dependent so the analyzer + post-run reports can show the blast radius of a single failure.
- **Backwards-compatibility**: existing in-flight projects have `OPEN/blocked_by` rows that were
  stranded by old failures. The cascade walk fires only on new failures; stranded rows remain. A
  one-shot cleanup that walks the store and propagates retroactively is a nice-to-have, not a
  requirement.

## Constraints

- The fix lives entirely in the orchestrator's failure path + scheduling gate; the store schema is
  already sufficient (`blocks`, `blocked_by`, `block_reason` all exist).
- Must not alter the semantics of `_on_ticket_completed` / `_unblock_dependents` — those work correctly
  today.
- Cascade behavior must be observable: the operator must be able to see in the TUI / via `jig story`
  that a dependent was failed/blocked specifically because of an upstream failure, not because of its
  own work.

## Requirements

- A ticket transitioning to `FAILED` causes every descendant in its `blocks` subgraph to transition to
  a terminal-or-blocked state with `block_reason="upstream-failed"` and a structured note naming the
  ancestor.
- The chosen scheme (A vs B) is consistently applied — no half-states where some dependents are
  failed and others are still `OPEN`.
- `_maybe_run_analyzer` correctly detects "all tickets terminal" after the cascade walk, so post-run
  analysis fires when a project comes to rest after a cascade.
- The TUI's blocked / failed views surface `upstream-failed` tickets distinctly so the operator can
  trace the root cause without digging through logs.
- A `dependent_failed_by_cascade` (or equivalent) event is emitted per propagation step for analytics.

## Non-goals

- The cause of the original failure — that's the review-routing problem.
- Operator-mediated decision on whether to cascade — phase-failure-escalation handles operator choice
  *before* a failure becomes terminal. By the time `_on_ticket_failed` fires, the decision has already
  been made (either by the orchestrator or by an operator who chose "fail" at the escalation prompt).
- Retroactive cleanup of historically-stranded `OPEN/blocked_by` tickets in existing projects.
- Auto-retry of failed upstream tickets — escalation already covers retry.
- Detecting *all* graph-level deadlocks (e.g. mutually-dependent tickets that never resolve). This work
  handles the failure-induced case only.

## Success criteria

- After a ticket fails, no descendant remains `OPEN` with `blocked_by` pointing transitively at the
  failed ticket.
- `_maybe_run_analyzer` fires (and emits `project_complete` / `analysis_complete`) once a failure has
  cascaded through its descendants, instead of the daemon idling silently.
- Re-running `240db21f`'s scenario produces a clean terminal state across the project, not the
  observed cul-de-sac.
- The operator can identify cascade-failed tickets vs primary failures from the TUI alone.

## Open questions

- [ ] Cascade-fail (A) vs cascade-block (B) — assuming phase-failure-escalation lands, A is the simpler
      default. Confirm.
- [ ] Multi-parent dependents (`blocked_by: [a, b]`, only `a` failed): under (A), do we fail the
      dependent immediately, or wait to see if `b` also fails? Strictest answer: fail immediately (the
      dependent can no longer complete normally because one ancestor is permanently failed); softest:
      wait. The strict answer matches operator intuition better but bears verifying.
- [ ] Eval / simulator runs need the same "all-terminal" signal to fire so test harnesses don't hang;
      already implicit if (A) is chosen.
- [ ] Where in `_on_ticket_failed` does the cascade run — synchronously before `_start_ready_tickets`,
      or as a background task? Synchronous keeps the state transition atomic; background is safer if
      the walk grows large. For current scale, synchronous.

## Change log

- 2026-05-17: Initial draft (brent)
