---
title: Textual TUI — Design
type: design
status: draft
owner: brent
created: 2026-04-28
updated: 2026-04-28
problem: ./problem.md
---

# Textual TUI — Design

## Summary

Replace the Bun + Gridland JSX TUI (`tui/`) with a Python Textual app (`jig/tui/`) that is the only operator-facing entry point. `jig` (no args) launches the TUI; the orchestrator runs as a separate background daemon (`jig daemon`); the two communicate over the existing WebSocket. The TUI has four tabbed screens (Now, Tickets, Spec, Events). Now is the conversation surface — scrolling transcript with structured prompts, slash commands, and a free-text "concierge" LLM agent for help and intent dispatch. CI / scripting use `jig --print "/<command>"` to run a single slash command and exit.

## Approach

**Process model.** Two long-lived processes: the daemon (orchestrator + agents + WebSocket server) and the TUI client (Textual app). The daemon outlives any single TUI session — agents can finish work while the operator's terminal is closed. The TUI is a thin view + input layer that owns no state.

```
┌─────────────────────────┐         ┌─────────────────────────┐
│  jig daemon             │◄──────►│  jig TUI (client)       │
│  orchestrator + agents  │  WS    │  Textual app            │
│  port 9100, persistent  │         │  one or many at a time  │
└─────────────────────────┘         └─────────────────────────┘
            │
            ▼
   ┌───────────────────┐
   │  .jig/ stores     │
   │  tickets, threads │
   │  bus, spec        │
   └───────────────────┘
```

**TUI shell.** Single Textual `App` with four screens. Top-tab navigation, persistent footer with daemon status + active-agent count + key hints. `Tab` cycles screens; `1`-`4` direct-jumps; `?` opens contextual help; `q` quits the TUI (daemon survives).

```
┌───────────────────────────────────────────────────────────────────┐
│  jig  ·  [Now]   Tickets   Spec   Events                          │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│                  ( current screen body )                          │
│                                                                   │
├───────────────────────────────────────────────────────────────────┤
│  daemon: connected  ·  agents: 0 active  ·  ?: help               │
└───────────────────────────────────────────────────────────────────┘
```

All four screens stay mounted; switching tabs toggles visibility. Each screen subscribes to its WebSocket topics on mount and stays subscribed (state stays current even when not visible). Screens own their own key bindings, which the footer renders dynamically.

**Now screen.** The default landing and the most novel piece. Three regions: scrollback (rich-rendered transcript), live region (Textual reactive widget that animates the "thinking" spinner), input (single-line at the bottom).

```
┌───────────────────────────────────────────────────────────────────┐
│  Now                                                              │
├───────────────────────────────────────────────────────────────────┤
│   ─── PO ──────────────────────────────  10:42:11               │
│   ╭─ PO asks ──────────────────────────╮                         │
│   │  What is this product?             │                         │
│   ╰────────────────────────────────────╯                         │
│   › A simple todo manager.                                       │
│                                                                   │
│   ─── PO ──────────────────────────────  10:43:02               │
│   ⠹ thinking… (12s)                                              │
├───────────────────────────────────────────────────────────────────┤
│   › _                                                             │
└───────────────────────────────────────────────────────────────────┘
```

Input is always available. The dispatcher routes:

```
operator input
   │
   ▼
starts with "/"?
   ├─ yes → parse as slash command
   │         ├─ valid → execute, append to scrollback
   │         └─ invalid → render error inline, keep input
   └─ no  → send to concierge agent
              ├─ agent maps to known action → propose, confirm, execute
              └─ agent answers conversationally → render reply inline
```

Modes are emergent, not explicit:
- **Idle**: no agent active; input goes to slash parser or concierge.
- **Active question**: a Question targeted at `any_human` is unresolved on the current ticket; the question Panel renders; the next operator input is interpreted as the Answer (posted via the existing answer flow).
- **Observation**: orchestrator is dispatching agents; spawn rules and spinner animate in the scrollback. Operator can still issue slash commands or concierge queries.

The rich-based init UX (rules, panels, dim spinners) translates 1:1 to Textual widgets via `RichLog` and custom widgets that wrap `rich.console.Console.print`. The brief-approval rendering, question panels, and gap reports we built recently all reuse here without rework.

**Concierge agent.** New role at `jig/defaults/roles/concierge.yaml`. Spawned per free-text input that doesn't match a slash command. Read-only by default; write actions go through a confirmation step.

Tools the concierge has:
- Read: `spec_list_capabilities`, `spec_get_capability`, `spec_list_non_goals`, `spec_resolve_uri`, `list_tickets`, `read_ticket`, `read_comments`, plus a new `recent_events(limit, kind=?)` daemon-side query that returns the last N bus messages from the in-memory ring buffer (or `messages.jsonl` tail if older than the buffer).
- Dispatch: `run_slash_command(name, args)` — executes any slash command via the same daemon path the TUI uses, with confirmation gating for write operations.

Cost note: every free-text input triggers one Claude API call. Slash commands skip the agent entirely — operators learn `/` for fast paths and free-text for "I don't remember the command." A simple per-minute rate cap (e.g. 10 concierge calls / minute) prevents accidental runaway cost.

**Tickets / Spec / Events.** Three read-mostly screens, master/detail layout for the first two, scrolling log for the third. Built incrementally as use exposes what matters; v1 ships a basic shape per screen and grows from there.

## Interfaces

### Slash command surface (v1 minimum)

```
/init <name>                     start an init flow inline in Now
/ticket new                      open inline ticket-creation form
/ticket update <id> <field>=<v>  update a ticket field
/answer                          explicit answer mode (when ambiguous)
/cancel                          cancel current agent / interaction
/spec                            render the brief preview inline
/spec capabilities [state=?]     list capabilities, optional state filter
/spec capability <id>            render one capability inline
/status                          orchestrator + agents summary
/events [--filter k] [--since t] tail recent events inline
/help                            open help overlay (modal)
/quit                            quit TUI (alias `q`)
```

Each slash command corresponds to one daemon-side handler (in `jig/tui/commands/`) and one `--print`-mode invocation. Adding a slash command is a deliberate design choice; it's the explicit fast path.

### `--print` mode

```
jig --print "/spec capabilities state=planned"
jig --print "/ticket update brief assignee=po"
jig --print "/status"
```

Same Pydantic input/output as the slash command, just emitted as text/JSON instead of rendered to a Textual widget. CI / scripting uses this.

### Wire protocol (TUI ↔ daemon)

Two message kinds from daemon to client:

```json
{"type": "snapshot", "topic": "tickets", "data": [...]}
{"type": "event", "topic": "tickets", "kind": "updated", "data": {...}}
```

Topics: `tickets`, `threads`, `agents`, `spec`, `events`. TUI subscribes on connect; daemon sends a snapshot followed by live events.

Client to daemon:

```json
{"type": "command", "name": "init", "args": {"name": "dogfood"}}
{"type": "command", "name": "concierge", "args": {"input": "create a ticket for due-dates"}}
```

Daemon executes, replies with `{type: "result", ...}` or streams `{type: "event", ...}` as the action progresses (concierge in particular streams text + tool calls + actions back).

### Daemon lifecycle

```
jig daemon start        # background fork; writes PID + socket addr to .jig/run/
jig daemon stop         # PID file → kill
jig daemon status       # alive? port? uptime? agents in flight?
```

`jig` (no args) auto-starts the daemon if not running, then connects.

## Data model

The TUI uses the existing Pydantic models directly: `Ticket`, `ThreadEntry`, `Capability`, `StructuredSpec`, `Behavior`, `NonGoal`, `Message`. No reimplementation, no wire-format translation. Pydantic serializes to JSON on the wire and back to Python objects on receipt.

Each Textual screen owns a small reactive state model:

```python
class TicketsScreenState(BaseModel):
    tickets: dict[str, Ticket] = {}
    selected_id: str | None = None
    layout: Literal["list", "board"] = "list"
    filter: TicketFilter = TicketFilter.ALL


class NowScreenState(BaseModel):
    scrollback: list[ScrollbackEntry] = []
    live_status: LiveStatus | None = None  # spinner / agent activity
    active_question: Question | None = None
    input_buffer: str = ""
```

Snapshots replace the model wholesale. Events mutate slices. Textual's `reactive` system re-renders affected widgets automatically.

### Connection lifecycle

```
Connecting   → footer: "connecting to daemon…"
Connected    → footer: green; normal operation
Reconnecting → footer: yellow; state frozen; input queued or rejected
Disconnected → footer: red; /connect to retry, q to exit
```

Reconnect re-fetches all snapshots; queued actions are dropped; operator re-issues if still wanted. Simple semantics.

## Alternatives considered

### Big-bang replacement

Build the full Textual TUI as a parallel module; cut over in one PR after feature parity. **Rejected** — multiple weeks of work shipping nothing usable; high risk of feature gaps surfacing only at cutover. Sole-user context makes phased migration cheap; we should use it.

### Phased migration with `--legacy` deprecation flag

Ship Textual TUI in stages alongside the Bun TUI; deprecate `--legacy` over time. **Rejected** — sole user, no external deprecation timeline matters. Keep both available during build, delete Bun when new TUI is functional. No `--legacy` flag needed.

### Slash commands only, no concierge agent

Skip the always-on LLM helper; operators learn the slash command surface. Simpler, zero LLM cost on the input path. **Rejected** — operator-side intent translation and "ask jig anything" are explicit goals (per Q5 and Q7 in brainstorming). The concierge isn't a bolted-on feature; it's part of the foundational UX.

### Single-process TUI (no daemon)

`jig` runs orchestrator + TUI in the same process; exit kills everything. Simpler architecture; no PID file management; no Unix socket. **Rejected** — background work is core (per Q6). Agents run for minutes-to-hours; operator must be able to close the TUI without killing in-flight work. The daemon model already exists in skeleton form (`jig start` + `ws_server`); we extend it, not invent it.

### Free-text intent only, no slash commands

Every input goes through the concierge; slash commands are unnecessary noise. **Rejected** — slash commands are the fast, predictable, zero-cost path for known operations. Free-text is for exploration and "I forgot the command." Both have value; the hybrid is what the operator actually needs.

### Chosen: Textual TUI + concierge agent + daemon, port-as-needed

Picks the boundaries that match jig's actual interaction model (conversational, not panel-based), respects background work (daemon), avoids language-toolchain split (single Python ecosystem), and preserves scripting (`--print`). The hybrid input model gives operators both fast slash commands and an always-on LLM helper. Port-as-needed lets the new TUI ship early without blocking on full Bun feature parity.

## Risks

- **Textual + asyncio integration.** Textual runs its own event loop. The daemon and concierge agents use asyncio. We need a clean boundary — likely the TUI is asyncio-native (Textual supports this) and runs WebSocket reads as async tasks. Spike this in Phase 1 to verify before committing.
- **Slash command surface drift.** Easy to grow `/<command>` proliferation without curation. Mitigation: every slash command corresponds to a single daemon-side handler in `jig/tui/commands/`; adding one is a deliberate design decision, reviewed.
- **Concierge cost.** Free-text input → API call → can run agents. Cost is real. Mitigation: per-minute rate cap (default 10/min); concierge result includes cost in the inline render so the operator sees what they spent.
- **Bun TUI's missing features at cutover.** Some kanban filtering, keyboard shortcuts, or modal flows may not exist in the new TUI when we delete the old one. Mitigation: dogfood the new TUI for several days before deletion; add anything missing as use exposes it.
- **Daemon discovery on first run.** `.jig/run/` may not exist; `jig daemon start` may fail (port already in use, permissions). Mitigation: clear error messages with recovery steps; `jig daemon status` always works.

## Out of scope

- Multi-tenant or remote daemon support. Protocol can support it later; v1 doesn't.
- Web view of the same daemon. Same protocol, different client — defer.
- Auth / transport security beyond a local Unix socket.
- Vim-mode key bindings, theme customization beyond Textual's defaults.
- A scripting story richer than `--print` (e.g., a Python SDK for jig).
- Notification surfaces (system tray, sound) when an agent needs an answer.
- Concierge free-text dispatch for *every* operation — v1 limits to a curated set of safe-to-execute actions.

## Open questions

(None — all resolved during brainstorming.)

## Implementation phases

Five phases. Phase 1 lays groundwork; Phase 2 ships the new TUI shell; Phase 3 makes the conversational surface real with init + concierge; Phase 4 fleshes out the other three screens; Phase 5 deletes the Bun TUI.

### Phase 1 — Daemon refactor

- `jig daemon start | stop | status` subcommands. Background-fork via `os.fork()` or a small `daemonize` helper; PID + socket address in `.jig/run/`.
- `ws_server` extended with snapshot messages on subscribe (per topic) and typed event messages (vs. raw bus dump). Existing Bun TUI keeps working — additive change to the protocol.
- Spike Textual + asyncio integration in a throwaway script to verify the event-loop boundary.

### Phase 2 — TUI shell + Now (idle / observation modes)

- New module `jig/tui/` with the Textual `JigApp`, four `Screen` subclasses (Now, Tickets, Spec, Events), persistent `Footer` with daemon status, top tab bar.
- Now functional in v1: idle prompt, slash command parser (`/help`, `/status`, `/quit`), scrollback, daemon status.
- Tickets / Spec / Events render placeholders.
- `jig` (no args) launches the new TUI; existing Bun TUI still launchable via `cd tui && bun run src/main.tsx` (no flag wiring needed since the user invokes Bun directly).

### Phase 3 — Now active mode + concierge agent

- Question panels, answer flow, brief-approval flow, init flow (`/init <name>`) all play out inline in Now's scrollback. The rich-based work from this session translates to Textual widgets.
- `jig/defaults/roles/concierge.yaml` added. Concierge spawned per free-text input via the daemon; output streamed back to Now.
- Read-only tool surface first; write actions added with confirmation flow.
- `--print` mode wired up: `jig --print "/<command>"` runs a single command non-interactively.

### Phase 4 — Tickets, Spec, Events screens

- Tickets first (highest-traffic): list/board toggle, filter, new/update actions, master/detail layout.
- Spec second: capability list grouped by state + non-goals, drill-in detail pane, modals for raw YAML view and rendered brief view.
- Events third: scrolling log, filter by kind, follow toggle, modal for full event payload.
- Snapshot + event subscriptions wire each screen's reactive state to the daemon stream.

### Phase 5 — Cutover

- Dogfood the new TUI for a few days against the existing dogfood project.
- When stable: delete `tui/` directory, `bun.lock`, `package.json`. Add `textual>=0.80` to `pyproject.toml` (the existing `websockets` dep already covers the wire transport). Update `docs/` to drop the `cd tui && bun install` instructions.
- The `Setup log` line now reads `jig --print "/spec brief --path dogfood"` (or similar — exact text per implementation).

## Change log

- 2026-04-28: Initial draft (brent)
