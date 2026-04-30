# Ontology

Authoritative names for the moving parts of jig. Use these in code
comments, commit messages, design discussions, and bug reports so
we're talking about the same things.

This doc is living — add terms as concepts firm up. Sections below
start with the TUI (the most-touched surface today); daemon /
orchestrator / agent terms get added as we firm them up.

## Visual zones

```
┌────────────────────────────────────────────────────┐
│ ░░░░░░░░░░░░░░░░ FRAME ░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ ░ Now  Tickets  Spec  Events ░░░░░░░░░░░░░░░░░░░ │   ← TAB STRIP (part of FRAME)
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ ░ ╭──────── PANE ──────────────────────────────╮ ░ │
│ ░ │                                            │ ░ │
│ ░ │       ░░░░░░░ DISPLAY ░░░░░░░               │ ░ │   ← read zone
│ ░ │       (RichLog / ListView / Tree …)         │ ░ │
│ ░ │                                            │ ░ │
│ ░ │   ┃░░░ COMPOSER (Now only) ░░░░░░░░░░       │ ░ │   ← type zone
│ ░ │   ┃                                         │ ░ │
│ ░ ╰────────────────────────────────────────────╯ ░ │
│ ░░░░░░░░░░░░░░░░░ FOOTER ░░░░░░░░░░░░░░░░░░░░░░░ │   ← project + daemon state
└────────────────────────────────────────────────────┘
```

| Zone | Color (current) | What it is | Where defined |
|---|---|---|---|
| **Frame** | `#000000` | Chrome around the whole TUI — Screen, TabbedContent, TabPane, Tabs strip. Reads as "outside the active interaction." | `Screen`, `TabbedContent`, `TabPane`, `Tabs` rules in `jig/tui/app.tcss` |
| **Pane** | `#1a2030` shell + `$accent` border | One bordered card per tab (Now / Tickets / Spec / Events). The container the operator interacts with. | `NowScreen, TicketsScreen, SpecScreen, EventsScreen` in `app.tcss` |
| **Display** | `#1a2030` | Read-only area inside a pane — scrollback in Now, list in Tickets, tree in Spec, tail in Events. Darker so the operator's eye scans it as "context." | `RichLog`, `ListView`, `Tree` in `app.tcss` |
| **Composer** | `#3d4862` + accent left-border | Input field at the bottom of Now where the operator types. Lighter so attention lands here. Also any future inline forms (e.g., new-ticket modal — though those are modals, not in-pane composers, so they may differ). | `TextArea`, `Input` in `app.tcss` |
| **Footer** | `$panel` | One-line strip docked to the bottom of the screen showing project name + git branch (left) and daemon state (right). | `JigFooter` widget in `jig/tui/widgets/footer.py` |

## Structural pieces

| Term | What it is |
|---|---|
| **App** | The Textual `App` subclass — `JigApp` in `jig/tui/app.py`. The process the operator runs when they type `jig`. |
| **Daemon** | The background `jig daemon serve` process that hosts the orchestrator + WebSocket server. Outlives any TUI session. The TUI is a client to it. |
| **Daemon Client** | TUI-side WebSocket client — `DaemonClient` in `jig/tui/daemon_client.py`. Manages connect / subscribe / send / reconnect. |
| **Pane** | One of the four tab views: Now, Tickets, Spec, Events. Each is a `Container` subclass (not a `Screen` — those are pushed/popped). |
| **Modal** | A `ModalScreen` pushed on top of the pane stack — Help (`?`/F1), `NewTicketModal`, `EditTicketModal`, `RawYamlModal`, `BriefModal`, `EventDetailModal`. Dismissed with Escape. |
| **Slash command** | A `/word args…` invocation typed in the Composer. Local commands (`/help`, `/quit`) handled in NowScreen; everything else dispatches to the daemon via the typed `command` envelope. |
| **Slash popup** | The list that appears above the Composer when input starts with `/`. Filters as you type. |
| **Concierge** | Free-text input (no leading `/`) routes to the concierge agent — a read-only LLM helper that answers questions or recommends slash commands. |

## Wire protocol terms

| Term | What it is |
|---|---|
| **Subscribe** | Client → daemon: `{"type": "subscribe", "topics": [...]}`. The daemon replies with one snapshot per topic and then streams typed events for them. |
| **Snapshot** | Daemon → client: `{"type": "snapshot", "topic": "...", "data": ...}`. Sent once per topic on subscribe. The client replaces its state from this. |
| **Event** | Daemon → client: `{"type": "event", "topic": "...", "kind": "...", "data": ...}`. Streamed as state changes. |
| **Topic** | One of: `tickets`, `spec`, `agents`, `events`, `prompts`, `threads`. The TUI subscribes to all but `threads` (which is per-ticket fetch). |
| **Command** | Client → daemon: `{"type": "command", "name": "...", "args": {...}}`. Triggers a handler from `jig.tui.commands` registry. |
| **Result** | Daemon → client: `{"type": "result", "ok": bool, "data"|"error": ...}`. Reply to a command. Each command gets exactly one. |
| **Prompt request** | Daemon → client: an `event` on the `prompts` topic with `kind: "request"`. Carries a `prompt_id` (UUID) and a `prompt_type` (e.g. `brief_approval`, `question_answer`). The TUI renders inline + flips Composer into "answering mode." |
| **Prompt reply** | Client → daemon: a `command` named `prompt_reply` with `args=[prompt_id, reply_text]`. Resolves the daemon's awaiting future. |
| **Answering mode** | NowScreen state when `_active_prompt_id is not None`. The next Composer submit is routed as a `prompt_reply` rather than as a slash dispatch or concierge query. |

## Daemon-side terms

| Term | What it is |
|---|---|
| **Orchestrator** | The async coordinator that owns ticket dispatch, agent lifecycle, bus routing. One per daemon. |
| **Configured / unconfigured** | Whether the orchestrator has loaded a project (i.e. `.jig/config.yaml` exists). Unconfigured mode boots the WS server but no stores or dispatch loops; `/init` promotes it via `Orchestrator.reload()`. |
| **Bus** | The append-only `Message` stream backing inter-agent communication (`jig/store/bus.py`). |
| **Stores** | `TicketStore`, `ThreadStore`, `MemoryStore`, `MessageBus` — JSONL-backed per-project state under `.jig/store/`. |
| **Spawn context** | `AgentSpawnContext` — the bag of stores + project + role config passed to `run_agent` when spawning a Claude Code subprocess for a ticket. |

## Spec terms

| Term | What it is |
|---|---|
| **Capability** | One unit of product surface — "users can post a job." Has an id, title, summary, optional user story, behaviors, AC, non-goals, open questions. |
| **Behavior** | A specific operation under a capability — "set a due date." Each behavior has an id and at least one AC. |
| **Acceptance Criteria** (**AC**) | One-sentence, testable statement of what "done" means for a behavior — "Natural language inputs 'today', 'tomorrow', and 'next week' are accepted and stored as resolved dates." Referenced by id (`[set-due-date]`) so tests can cite the AC they cover. |
| **Non-goal** | Something explicitly out of scope. Has an id + rationale. Operator-owned. |
| **User story** | Optional `As X, I want Y, so that Z` framing on a capability. |
| **Persona** | An actor who uses the product — customer / merchant / maintainer / etc. (See `docs/multi-level-spec/`.) |
| **Journey** | A narrative walkthrough of one persona's path through the product. (See `docs/multi-level-spec/`.) |
| **Module** | A grouping of capabilities. Organizational, not architectural. (See `docs/multi-level-spec/`.) |

## Composer affordances

| Term | What it is |
|---|---|
| **Slash popup** | List of slash commands above the Composer when input begins with `/`. Filters as you type. |
| **Inline suggestion** | Ghost-text completion (Input only — TextArea has no equivalent). |
| **Answering mode** | Composer state when a `prompt_request` is awaiting; next submit becomes a `prompt_reply`. |
| **History recall** | Up/Down arrows cycle previous submissions when cursor is at the first/last line of the Composer. |
| **Clipboard image paste** | `Ctrl+I` reads a PNG from the OS clipboard, saves it to `<project>/.jig/uploads/jig-clip-<UTC>.png`, inserts `[image: <abs path>]` at the cursor. macOS via `osascript`; Linux via `wl-paste` / `xclip`. |
| **Thinking indicator** | A Static line above the Composer showing `⠹ <role> is thinking… (Ns)` while a daemon-side agent is running. Driven by `agent_thinking{role, elapsed, active}` events. |

## Things that are NOT in this ontology yet

- TBD as concepts firm up.

## Change log

- 2026-04-30: Initial draft (brent + claude). Captures Frame / Pane / Display / Composer / Footer + protocol terms.
