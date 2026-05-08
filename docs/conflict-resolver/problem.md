---
title: Merge Conflict Auto-Resolver — Problem Statement
type: problem
status: active
owner: brent
created: 2026-05-07
updated: 2026-05-07
---

# Merge Conflict Auto-Resolver — Problem Statement

## Context

Jig runs parallel ticket branches. After each ticket completes, `merge_ticket` pre-integrates the base branch into the ticket branch before merging. When two tickets touch different lines in the same file, the pre-integration resolves the difference cleanly. When they touch the same lines differently, jig surfaces a `MERGE_CONFLICT` status, preserves the worktree, and stops — waiting for a human to intervene.

## Problem

Overlapping-line conflicts between parallel tickets stall progress and require manual intervention even when the resolution is mechanical. The PM may schedule conflicting tickets in parallel; when it does, every affected ticket needs a human touch before it can reach `RESOLVED`. In practice, jig agents are capable of reading conflict markers and choosing the correct resolution — but nothing currently invokes them to do so.

## Simplest possible solution

On `MergeConflictError`, spawn a focused agent in the conflicted worktree, let it resolve the markers and commit, then retry the merge once. If the retry succeeds, the ticket resolves normally. If not, fall through to the existing human-review path.

## Complications considered

- **Scale**: N/A — the number of concurrent conflicts is bounded by the number of parallel tickets, which is small.
- **Concurrency**: The merge lock already serializes `merge_ticket` calls. The conflict resolver runs after the lock is released (worktree is preserved); no additional locking needed.
- **Failure modes**: The resolver agent may fail to spawn, fail to run, or run without fixing the conflict. All three must fall through to `MERGE_CONFLICT` without crashing or changing current behavior. The retry must not loop — one attempt only.
- **Cross-cutting policies**: N/A — no PII, no secrets, no auth surface.

## Constraints

- Must not regress the existing `MERGE_CONFLICT` / human-review path.
- One retry attempt only — no loop, no config knob (YAGNI).
- The resolver role must ship as a jig default (no project configuration required).

## Requirements

- On `MergeConflictError`, jig attempts to resolve the conflict automatically before routing to `MERGE_CONFLICT`.
- The resolver agent is spawned in the preserved worktree with conflict markers present.
- After the agent completes, `merge_ticket` is retried once.
- If the retry succeeds, the ticket status reaches `RESOLVED`.
- If the retry fails or the agent fails, the ticket routes to `MERGE_CONFLICT` (unchanged from today).
- The resolver must never turn a conflict into a crash.

## Non-goals

- Configurable retry count.
- PM-level file-overlap detection / `depends_on` inference.
- New ticket status or TUI changes.
- Notification / event for "resolver attempted."

## Success criteria

Tickets that previously landed in `MERGE_CONFLICT` due to overlapping parallel edits now reach `RESOLVED` automatically when the conflict is resolvable. The human-review path is unchanged when the agent cannot fix it.

## Open questions

- [ ] None blocking.

## Change log

- 2026-05-07: Initial draft (brent)
