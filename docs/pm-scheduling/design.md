---
title: PM Scheduling — Design
type: design
status: active
owner: brent
created: 2026-05-08
updated: 2026-05-08
problem: ./problem.md
---

# PM Scheduling — Design

## Summary

Three coordinated changes reduce merge conflicts caused by PM over-parallelism: (1) PM prompt improvements that enforce a small-project linear chain and a bones-first gate before fan-out; (2) an optional `max_parallel` cap on the `Project` model that the orchestrator enforces as a hard concurrency limit; and (3) a post-conflict replan spawn that lets the PM add ordering constraints to pending tickets after a conflict is resolved.

## Approach

### 1. PM prompt changes (`jig/defaults/roles/pm.yaml`)

Two new rules replace the unconditional "Maximize parallelism" instruction:

**Small project rule:** If the total number of tickets being created is ≤ 10, create a fully linear dependency chain — each ticket depends on the previous one. No parallel fan-out.

**Bones-first rule:** The first implementation ticket in any group of related features acts as a sequential gate. Do not create parallel tickets that depend on the same setup ticket until that first ticket has been identified and placed at the front of the chain. Other tickets depend on it, not on each other's predecessors.

These rules are enforced by the LLM following instructions. The `max_parallel` cap (below) is the mechanical backstop.

### 2. `max_parallel` cap (`jig/models.py` + `jig/orchestrator.py`)

`Project` gains an optional field:

```python
max_parallel: int | None = None
```

`None` means no cap (current behavior — fully backward compatible). Operators set this in their `project.yaml` when they want a hard limit regardless of what the PM planned.

In `Orchestrator._start_ready_tickets`, before dispatching each ready ticket:

```python
if project.max_parallel and len(self._running_tickets) >= project.max_parallel:
    break
```

Tickets that are ready but blocked by the cap remain in the ready queue and are dispatched as running tickets complete (the existing completion-triggered re-dispatch already handles this).

### 3. Post-conflict replan (`jig/runtime.py` + `jig/prompt_builder.py` + `jig/orchestrator.py`)

A new `SpawnReason.REPLAN` is added alongside `CONFLICT_RESOLVER`.

After `_try_resolve_conflict` returns `True` and the retry merge succeeds, the orchestrator spawns a replan agent (fire-and-forget, non-blocking):

```
_try_replan(ticket_id, conflicted_files, pending_ticket_ids) -> None
```

- Loads the `pm` role via `load_role` (project override first, then default).
- Builds `AgentSpawnContext` with `spawn_reason=REPLAN` and an `initial_bus_message` carrying the conflict info: which files conflicted and which pending ticket IDs exist.
- Dispatches `_run_agent_with_analytics` without awaiting its result (or awaits in a background task).
- Never raises — any failure is logged and scheduling continues as-is.

The replan PM is prompted to:
1. Read the conflict info from its initial bus message.
2. List pending tickets using `list_tickets(status="pending")`.
3. For each pending ticket likely to touch the conflicted files, add a `depends_on` constraint using `update_ticket` to serialize it behind tickets that already resolved the conflict area.
4. Exit when done — no ticket status to update (the replan agent is not tied to a ticket).

The replan PM may **not** create or delete tickets. Its only output is `update_ticket` calls adjusting `depends_on` on not-yet-started tickets.

## Interfaces

No new public API. `max_parallel` in `project.yaml` is the only new operator-visible surface:

```yaml
max_parallel: 3  # optional; omit for no cap
```

## Data model

`Project` model gains one optional field:

```python
max_parallel: int | None = None
```

No migration needed — existing `project.yaml` files that omit the field deserialize to `None` (Pydantic default).

## Alternatives considered

### PM prompt only (no model change, no replan)

Add the small-project and bones-first rules to `pm.yaml` only. No `max_parallel` field, no replan spawn. Simple but relies entirely on LLM compliance — a PM that ignores its instructions still fans out unconditionally. Rejected: without a mechanical backstop, the fix is unreliable.

### Orchestrator-enforced file-overlap tracking

After each merge conflict, record which files conflicted in a persistent store. Before dispatching a ticket, check if any of the files it "will touch" (inferred from its description) are in the conflict set. Rejected: the orchestrator cannot reliably infer which files a ticket will touch from its spec text; this requires LLM inference that belongs in the replan PM, not in the orchestrator.

### Dynamic `max_parallel` computed from ticket count

Set `max_parallel = 1` automatically when total ticket count ≤ 10, without requiring an operator config field. Rejected: the PM prompt rule handles the small-project case; adding automatic orchestrator computation creates two overlapping mechanisms with unclear precedence.

### Chosen: PM prompt + static `max_parallel` cap + replan spawn

PM prompt handles the common case semantically. `max_parallel` is a simple, explicit operator escape hatch with no magic. Replan gives the PM a chance to fix ordering after a conflict surfaces. Three independent layers, each comprehensible on its own.

## Risks

- The replan PM may add overly conservative `depends_on` constraints, serializing tickets that could have run safely in parallel. Mitigated: the replan is one-shot and only fires after an actual conflict — it addresses a confirmed problem, not a hypothetical one.
- The replan PM may fail to identify pending tickets correctly if the ticket store is large. Mitigated: it uses `list_tickets` with a status filter; the PM has cross-ticket access.
- A project with `max_parallel: 1` and a slow ticket pipeline may be noticeably slower than before. Mitigated: this is an explicit operator choice; the default is `None` (no cap).

## Out of scope

- PM-level file-overlap prediction at plan time.
- Automatic `max_parallel` inference from ticket count.
- Multi-retry replan loop.
- Notification events for "replan attempted."
- Changes to the v2 planner PM (`planner_pm.yaml`) — it already has `bones_first` ordering.

## Open questions

- [ ] None blocking.

## Change log

- 2026-05-08: Initial draft (brent)
