---
title: Issue Tracker Front Doors — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-06-08
updated: 2026-06-08
design: ./design.md
---

# Issue Tracker Front Doors — Implementation Plan

## Overview

Build bottom-up so the risky core lands first behind tests, then the adapters, then the orchestrator integration. Order:
(1) the `PROPOSED` status, (2) the `jig-N` key + cross-process-safe create path, (3) the `IssueService` seam, (4) the
CLI adapter, (5) the standalone stdio MCP adapter, (6) the orchestrator reconcile tick. Each step is test-first and
self-contained; steps 1–3 are pure library code with no external surface, so they are the cheapest to verify. The whole
feature is one PR; steps are commits within it.

## Preconditions

- [x] `design.md` approved.
- [x] Worktree `feat/issue-frontdoors` created.
- [x] Open questions resolved by design leans: lazy key backfill on first front-door access (step 3); reconcile interval
      a 30s constant (step 6).

## Steps

### 1. Add the `PROPOSED` status

**What:** Add `TicketStatus.PROPOSED = "proposed"` in `jig/ticket.py`. Audit the ~20 files referencing `TicketStatus`
for any exhaustive handling that must account for the new value; the dispatch path needs none (`find_ready()` is
OPEN-only). Add a `PROPOSED` column to the TUI board (`_BOARD_COLUMNS`) and an entry to the status→color map in
`jig/tui/screens/tickets.py`.

**Why:** Establishes the non-dispatchable landing state the front doors depend on, independent of all other work.

**Verify:** Unit test: a `PROPOSED` ticket is absent from `find_ready()` candidates even with no blockers and a
top-level `work_type`. `ruff check` + full suite green. Manual: TUI renders the new column.

**References:** design §"The `PROPOSED` gate".

### 2. `jig-N` key and cross-process-safe create path

**What:** Add `key: str` to `Ticket` (`jig/ticket.py`). In `jig/store/tickets.py` (`TicketStore.create`) assign the key
from a per-project counter persisted at `.jig/store/issue_seq`, with the read→assign→append→persist sequence wrapped in
an `fcntl.flock` on `.jig/store/.issue.lock`, retaining the existing `asyncio.Lock`. Add key→id resolution helper
(accept `jig-N` or UUID). All tickets (internal and front-door) get a key.

**Why:** Provides the human handle and closes the cross-process counter race for every writer.

**Verify:** Tests: (a) sequential creates assign `jig-1`, `jig-2`, …; (b) two concurrent OS processes (or two event
loops + real `flock`) creating tickets never collide on a key and produce contiguous-or-gapped-but-unique keys;
(c) resolver returns the same ticket for `jig-N` and its UUID; (d) crash-before-persist simulation does not reissue a
key. Suite + `ruff` green.

**References:** design §"`jig-N` key and cross-process safety", §"Data model".

### 3. `IssueService`

**What:** New `jig/issues/service.py` — context-free async class over `TicketStore`/`ThreadStore`. Methods: `create`
(enforces AC presence + valid `work_type`/`size`, sets `PROPOSED`, assigns key, records `created_by`), `get`, `list`,
`update`, `close`, `comment`, `link`, `approve` (`PROPOSED → OPEN`). `ref` args accept `jig-N` or UUID. Lazy-backfill a
key on first access for any pre-existing keyless ticket.

**Why:** The single validated path all front doors share; isolates CRUD from agent context.

**Verify:** Tests: create with missing AC fails loudly and writes nothing; create with bad `work_type` fails loudly;
happy-path create yields `PROPOSED` + a key; `approve` flips to `OPEN`; `link` sets `blocks`/`blocked_by`/`parent_id`
and the reverse edges; `list` filters; keyless legacy ticket gets a key on access. Suite + `ruff` green.

**References:** design §"`IssueService`".

### 4. CLI adapter — `jig issue`

**What:** Add `@cli.group("issue")` in `jig/cli.py` with subcommands `create`, `list`, `show`, `update`, `approve`,
`close`, `comment`, `link` per the design's CLI table. Body input via `--body` / `--body-file` / `-` (stdin) / `$EDITOR`.
Failures exit non-zero with a clear message.

**Why:** The human/shell front door.

**Verify:** Tests via Click's `CliRunner`: create prints the assigned `jig-N`; `show` accepts key and UUID; missing-AC
create exits non-zero and writes nothing; `approve` transitions status. Manual smoke in a jig-initialized checkout with
no orchestrator running: `jig issue create … && jig issue list`.

**References:** design §"Interfaces / CLI".

### 5. Standalone stdio MCP adapter

**What:** New `jig/issues/mcp.py` exposing `issue_create`, `issue_list`, `issue_show`, `issue_update`, `issue_close`,
`issue_comment`, `issue_link` over `IssueService` — **no `issue_approve`**. Add a launchable stdio entrypoint (console
script / `python -m jig.issues.mcp`) external agents register in their own `.mcp.json`. `created_by` carries the caller
name. Distinct from `create_agent_mcp_server`.

**Why:** The non-jig-agent front door.

**Verify:** Tests: each tool handler maps to the right `IssueService` call and shape; the tool set contains no approve
tool; missing-AC create returns a tool error, not a partial write. Manual: register the server in a separate Claude
Code session and create + read an issue.

**References:** design §"Interfaces / Standalone MCP tools".

### 6. Orchestrator reconcile tick

**What:** Add a low-frequency (30s constant) background task in `jig/orchestrator.py` that, under the store lock,
re-reads `tickets.jsonl` to merge externally-appended records into the in-memory map, then runs the existing
ready-scan. Wire it into the orchestrator's task lifecycle (start/cancel alongside the other background tasks).

**Why:** The only path by which an externally-created-then-approved issue reaches dispatch on a live orchestrator.

**Verify:** Integration test: with a running orchestrator (test harness), an out-of-band append to `tickets.jsonl` in
`PROPOSED` is **not** dispatched; after `IssueService.approve` flips it to `OPEN`, the next reconcile reloads and
dispatches it. Confirm the reload is idempotent (no duplicate dispatch). Suite + `ruff` green.

**References:** design §"Reconcile tick".

## Rollback

Pure additive feature on a feature branch — no production, no shared infra, no schema migration (`.jig/store/` is
regenerable). If any step destabilizes, revert its commit; steps 1–3 are independent of 4–6. The `flock`/counter change
(step 2) is the only one touching the shared write path — if it regresses, reverting step 2 (and the `key` field)
restores prior behaviour, with steps 3–6 reverted as dependents.

## Out of scope for this plan

- Git-committed / portable issues, cross-repo use.
- Routing the per-agent MCP through `IssueService`.
- Active daemon push notification.
- A web/TUI authoring surface beyond the existing board + the new `PROPOSED` column.

## Change log

- 2026-06-08: Initial draft (brent)
