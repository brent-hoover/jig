---
title: Issue Tracker Front Doors — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-06-08
updated: 2026-06-08
---

# Issue Tracker Front Doors — Problem Statement

## Context

jig's `store/` layer is a JSONL-backed work model: tickets carry a status workflow, a dependency graph
(`blocks`/`blocked_by`/`parent_id`), classification (`work_type`, `size`), acceptance criteria, comments, and threads.
It is already a capable issue tracker internally — `find_ready()` and `_unblock_dependents()` give it
dependency-aware dispatch that rivals purpose-built trackers like beads.

Today the only way to create or mutate a ticket is from *inside* the orchestrator: an agent jig spawned calls the
per-agent MCP server (`mcp_server.py` / `ticket_mcp.py`), whose handlers are bound to agent context — a worktree, a
sender identity, commit tooling, and an in-process `MessageBus`. There is no way for a process that jig did not spawn
to file work into the store.

## Problem

A human or an agent that jig did not spawn (e.g. a Claude Code session working in the same checkout, or an ad-hoc
script) has no supported way to create or read issues in jig's store. Concretely:

- The ticket-mutation handlers in `ticket_mcp.py` require agent context (`worktree`, `sender`, correlation binding) and
  are exposed only through the in-process SDK MCP server jig stands up per agent. A separate process cannot call them.
- Ticket IDs default to a UUID4 (`StoreModel.id`). There is no human-usable handle, so even if a CLI existed,
  `show 3f2a8c1e-…` would be unusable ergonomically.
- The `MessageBus` is in-process pub/sub (asyncio queues that persist to `messages.jsonl` but only notify in-memory
  subscribers). A second process appending a ticket cannot notify a running orchestrator through it.

The result: jig's store is a good tracker that only jig's own agents can reach.

## Simplest possible solution

Expose the existing store through two thin front doors — a `jig issue …` CLI subgroup and a standalone stdio MCP
server — both calling a small context-free service layer over `TicketStore`/`ThreadStore`. Keep storage, the data
model, and the orchestrator exactly as they are. Add a monotonic `jig-N` key as a display/lookup alias so the CLI has
a usable handle. Let a running orchestrator pick up externally-created issues on its next ready-scan, with a
low-frequency reconcile tick so pickup is eventually guaranteed.

Issues created through the front doors do **not** land in a dispatchable state. They land in a non-dispatchable
"proposed" status and require an explicit operator approval before a running orchestrator will work them. Approval
promotes the issue to `OPEN` (the existing dispatchable status), after which the next ready-scan picks it up, with a
low-frequency reconcile tick so post-approval pickup is eventually guaranteed.

This is deliberately *not* a rewrite: it is adapters over what already works, plus the small additions (short key,
proposed→approved gate, reconcile tick) that the front doors actually require.

## Complications considered

- **Scale**: N/A — bounded. Issue volume is human/agent-authored, not machine-generated at scale; the store is already
  JSONL and unchanged here.
- **Concurrency**: **Applies.** CLI and standalone MCP run as separate OS processes from a running orchestrator. Two
  processes may append to `tickets.jsonl` and increment the `jig-N` counter concurrently. This forces cross-process
  serialization (file locking) on the create path and the counter — the existing in-process `asyncio.Lock` does not
  span processes.
- **Failure modes**: **Applies, narrowly.** A create that fails validation (missing AC, bad `work_type`) must fail
  loudly with a clear message and a non-zero exit (CLI) / tool error (MCP) — no partial writes. A crash mid-append must
  not corrupt the JSONL or leak a consumed counter value. Otherwise fail-loud with no recovery machinery.
- **Cross-cutting policies**: Mostly N/A. No PII/secrets/auth surface beyond filesystem access to `.jig/store/`, which
  is the same trust boundary the orchestrator already assumes (shared working directory). `created_by` provenance must
  be recorded so externally-authored issues are attributable.

## Constraints

- Storage stays runtime-only JSONL in `.jig/store/` — not git-committed, not relocated. Consumers share access by
  sharing the working directory.
- jig's work contract is enforced uniformly: every issue, regardless of front door, must have an Acceptance Criteria
  section and a valid `work_type`. No relaxed/lightweight issue tier.
- Internal `id` (UUID4) must not change — `blocks`/`blocked_by`/`parent_id` references and the `tickets.{id}` bus topic
  depend on it.
- The orchestrator's dispatch hot path and the existing per-agent MCP must not be rewired by this work.
- Async throughout; type hints on new code; ruff + pytest per project conventions.

## Requirements

- A `jig issue` CLI subgroup supporting create / list / show / update / close / comment / link.
- A standalone stdio MCP server an external agent can register in its own `.mcp.json`, exposing the same operations.
- Both front doors call one context-free service layer that enforces the full AC + `work_type`/`size` contract.
- A human-usable `jig-N` short key, assigned at create, accepted interchangeably with the UUID for lookup.
- Cross-process-safe create and key assignment.
- Issues created through the front doors land in a non-dispatchable "proposed" state — never auto-dispatched.
- An explicit operator approval action (CLI / TUI, **not** the external MCP) promotes a proposed issue to the
  dispatchable `OPEN` state.
- After approval, a running orchestrator picks the issue up without further manual intervention.

## Non-goals

- Git-committed or cross-repo-portable issues (beads-style). Storage stays runtime-only and working-dir-local.
- Full extraction of all ticket CRUD (including the per-agent MCP) onto the new service — the agent MCP keeps its
  richer, context-bound handlers.
- Active push notification from CLI/MCP to a running daemon (no socket/WS ping). Pickup is scan-based.
- Relaxing or making optional the AC / `work_type` invariants.
- Replacing the internal UUID id scheme.
- Allowing external (MCP) callers to approve their own issues for work — approval is an operator-only action.

## Success criteria

- From a plain shell in a jig-initialized checkout, `jig issue create … && jig issue list` creates and shows an issue
  with a `jig-N` key, with no orchestrator running.
- An external Claude Code agent with the standalone MCP registered can create and read issues via tool calls.
- A freshly created issue (any front door) is **not** dispatched by a running orchestrator — it sits in the proposed
  state until approved. The standalone MCP exposes no way to approve it.
- After an operator runs the approval action, the issue is dispatched within the reconcile interval, without further
  manual action.
- Creating an issue without an AC section, or with an invalid `work_type`, fails loudly (non-zero exit / tool error)
  and writes nothing.
- Concurrent creates from two processes never collide on a `jig-N` key or corrupt `tickets.jsonl`.
- New code passes `ruff check`, `ruff format --check`, and the pytest suite.

## Open questions

- [ ] Does the existing `_collection` write path already hold a cross-process lock, or only the in-process
      `asyncio.Lock`? (Determines whether locking is new work or a tightening of existing code.) — to be resolved in
      design.
- [ ] Where does the `jig-N` counter persist — a dedicated `key_seq` file under `.jig/store/`, or a single-row store?
      — design detail.
- [ ] Does the non-dispatchable landing state warrant a new `TicketStatus` (e.g. `PROPOSED`), or can an existing value
      be reused? (Current statuses — `OPEN`, `BLOCKED`, `NEEDS_INFO`, etc. — none cleanly mean "awaiting approval to
      start.") — to be resolved in design.

## Change log

- 2026-06-08: Initial draft (brent)
- 2026-06-08: Front-door issues land in a non-dispatchable "proposed" state requiring explicit operator approval before
  dispatch; approval is not exposed on the external MCP (brent)
