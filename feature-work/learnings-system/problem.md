---
title: Shared Learnings System — Problem Statement
type: problem
status: draft
owner: Brent Hoover
created: 2026-05-25
updated: 2026-05-25
---

# Shared Learnings System — Problem Statement

## Context

Jig orchestrates multiple agents across many tickets in a project run. Each agent can call the
`record_learning` MCP tool to save observations. These are stored in `MemoryStore` keyed by
role (`add_role_learning`) and loaded at spawn time via `get_role_learnings(ctx.role)`.
This gives each role a persistent scratchpad but creates no project-wide channel.

When a dev agent discovers a workaround — say, suppressing a mypy error for a specific module —
that information lives only in the file it wrote and in the JSONL store under the dev role. A
test agent spawned two minutes later on a different ticket has no way to see it. A dev agent in
a subsequent session won't see it either; `get_role_learnings` returns role-scoped records
only, and there is no cross-role or project-scoped retrieval path.

## Problem

There is no project-scoped accumulator of non-obvious decisions and workarounds. Every agent
runs with blank project-knowledge, causing:

- **Duplicate discovery** — the same workaround is re-found by successive agents working
  independently on related tickets.
- **Wasted tokens and time** — agents repeat debugging steps already performed and
  documented elsewhere.
- **No self-knowledge accumulation** — the project never builds a corpus of "here's what
  we've learned the hard way" that narrows the solution space for future agents.

Concrete example from the `hn-cli-20260524T144910Z` eval: ticket 2's dev agent added
`[[tool.mypy.overrides]] ignore_errors = true` for `tests.test_top_cmd` at 14:22 to silence a
`call-overload` error from `int(item["score"])` against `dict[str, object]`. Ticket 3's test
agent spawned at 14:24 — two minutes later — and wrote `tests/test_filter_flags.py` with the
exact same problematic pattern. The same validate failure triggered, the same fix was
rediscovered: ~$0.70 of avoidable rework from a two-minute propagation gap.

## Simplest possible solution

Append every `record_learning` call to a single project-level flat file alongside role-scoped
learnings. Load the full file into every dev/test/review agent's prompt.

## Complications considered

- **Scale**: Applies. The accumulator grows unboundedly across project runs. An uncapped flat
  file loaded wholesale eventually blows the context window. Requires token-budget enforcement,
  tag-scoped filtering, or periodic summarization.
- **Concurrency**: Applies mildly. Multiple agents run concurrently and may write project
  learnings simultaneously. The existing `TypedCollection` JSONL append mechanism handles this
  already — no new concern.
- **Failure modes**: N/A — if loading fails, agents fall back to prompts without project
  learnings. Worst outcome is a miss, not a crash.
- **Cross-cutting policies**: N/A — learnings contain no PII, secrets, or auth material.

## Constraints

- Must fit within the existing JSONL append-store and `MemoryStore` architecture; no new
  external storage or services.
- No migration script required — existing role-scoped learning records are unaffected.
- The `record_learning` MCP tool interface must extend gracefully (optional new parameters)
  without breaking existing callers.

## Requirements

- An agent can record a learning with an explicit list of target roles.
- A learning written with `roles=["dev","test"]` is visible to both dev and test agents
  subsequently spawned in the same project, regardless of ticket.
- Omitting `roles` records the learning only for the calling agent's role (existing behavior).
- Each entry in `roles` must be a non-empty string; invalid entries are rejected.

## Non-goals

- Tag-scoped or token-budgeted loading — the existing `limit=20` in `get_role_learnings`
  is the current guard; further budgeting is a follow-on.
- A separate `## Project Learnings` prompt section — learnings from other roles surface
  naturally in the existing `## Memories` section.
- Expiration / staleness detection (accepted risk; follow-on).
- PM/SA agents loading cross-role learnings.
- UI or CLI interface for browsing/editing project learnings.

## Success criteria

- An agent calls `record_learning` with `roles=["dev","test"]`; a test agent spawned
  subsequently on a different ticket sees that entry in its `## Memories` context.
- Omitting `roles` is backward-compatible — learning is recorded only for the calling role.
- Invalid `roles` entries (non-string, empty) are rejected with a clear error.
- Unit tests confirm fan-out persistence, per-role retrieval, and default behavior.

## Open questions

None — resolved during design; see `design.md`.

## Change log

- 2026-05-25: Initial draft (Brent Hoover)
- 2026-05-25: Align requirements/success criteria with roles fan-out implementation (Brent Hoover)
