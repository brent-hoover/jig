---
title: Shared Learnings System — Design
type: design
status: active
owner: Brent Hoover
created: 2026-05-25
updated: 2026-05-25
problem: ./problem.md
---

# Shared Learnings System — Design

## Summary

Extend the existing `record_learning` MCP tool with an optional `roles` parameter. When an
agent supplies `roles: ["dev", "test"]`, the handler fan-outs and writes one `Learning` record
per role. The read path (`get_role_learnings`) is completely unchanged — each agent loads its
own role's learnings as before, but now those may include entries written by agents of other
roles. Default behavior (`roles` omitted) is identical to today.

## Approach

**Fan-out on write.** When `record_learning` is called with `roles=["dev","test","review"]`,
`handle_record_learning` iterates the list and calls `memory.add_role_learning` once per role.
Each resulting `Learning` record is identical except for the `role` field.

Since `.jig/store/` is per-project, cross-role sharing is automatically project-scoped — no
sentinel values, no new collection, no read-side changes needed.

The write is synchronous from the calling agent's perspective: by the time `record_learning`
returns, all fan-out records are appended to `learnings.jsonl`. Any agent spawned afterward
sees them on its first `get_role_learnings` call.

## Interfaces

### MCP tool: `record_learning`

Before:
```
record_learning(content: str) → str
```

After:
```
record_learning(content: str, roles: list[str] | None = None) → str
```

- `roles`: optional list of role names to share this learning with. Defaults to
  `[current_role]`, preserving existing behavior. Example: `["dev", "test", "review"]`.
- Tool description updated to explain the `roles` parameter and nudge agents to use it when
  a finding applies beyond their own role.

### `MemoryStore.add_role_learning`

Before:
```python
async def add_role_learning(self, *, role: str, content: str) -> str
```

After:
```python
async def add_role_learning(self, *, roles: list[str], content: str) -> list[str]
```

Iterates `roles`, inserts one `Learning` per role, returns list of inserted IDs.

### `ticket_mcp.handle_record_learning`

```python
async def handle_record_learning(
    *,
    memory: MemoryStore,
    role: str,
    args: dict,
) -> str
```

Reads `args.get("roles", [role])` and passes to `memory.add_role_learning`.

## Data model

No schema changes. The `Learning` model already has `role: str`, `content: str`, `tags:
list[str]`, and `timestamp: str`. Fan-out produces N records that are structurally identical
to today's records — one per role.

The `tags` field remains available for future use but is out of scope here.

## Alternatives considered

### Single record with `roles: list[str]` + read-side intersection

Store one record with `roles=["dev","test"]` and filter on read with a list-intersection
check in `get_role_learnings`. Avoids duplicate storage but requires changing the read path,
the `Learning` model, and the `TypedCollection` index (which indexes exact field values, not
list membership). More code, no benefit over fan-out at this scale.

### Separate `project_learnings.jsonl` collection

New `ProjectLearning` model, new `TypedCollection`, new `MemoryStore` methods. Cleanest data
model and enables independent eviction later, but that's a non-goal. More code for the same
observable behavior.

### Boolean `project_scoped` flag with `__project__` sentinel role

`project_scoped=true` stores with `role="__project__"` and `build_agent_prompt` loads it for
all qualifying roles. Works, but the sentinel is a smell and "all qualifying roles" requires
a hardcoded allowlist somewhere. The `roles` array is more explicit and avoids the sentinel.

### Chosen: fan-out on write with `roles` array

Minimal change — one new optional parameter, one updated handler, one updated store method.
Read path is untouched. Behavior is immediately correct for the next spawned agent. Fits
naturally into the existing model without new types, files, or index changes.

## Risks

- **Role name typos / wrong targets** — `handle_record_learning` validates each requested role
  against `valid_roles` (the project's known role set, passed in from `create_agent_mcp_server`)
  and rejects unknown names. When `valid_roles` is empty (e.g. in tests without a full project
  context), the check is skipped. An empty `roles=[]` list is rejected with a clear error.
- **Fan-out amplification** — a learning written to 5 roles stores 5 records. At current
  scale (tens of learnings per run, 3–4 roles) this is negligible. Worth noting if the corpus
  ever grows large.

## Out of scope

- Tag-scoped loading / filtering by tags.
- Token-budget enforcement on learnings load (existing `limit=20` in `get_role_learnings`
  is the current guard).
- Staleness detection or expiration.
- Exposing `tags` in the MCP tool.
- Per-role opt-in for receiving cross-role learnings (e.g. `receives_cross_role_learnings:
  bool` on `RoleConfig`). Roles are user-defined; a blanket allowlist would need to be
  maintained by the operator. Follow-on if needed in practice.

## Open questions

None — design is fully resolved.

## Change log

- 2026-05-25: Initial draft (Brent Hoover)
