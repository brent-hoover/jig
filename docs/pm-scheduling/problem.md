---
title: PM Scheduling — Problem Statement
type: problem
status: active
owner: brent
created: 2026-05-08
updated: 2026-05-08
---

# PM Scheduling — Problem Statement

## Context

Jig runs parallel ticket branches. After each ticket completes, `merge_ticket` pre-integrates the base branch into the ticket branch before merging. The conflict resolver (added previously) handles overlapping-edit conflicts by spawning an agent to fix conflict markers and retry the merge. However, merge conflicts are still occurring at a high rate in practice — not because the resolver fails, but because the PM over-parallelizes work from the start.

Two patterns cause excessive conflicts:

1. **Early parallelism on a thin codebase.** When a project starts, the SA applies a scaffold (a template commit) and the PM immediately fans out to many parallel tickets. All those tickets branch from the same thin scaffold and independently modify the same shared files (CLI entry points, config, lock files, shared models). Every parallel pair that touches the same file produces a conflict.

2. **Small projects that don't benefit from parallelism.** For projects with few tickets, the overhead of merge conflicts and conflict resolution exceeds any speedup from parallel execution. Sequential execution is simply faster end-to-end.

The PM's current instructions explicitly say "Maximize parallelism," which is correct for large mature codebases but harmful for new or small projects.

## Problem

The PM has no awareness of project maturity or size. It maximizes parallelism unconditionally, causing:
- Add/add and overlapping-edit conflicts on files that all early tickets touch
- Wasted agent spawns for conflict resolution on conflicts that were avoidable
- Slower total throughput on small projects where sequential would be faster

## Simplest possible solution

Change the PM prompt to tell it not to parallelize when it's creating ≤ 10 tickets, and not to fan out parallel tickets until the first implementation ticket of any group has completed.

## Complications considered

- **Scale**: N/A — the number of parallel tickets is bounded by the number of ready tickets, which is small.
- **Concurrency**: The PM runs before tickets are dispatched; its output (the dependency graph) is already the concurrency plan. Changing the dependency graph at plan time is safe.
- **Failure modes**: The PM might ignore its instructions and still over-parallelize. Mitigated by a hard `max_parallel` cap in the orchestrator as a safety net.
- **Cross-cutting policies**: N/A — no PII, no secrets, no auth surface.

## Constraints

- Must not regress existing behavior on large projects where parallelism is beneficial.
- `max_parallel` must be backward-compatible (unset = no cap = current behavior).
- The replan spawn must never loop; one replan per conflict only.
- Must not require operators to change project config for the safe defaults to apply.

## Requirements

- PM uses a linear dependency chain when the total ticket count it is creating is ≤ 10.
- PM does not fan parallel tickets out until the first bones/setup ticket of any group completes.
- Orchestrator enforces a configurable `max_parallel` cap on concurrent tickets.
- After a successfully resolved merge conflict, orchestrator spawns the PM in a `REPLAN` role to add ordering constraints to pending tickets.
- Replan PM may only adjust `depends_on` on not-yet-started tickets; it does not create or delete tickets.
- All changes fall through gracefully: replan failure leaves scheduling unchanged.

## Non-goals

- PM-level file-overlap detection (predicting which files a ticket will touch from its spec).
- Automatic `max_parallel` computation from ticket count (operator sets it explicitly or leaves it unset).
- Multi-retry replan loop.
- Notification events for "replan attempted."

## Success criteria

- Eval runs on small projects (≤ 10 tickets) produce zero or near-zero merge conflicts caused by over-parallelism.
- Large projects are unaffected (parallelism still fans out after bones gates).
- Operators can cap concurrency via `max_parallel` without code changes.

## Open questions

- [ ] None blocking.

## Change log

- 2026-05-08: Initial draft (brent)
