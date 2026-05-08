---
title: Merge Conflict Auto-Resolver — Design
type: design
status: active
owner: brent
created: 2026-05-07
updated: 2026-05-07
problem: ./problem.md
---

# Merge Conflict Auto-Resolver — Design

## Summary

When `merge_ticket` raises `MergeConflictError`, the orchestrator spawns a built-in `conflict_resolver` agent in the preserved worktree. The agent finds and resolves all conflict markers, commits, and signals completion via `update_ticket`. The orchestrator then retries the merge once. Success → `RESOLVED`; failure → `MERGE_CONFLICT` (human path, unchanged).

## Approach

### New role: `conflict_resolver`

`jig/defaults/roles/conflict_resolver.yaml` ships as a jig built-in. Its prompt is focused:

1. Run `git diff --name-only --diff-filter=U` to list conflicted files.
2. For each file, read the content, understand both sides of every `<<<<<<<`/`=======`/`>>>>>>>` block, and edit to a resolved state.
3. Call `commit_progress` to commit the resolution.
4. Call `update_ticket(status="resolved")` to signal completion.

Tool set: `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash`. No `context7`, no `default_context`, no `allow_add_dependency`. The agent needs nothing beyond its own tools — the conflict markers are self-describing.

### New spawn reason: `CONFLICT_RESOLVER`

Added to `SpawnReason` in `jig/runtime.py` alongside `PHASE_PRIMARY`, `QA_RESPONDER`, and `EVALUATOR`.

### New private method: `Orchestrator._try_resolve_conflict`

```
_try_resolve_conflict(ticket_id, ticket) -> bool
```

- Loads `conflict_resolver` role via `load_role`.
- Builds `AgentSpawnContext` with `spawn_reason=CONFLICT_RESOLVER`, the existing worktree path, and `initial_bus_message = {"kind": "conflict_resolve_spawn", "ticket_id": ..., "branch": ...}`.
- Awaits `_run_agent_with_analytics`.
- Returns `True` on clean completion, `False` on any exception (missing role, spawn error, agent error).
- Never raises — any failure becomes `False`.

### Modified: `Orchestrator._on_ticket_completed`

The `except MergeConflictError` block gains one step:

```python
except MergeConflictError as exc:
    resolved = await self._try_resolve_conflict(ticket_id, ticket)
    if resolved:
        try:
            merge_result = await merge_ticket(...)
        except MergeConflictError:
            merge_conflict = True   # give up → human path
    else:
        merge_conflict = True       # agent failed → human path
```

Everything below this block is unchanged.

## Interfaces

No new public API. The `conflict_resolver` role file is the only new user-visible artifact; projects that want to override it can place their own `conflict_resolver.yaml` in `.jig/roles/`.

## Data model

No new persistent state. The conflicted worktree already exists on disk when the resolver runs; it uses it as-is.

## Alternatives considered

### New `CONFLICT_RESOLVING` ticket status

Set status to `CONFLICT_RESOLVING`, pop the ticket from `_running_tickets`, dispatch the agent, re-trigger `_on_ticket_completed` when the agent completes. More observable in the TUI but adds a new status enum value, new bus event, new TUI rendering, and new test surface. Rejected: significant complexity for no functional gain over the inline retry.

### Configurable retry count

Add `conflict_resolve_retries: N` to the project YAML. Rejected: YAGNI. One retry is sufficient; add the config knob if experience proves otherwise.

### Reuse the project's dev role

Spawn the project's `dev` agent instead of a built-in role. Rejected: the dev prompt is task-oriented (implement features, run tests, add dependencies). A conflict resolver needs none of that context — a focused prompt produces a faster, more reliable result.

### Chosen: inline retry with built-in role

Smallest change, contained to `_on_ticket_completed` and a new role file. No new status, no new events, no TUI changes. Falls back to the existing human path on any failure.

## Risks

- The resolver agent could make a semantically wrong resolution (choose the wrong side of a conflict). Mitigated: the merged result will still have to pass the test phase if the workflow includes one; a bad resolution surfaces there rather than silently.
- If the agent loops or stalls, it will block progress on that ticket. Mitigated: the existing stall detector (`evals/watcher`) applies to resolver spawns like any other agent.

## Out of scope

- PM-level file-overlap detection / `depends_on` scheduling.
- Notification events for "resolver attempted."
- Multi-retry loop.

## Open questions

- [ ] None blocking.

## Change log

- 2026-05-07: Initial draft (brent)
