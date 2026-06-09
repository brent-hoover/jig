---
title: Issue Tracker Front Doors — Design
type: design
status: draft
owner: brent
created: 2026-06-08
updated: 2026-06-08
problem: ./problem.md
---

# Issue Tracker Front Doors — Design

## Summary

Expose jig's existing ticket store to processes jig did not spawn, through two thin front doors — a `jig issue …` CLI
subgroup and a standalone stdio MCP server — both calling a new context-free `IssueService` over `TicketStore` /
`ThreadStore`. The service enforces the full work contract (AC + `work_type`/`size`) on every caller. Issues created
through either front door land in a new non-dispatchable `PROPOSED` status and require an operator-only approval
(`PROPOSED → OPEN`) before the orchestrator will work them. Each ticket gains a human-usable monotonic `jig-N` key
(internal UUID unchanged). Cross-process correctness is handled with an `flock`-guarded create critical section; a
low-frequency orchestrator reconcile tick reloads externally-appended tickets from disk before scanning for ready work.

## Approach

### Components

| Component | Location (new unless noted) | Responsibility |
|---|---|---|
| `IssueService` | `jig/issues/service.py` | Context-free async CRUD + link/approve over the stores; enforces the contract. |
| CLI subgroup | `jig/cli.py` (`@cli.group("issue")`) | Thin adapter; human-formatted I/O; non-zero exit on failure. |
| Standalone MCP | `jig/issues/mcp.py` + console entrypoint | stdio MCP server external agents register in their own `.mcp.json`. |
| `jig-N` key + counter | `jig/store/tickets.py`, `.jig/store/` | Key assignment in the shared create path; counter persisted + `flock`-guarded. |
| `PROPOSED` status | `jig/ticket.py` (`TicketStatus`) | Non-dispatchable landing state for front-door issues. |
| Reconcile tick | `jig/orchestrator.py` | Periodic disk-reload + ready-scan so approved external issues are dispatched. |
| Project-root discovery | `jig/issues/` | Walk up from cwd to the nearest ancestor containing `.jig/`; shared by the CLI and the standalone MCP. |

The orchestrator's dispatch hot path and the per-agent MCP (`mcp_server.py` / `ticket_mcp.py`) are **not** rewired. The
per-agent MCP keeps its richer, context-bound handlers; `IssueService` is a parallel, narrower path.

### `IssueService`

A plain async class constructed from a project path (which opens/loads the same `TicketStore` and `ThreadStore` the
orchestrator uses). It exposes:

- `create(...) -> Ticket` — validates AC presence and `work_type`/`size`, assigns `PROPOSED` status and a `jig-N` key,
  writes through the shared create path. `created_by` is a caller-supplied string (`"cli"`, the external agent name).
- `get(ref)`, `list(filters)`, `update(ref, ...)`, `close(ref)`, `comment(ref, body, author)`,
  `link(ref, blocks=/blocked_by=/parent=, remove=)`.
- `approve(ref) -> Ticket` — transitions `PROPOSED → OPEN`. Exposed by the CLI/TUI only, **not** by the MCP adapter.

`ref` accepts either a `jig-N` key or a raw UUID; the service resolves keys to the internal id. It takes no worktree,
agent binding, or commit tooling — that is the whole point of the seam.

The `PROPOSED → OPEN` transition is enforced as operator-only at the **store** layer, not just by omitting an MCP tool:
`TicketStore.update` rejects `PROPOSED → OPEN`, and the only sanctioned path is `TicketStore.approve` (which
`IssueService.approve` and the CLI call). This closes the bypass where `IssueService.update` / the agent MCP
`update_ticket` could otherwise set `status=open` directly and skip approval.

### The `PROPOSED` gate

Add `TicketStatus.PROPOSED = "proposed"`. `find_ready()` already filters on `status == OPEN`, so a `PROPOSED` ticket is
inherently never dispatched — no change to dispatch logic. Approval is the single transition `PROPOSED → OPEN` via
`IssueService.approve`. Internal creation paths (planning, agents) are unchanged and continue to create `OPEN` tickets
directly; only the front doors create `PROPOSED`.

### `jig-N` key and cross-process safety

The store core today guards writes with an in-process `asyncio.Lock` only and serves uniqueness from an in-memory map
built at `load()` — there is no cross-process lock. Two facts follow, and the design addresses both:

1. **Key assignment must be cross-process safe.** Key assignment moves into the shared `TicketStore.create` path so
   every ticket (internal or front-door) gets a `jig-N`. The create critical section — read counter → assign key →
   **persist counter → append record** — runs under an `flock` on a dedicated lockfile (`.jig/store/.issue.lock`) in
   addition to the existing `asyncio.Lock`. The counter is persisted *before* the append: a crash in between leaves a
   harmless gap (keys need not be contiguous), whereas the reverse order could reissue a key. The `flock` is
   process-level; the `asyncio.Lock` keeps in-memory state consistent within a process. The counter persists as a
   single integer in `.jig/store/issue_seq`.
2. **A running orchestrator holds a stale in-memory map.** It never re-reads `tickets.jsonl`, so a ticket appended by a
   CLI/MCP process is invisible to `find_ready()`. The reconcile tick (below) reloads from disk before scanning. CLI and
   MCP processes are short-lived and `load()` fresh, so they always see current disk state.

### Reconcile tick

A low-frequency background task in the orchestrator (default 30s, configurable) that, under the store lock, re-reads
`tickets.jsonl` to merge records appended by other processes into the in-memory map, then runs the existing ready-scan.
This is the only mechanism by which an externally-created-then-approved issue reaches dispatch. It is off the dispatch
hot path and additive to existing event-driven scan triggers. Append-only JSONL with last-record-wins per `_id` makes
the reload idempotent and consistent with the orchestrator's own in-flight (already-appended) writes.

## Interfaces

### Project resolution

Stores are per-project (`<project>/.jig/store/`), so the front doors must locate the project first. Both the CLI and
the standalone MCP **walk up from the current working directory** to the nearest ancestor containing a `.jig/`
directory (git-style), so they work from any subdirectory of a project — not only its root. A `--path` override (CLI)
forces a specific project root and skips discovery. If no `.jig/` is found in any ancestor, the command fails loudly
with a clear "not inside a jig project" message. This is a deliberate ergonomic departure from the existing
`--path`-defaults-to-cwd-root commands (`start`/`plan`/`sync`), which require the root.

### CLI — `jig issue <verb>`

| Command | Maps to | Notes |
|---|---|---|
| `create` | `create` | `--title`, `--type`, `--size` (default M); body via `--body` / `--body-file` / `-` (stdin) / `$EDITOR`. AC required in body. `--created-by` default `"cli"`. Prints assigned `jig-N`. |
| `list` | `list` | `--status`, `--type`, `--label`, `--assignee`. Prints `jig-N  status  type  title`. |
| `show <ref>` | `get` + comments | `ref` = `jig-N` or UUID. |
| `update <ref>` | `update` | `--status`, `--assignee`, `--title`, `--add-label` / `--remove-label`. |
| `approve <ref>` | `approve` | `PROPOSED → OPEN`. Operator action. |
| `close <ref>` | `close` | status → resolved/closed. |
| `comment <ref>` | `comment` | `--body` / stdin. |
| `link <ref>` | `link` | `--blocks` / `--blocked-by` / `--parent` (+ `--remove`). |

Validation failures exit non-zero with a clear message and write nothing.

### Standalone MCP tools

`issue_create`, `issue_list`, `issue_show`, `issue_update`, `issue_close`, `issue_comment`, `issue_link` — over
`IssueService`. **No `issue_approve`** — approval is operator-only. `created_by` carries the calling agent's name. The
server is a launchable stdio process (its own entrypoint), distinct from the in-process `create_agent_mcp_server`
factory.

### `jig-N` key

Format `jig-<N>`, `N` a positive integer monotonically increasing per project, never reused. Accepted anywhere a ticket
reference is taken by the CLI/MCP.

## Data model

- `Ticket` gains `key: str = ""` (the `jig-N` value), populated at create. Defaulted, not required, so pre-feature
  keyless records still validate on load; lazy-backfilled on first front-door access. Internal `id` (UUID4) unchanged.
- `TicketStatus` gains `PROPOSED = "proposed"`.
- Counter: a single integer in `.jig/store/issue_seq`, read-modify-written under `flock` on `.jig/store/.issue.lock`.
- No migration: existing `.jig/store/` data is regenerable; old tickets simply have no `key` until next touched, and the
  CLI/MCP can backfill on read if needed (decided in plan).

## Alternatives considered

### Reuse an existing status for the gate

Reusing `BLOCKED` or `NEEDS_INFO` to mean "awaiting approval" was rejected — both carry distinct existing semantics
(dependency-blocked; missing information) that the orchestrator and TUI already act on. Overloading them would make
"why isn't this dispatching?" ambiguous. A dedicated `PROPOSED` is unambiguous and costs one enum value plus a TUI
board column.

### Persist-only with no reload (as originally framed)

The original "picked up on next scan" framing assumed the orchestrator would see external appends. It will not — the
store is in-memory after `load()`. Pure persist-only would mean externally-created issues are *never* dispatched by a
live orchestrator (only after a full restart). Rejected as not meeting the success criteria.

### File-watch (inotify/watchdog) instead of a reconcile tick

A filesystem watcher on `tickets.jsonl` would give near-real-time pickup but adds a dependency, a watcher lifecycle,
and dedup-against-own-writes logic. The user chose scan-based pickup; a periodic reload is simpler and bounded, and the
approval gate already means there is a human in the loop, so sub-30s latency has no value here.

### `flock` only inside `IssueService` (not the shared create path)

Locking only the front-door path would leave a race between a front-door create and a concurrent orchestrator/agent
create (both touch the `jig-N` counter). Putting key assignment and its lock in the shared `TicketStore.create` path
closes that window for all writers at the cost of an `flock` acquire on the in-process hot path — negligible at
human-scale issue volume.

### Chosen

`IssueService` seam + CLI + stdio MCP, `PROPOSED` gate, `jig-N` assigned in the shared create path under `flock`, and a
reconcile tick that reloads before scanning. It delivers both front doors and the approval gate while confining new
risk to the create path and one background task.

## Risks

- **Shared create-path change.** Moving key assignment and adding `flock` to `TicketStore.create` touches the path the
  orchestrator and every agent already use. Mitigation: keep the `asyncio.Lock` semantics intact, add `flock` around
  the same section, cover with tests for both single-process and concurrent-process creates.
- **Reconcile reload consistency.** Re-reading the JSONL mid-run must not drop or duplicate in-memory state. Mitigation:
  reload under the store lock; rely on append-only + last-record-wins per `_id` (the orchestrator's own writes are
  already on disk, so a rebuild includes them).
- **New `PROPOSED` enum value.** ~89 `TicketStatus` references across ~20 files; the TUI board (`_BOARD_COLUMNS`) and any
  status→color map need `PROPOSED`. Most references are equality checks tolerant of a new value. Mitigation: audit
  exhaustive sites in the plan; add a board column + color.
- **JSONL append atomicity across processes.** Single-line `open("a")` appends are atomic on POSIX under `PIPE_BUF`, but
  performing the append inside the same `flock` critical section as the counter removes reliance on that assumption.
- **Counter file corruption / crash mid-create.** A crash between counter bump and record append could consume a key
  with no ticket (a gap), which is harmless (keys need not be contiguous). A crash before counter persist could reissue
  a key — prevented by persisting the counter before releasing the `flock`.

## Out of scope

- Git-committed or cross-repo-portable issues; storage stays runtime-only in `.jig/store/`.
- Full extraction of the per-agent MCP onto `IssueService`.
- Active push notification from CLI/MCP to a running daemon.
- Relaxing the AC / `work_type` invariants.
- Replacing the internal UUID id scheme.
- Allowing MCP (external) callers to approve issues.

## Open questions

- [ ] Backfill behaviour for pre-existing keyless tickets — assign a `jig-N` lazily on first front-door access, or leave
      them keyless and addressable only by UUID? (Lean: lazy backfill on access; settle in plan.)
- [ ] Reconcile interval default and whether it is surfaced as config vs constant. (Lean: 30s constant initially.)

## Change log

- 2026-06-08: Initial draft (brent)
- 2026-06-08: Tightened per review — persist counter before append (no key reissue); `PROPOSED → OPEN` enforced
  operator-only at the store layer (not just MCP tool omission); `Ticket.key` defaulted so legacy records still load
  (brent)
- 2026-06-08: Added git-style project-root discovery (walk up to nearest `.jig/`) shared by the CLI and standalone MCP,
  with a `--path` override; confirms per-project (not global) storage (brent)
