---
title: EDGE Audit — direct engine-internal access in CLI/TUI
type: reference
status: draft
owner: brent-hoover
created: 2026-06-26
updated: 2026-06-26
---

# EDGE Audit — direct engine-internal access in CLI/TUI

Epic 9 (EDGE) task 2: audit `jig/cli.py` and `jig/tui/` for direct access to engine internals — the imports
that must be retired by routing through the daemon API contract (`jig/edge/api.py`). This is the input to the
Epic 9 **MVP** ("Route all CLI/TUI access through the daemon API — no direct store or engine imports").

A violation = the edge importing an engine, store, orchestrator, agent, MCP, or workflow module directly,
instead of sending a `Command` / reading a `Snapshot` / reacting to an `Event`.

## `jig/cli.py`

| Module imported | Category | Why it's a violation |
|---|---|---|
| `jig.orchestrator` (`Orchestrator`) | engine | Edge drives the Build engine directly instead of a `StartProject`/command. |
| `jig.store.tickets`, `jig.store.threads`, `jig.store`, `jig.store.quality`, `jig.store.audit` | store | Edge reads/writes the JSONL stores directly instead of rendering a `Snapshot`. |
| `jig.ticket`, `jig.ticket_events` | domain/engine | Edge constructs domain entities + emits ticket events directly. |
| `jig.persistence` | store | Direct persistence access. |
| `jig.po_ontology_mcp`, `jig.issues.mcp` | MCP | Edge calls MCP tool handlers directly. |
| `jig.onboard_workflow` | engine | Edge runs an authoring workflow directly. |
| `jig.pm.cycle_view`, `jig.pm.calibration`, `jig.pm.overrides` | engine | Direct PM engine access. |
| `jig.graph.derive`, `jig.cascade_viewer`, `jig.canonicalize`, `jig.catalog`, `jig.section_locks`, `jig.quartermaster`, `jig.profile_loader`, `jig.spec_loader` | engine/store | Direct engine/spec internals for rendering + mutation. |

**Edge-appropriate (keep):** `jig.daemon`, `jig.ws_server`, `jig.config`, `jig.logging_setup`, `jig.safe_path`,
`jig.container`, `jig.dev_env.*`, `jig.tui.app`, `jig.story` (rendering), `jig.issues.cli`, `jig.sim.cli`.

## `jig/tui/`

| Module imported | Category | Why it's a violation |
|---|---|---|
| `jig.agent` | **agent** | The TUI imports the real Claude agent stack — the most egregious leak. |
| `jig.runtime` | runtime | Direct Agent Runtime access. |
| `jig.init_workflow` | engine | Edge runs the init/authoring workflow directly. |
| `jig.ticket_mcp`, `jig.po_l1`/`po_l*` | MCP | Direct MCP tool-handler calls. |
| `jig.ticket`, `jig.thread`, `jig.schemas.po` | domain | Edge depends on engine domain types instead of DTOs. |
| `jig.store.tickets`, `jig.persistence` | store | Direct store/persistence access. |
| `jig.prompt_registry`, `jig.init_prompts`, `jig.project` | engine | Direct engine config/prompt internals. |

**Edge-appropriate (keep):** `jig.tui.*` (internal: commands/screens/widgets/slash/daemon_client/console_stream/
clipboard), `jig.events`, `jig.daemon`.

## Summary

- The edge reaches across **every** layer: stores (`jig.store.*`, `jig.persistence`), the Build engine
  (`jig.orchestrator`), authoring workflows (`jig.init_workflow`, `jig.onboard_workflow`), the PM engine
  (`jig.pm.*`), MCP handlers (`jig.*_mcp`), and even the **real agent** (`jig.agent`, from the TUI).
- The new `jig/edge/api.py` contract is the boundary all of these collapse behind. The MVP work is mechanical
  but broad: replace each direct call with a `DaemonApi` command/snapshot/event, then enforce the boundary
  with the `tests/edge/test_purity.py` invariant extended to the whole `jig/edge/` package.
- `jig/edge/` itself imports **no** engine module today (proven by `test_edge_imports_only_itself`),
  so the boundary is clean from the start — the migration moves the edge code *into* `jig/edge/` behind the
  contract rather than the reverse.
