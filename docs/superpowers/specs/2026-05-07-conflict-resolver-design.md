# Merge Conflict Auto-Resolver

**Date:** 2026-05-07  
**Status:** Approved

## Problem

When parallel ticket branches touch the same lines, jig routes the ticket to `MERGE_CONFLICT` and expects a human to resolve it manually. The worktree is preserved but nothing moves forward until a human intervenes.

## Goal

On a merge conflict, automatically spawn a focused agent to resolve the conflict markers, commit the resolution, and retry the merge. If the agent succeeds, the ticket resolves normally. If it fails, fall through to the existing human-review path — no regression.

## Approach

Inline retry in `_on_ticket_completed` (Approach A). No new ticket status, no new bus event type, no TUI changes. One retry attempt; if the agent cannot fix it, the ticket lands in `MERGE_CONFLICT` as before.

## Components

### 1. `jig/defaults/roles/conflict_resolver.yaml`

New built-in role. Focused prompt:

- Run `git diff --name-only --diff-filter=U` to list conflicted files
- Read each file, understand both sides of each conflict marker block, edit to a resolved state
- Commit the resolution via `commit_progress`
- Call `update_ticket(status="resolved")` to signal completion

Tool set: `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Bash`. No `context7`, no `default_context`, no `allow_add_dependency`.

### 2. `SpawnReason.CONFLICT_RESOLVER` in `jig/runtime.py`

New enum value alongside `PHASE_PRIMARY`, `QA_RESPONDER`, `EVALUATOR`.

### 3. `Orchestrator._try_resolve_conflict` (new private method)

Loads the `conflict_resolver` role from defaults. Builds an `AgentSpawnContext` with:

- `spawn_reason=SpawnReason.CONFLICT_RESOLVER`
- `worktree_path` pointing at the existing conflicted worktree (`.jig/worktrees/<ticket_id>`)
- `initial_bus_message` with `kind="conflict_resolve_spawn"`, `ticket_id`, `branch`

Awaits `_run_agent_with_analytics`. Returns `True` if the agent completes without exception, `False` on any failure (missing role, spawn error, agent error). Never raises — a resolver failure must not turn a conflict into a crash.

### 4. `Orchestrator._on_ticket_completed` (modified)

The `except MergeConflictError` block gains one step before routing to `MERGE_CONFLICT`:

```
except MergeConflictError as exc:
    resolved = await _try_resolve_conflict(ticket_id, ticket)
    if resolved:
        # retry merge — if it succeeds, fall through to RESOLVED
        try:
            merge_result = await merge_ticket(...)
        except MergeConflictError:
            merge_conflict = True  # give up, fall through to human path
    else:
        merge_conflict = True  # agent failed, fall through to human path
```

## Data Flow

```
_on_ticket_completed
  └─ merge_ticket() → MergeConflictError
      └─ _try_resolve_conflict(ticket_id, ticket)
          ├─ load conflict_resolver role
          ├─ AgentSpawnContext(CONFLICT_RESOLVER, worktree, initial_bus_message)
          ├─ _run_agent_with_analytics(ctx)
          └─ returns True / False
      ├─ True  → retry merge_ticket() → RESOLVED (or MERGE_CONFLICT on second fail)
      └─ False → MERGE_CONFLICT (existing human path)
```

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| `conflict_resolver.yaml` not found | Log warning, return `False` → MERGE_CONFLICT |
| Agent spawn exception | Log warning, return `False` → MERGE_CONFLICT |
| Agent runs but leaves markers | Retry merge fails → MERGE_CONFLICT |
| Retry merge succeeds | RESOLVED, normal cleanup |

## What Does Not Change

- `MERGE_CONFLICT` ticket status — still exists, still the human-escalation path
- `ticket_merge_conflict` bus event — still emitted when the resolver gives up
- Worktree preservation on unresolved conflict — unchanged
- TUI — no changes needed

## Testing

- Unit: `_try_resolve_conflict` returns `False` when role file is missing (mock `load_role` to raise `FileNotFoundError`)
- Unit: `_try_resolve_conflict` returns `False` when `_run_agent_with_analytics` raises
- Integration: after the resolver runs and commits, `merge_ticket` retry succeeds → ticket reaches `RESOLVED`
- Existing MERGE_CONFLICT tests must still pass (no regression on the give-up path)
