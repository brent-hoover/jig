---
title: Shared Learnings System — Implementation Plan
type: plan
status: draft
owner: Brent Hoover
created: 2026-05-25
updated: 2026-05-25
design: ./design.md
---

# Shared Learnings System — Implementation Plan

## Overview

Three small changes in dependency order: store method first (foundation), then the MCP handler
that calls it, then the tool schema that exposes it to agents. Tests accompany each step.
No read-path changes; no model changes.

## Preconditions

- [x] Design approved
- [x] `feature-work/learnings-system/design.md` written

## Steps

### 1. Update `MemoryStore.add_role_learning` to accept a list of roles

**What:** Change signature from `add_role_learning(*, role: str, content: str) -> str` to
`add_role_learning(*, roles: list[str], content: str) -> list[str]`. Iterate `roles`, insert
one `Learning` per role, return all inserted IDs. Update existing call sites (there is one:
`ticket_mcp.handle_record_learning`).

**Why:** All other changes depend on the store being able to fan-out.

**Verify:** Update `tests/test_store_memory.py` — existing tests pass with the new signature
(callers updated to `roles=["dev"]`); add a test confirming two records are written when
`roles=["dev","test"]` and that each is retrievable via `get_role_learnings`.

### 2. Update `handle_record_learning` to read `roles` from args

**What:** In `ticket_mcp.handle_record_learning`, read `args.get("roles") or [role]` and pass
to `memory.add_role_learning(roles=..., content=...)`. Update return string to list all roles
written. No other changes to `ticket_mcp.py`.

**Why:** Wires the store change to the MCP layer.

**Verify:** Update `tests/test_ticket_mcp.py` — existing `record_learning` test still passes
(no `roles` arg → defaults to `[role]`); add a test that passes `roles=["dev","test"]` and
confirms records appear under both roles.

### 3. Expose `roles` parameter in the MCP tool schema

**What:** In `mcp_server.py`, update the `record_learning` tool definition:
- Add `"roles": list` to the schema dict (optional)
- Update description to explain the parameter and list that omitting it defaults to the
  calling agent's role; include an example: `["dev", "test", "review"]` for project-wide
  findings.

**Why:** Agents only see what the tool schema exposes. Without this step the `roles` parameter
is silently ignored.

**Verify:** Run full test suite (`uv run pytest tests/ -v`), ruff check, ruff format --check.

## Rollback

All changes are backward-compatible — `roles` is optional everywhere. If something goes wrong,
revert the three files (`store/memory.py`, `ticket_mcp.py`, `mcp_server.py`) and the existing
single-role behavior is restored.

## Out of scope for this plan

- Tag filtering or tag-scoped loading
- Token-budget enforcement beyond the existing `limit=20`
- Staleness / expiration
- Exposing `tags` in the MCP tool

## Change log

- 2026-05-25: Initial draft (Brent Hoover)
