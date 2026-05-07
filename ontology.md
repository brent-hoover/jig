# Ontology

Authoritative names for the moving parts of jig. Use these in code comments, commit messages, design discussions, and
bug reports so we're talking about the same things.

This doc is living — add terms as concepts firm up. Sections below start with the TUI (the most-touched surface today);
daemon / orchestrator / agent terms get added as we firm them up.

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

(This document — `ontology.md` at the repo root — is **jig's own** vocabulary: terms used across jig's design, code, and
prompts. Each user project also gets its own `.jig/spec/ontology.md` capturing the *operator's* domain vocabulary as it
emerges during PO discovery. See "Project ontology" below.)

| Term | What it is |
|---|---|
| **Capability** | One unit of product surface — "users can post a job." Has an id, title, summary, optional user story, behaviors, AC, non-goals, open questions. |
| **Behavior** | A specific operation under a capability — "set a due date." Each behavior has an id and at least one AC. |
| **Acceptance Criteria** (**AC**) | One-sentence, testable statement of what "done" means. Two flavors: **behavior AC** (PO-authored, user-visible — "Natural language inputs 'today', 'tomorrow', and 'next week' are accepted and stored as resolved dates"); **integration AC** (SA-authored, system-visible — "Writes to the `orders` collection in the main db, indexed on `user_id`"). Referenced by id (`[set-due-date]`) so tests can cite the AC they cover. |
| **Non-goal** | Something explicitly out of scope. Has an id + rationale. Operator-owned. |
| **User story** | Optional `As X, I want Y, so that Z` framing on a capability. |
| **Persona** | An actor who uses the product — customer / merchant / maintainer / etc. (See `docs/v2.0/multi-level-spec/`.) |
| **Journey** | A narrative walkthrough of one persona's path through the product. (See `docs/v2.0/multi-level-spec/`.) |
| **Suite** | A grouping of related capabilities. Operator/PO-owned, organizational. The L2 unit in the multi-level spec. "The catalog suite." (See `docs/v2.0/multi-level-spec/`.) |
| **Module** | An implementation unit — service / package / deployment boundary. SA-owned, architectural. A suite's capabilities may be implemented across multiple modules; one module may implement parts of multiple suites. "The ingest-worker module." |
| **Contract** | An SA-authored constraint at an integration boundary — schemas, API shapes, message envelopes, ownership of collections. Lives in `modules/<m>/contracts.yaml`. The thing code review enforces. |

## PM / Build-plan terms

| Term | What it is |
|---|---|
| **Planner PM** | Strategic PM agent. Runs in passes after SA-done. Reads PO + SA artifacts, decomposes capabilities into tickets, produces the build plan. Senior or SA tier. (See `docs/v2.0/pm-workflow/`.) |
| **Coordinator PM** | Tactical PM agent. Runs continuously. Dispatches tickets per the plan, routes dev escalations, tracks stalled work. Standard tier; mostly deterministic with thin LLM judgment. |
| **Build plan** | Living artifact at `.jig/plan/build-plan.yaml`. Organizes work into epics × completeness layers (bones / MVP / final). Owned by Planner PM, updated as work surfaces gaps. |
| **Epic** | A unit of the build plan — typically a module's worth of work, or a coherent capability cluster. Each epic has its own bones / MVP / final progression. |
| **Bones** | Build-plan layer 1: the union of all epic tracer bullets. System walking skeleton — every module touched, every contract exercised, single happy path, no edge cases. Bones of *all* epics complete before any epic's MVP begins. |
| **MVP** | Build-plan layer 2: per-epic minimum useful functionality. Real integrations, critical failure modes handled. |
| **Final** | Build-plan layer 3: per-epic full coverage. Edge cases, polish, performance, the long tail of AC. |
| **Tracer bullet** | A ticket type. Deliberately cross-module, deliberately incomplete in scope (one happy path, no error handling). Output is "data flows end-to-end, contracts compose." Distinct from a spike (spikes answer architectural unknowns; tracer bullets validate that *our own pieces* fit together). |
| **Spike** | A ticket type. Bounded exploration to mitigate a flagged architectural risk. Output is a learning (a comment with findings), not production code. Time-boxed. |
| **Standard ticket** | A ticket type. Single-capability, full AC, conventional implementation. Most tickets are these. |
| **Dev tier** | One of `standard | senior | sa`. Determines which agent (model + budget + context cap) implements the ticket. Assigned by Planner PM. |
| **Reviewer set** | The subset of federated reviewer agents that runs on a given ticket's PR. Default-on subset: contract-compliance, cross-cutting-policy, spec-compliance. Add-ons selected per ticket characteristics. |
| **Reviewer federation** | Multiple specialized reviewer agents running in parallel per PR (rather than one monolithic reviewer). Each has its own tight focus, narrow context, and tier. |
| **Severity** | One of `critical | important | notable`. Critical must be fixed; important should be unless rework needed (then consult SA); notable can be deferred. |
| **DEFERRED queue** | `.jig/plan/deferred.jsonl` — notable-severity items pushed forward from review for later triage. Owned by Coordinator PM; revisited by Planner PM at re-plan time. |

## Visual Design terms

| Term | What it is |
|---|---|
| **Visual Designer (VD)** | Agent role parallel to PO / SA / PM. Owns visual artifacts AND frontend architecture (stack, build, component pattern). The architect for the frontend, not just the visual designer. Runs in parallel with SA after PO discovery completes. (See `docs/v2.0/visual-design/`.) |
| **Frontend architecture** | VD-owned technical decisions for the UI: stack (HTMX + Alpine + custom CSS by default), build tooling, component pattern, accessibility target. Lives in `.jig/design/frontend.yaml`. SA owns backend architecture; VD owns frontend. |
| **Wireframe** | Grey-styled HTML describing one screen's structural layout — semantic elements, region markers, affordance labels, no real styling beyond the shared `wireframe.css`. Lives in `.jig/design/wireframes/<screen-id>.html` with a sidecar `<screen-id>.notes.md` for behaviors / states / cross-references. The HTML wireframe is also the starting code for the bones-layer implementation — additive transition, not throw-away. |
| **Screen** | One operator-facing surface in the product. Derived from L1 journeys + L3 capabilities. Each screen gets one wireframe; the union covers every user-visible journey step. |
| **Screen roster** | `.jig/design/wireframes/screens.yaml` — the canonical list of screens with mappings to journey ids, capability ids, suite. The visual equivalent of L1's capability roster. |
| **Design system** | Tokens (color / typography / spacing / radius), component spec, brand guidance. Lives in `.jig/design/system/`. Always present — VD applies defaults at the moment discovery starts; operator can replace via Anthropic-provided design tooling, supply their own export, or keep defaults indefinitely. `default` is a permanent valid state, not a placeholder. |
| **Design tokens** | Named primitive values (`color.primary`, `spacing.md`, `font.base`) referenced by implementation. The lowest-level unit of the design system. |
| **Visual compliance** | Reviewer agent type in the PM federation. Vision-based diff between an implementation screenshot and the wireframe (plus design-system check at MVP/Final layers). |
| **Browser index** | Auto-generated `.jig/design/wireframes/index.html` listing each per-screen wireframe HTML via `<iframe>` embed with state-toggle controls. Operator opens locally to review wireframe set or compare against implementation screenshots. |
| **wireframe.css** | Single project-level stylesheet that grey-box styles every wireframe. Generated once at VD discovery start; agents don't edit it. Bones-layer implementation removes this stylesheet and links the design system's real CSS — same HTML structure, real visual. |
| **wireframe-HTML linter** | Deterministic Python module enforcing the constrained wireframe vocabulary — allowed elements, no inline `style=`, no real color hexes, required `data-wireframe-region` on layout containers, etc. Runs on every wireframe save and as part of the per-commit reviewer cadence on `.jig/design/wireframes/*.html`. |

## Project ontology

| Term | What it is |
|---|---|
| **Project ontology** | Per-project file at `.jig/spec/ontology.md` capturing the *operator's* domain vocabulary — the terms used by people who actually use the product. Distinct from this file (jig's own vocabulary). Captured by L1 PO during journey walks; read by every subsequent agent (PO continuing, SA, VD, PM, dev) so terminology stays consistent across the project's artifacts and code. Operator can edit directly. |
| **Domain term** | One entry in the project ontology. The operator's word for a concept ("blocker," "standup," "shopping cart"), with a short definition derived from how the operator used it, plus a back-reference to the journey where it was first surfaced. |

## Synthetic operator simulator

| Term | What it is |
|---|---|
| **Synthetic operator** | Agent role (`synthetic-operator`) that drives jig's daemon end-to-end as if a human operator were using it. Used by the simulator to validate v2 workflow design choices through scripted scenarios. Lightweight (small model, narrow tools, persona-driven). See `docs/v2.0/synthetic-operator/`. |
| **Scenario** | YAML script describing a project shape + persona + sequence of operator turns + assertions. Lives in `docs/v2.0/synthetic-operator/scenarios/{smoke,full,nightly}/`. Played end-to-end by the simulator driver against a fresh isolated daemon. |
| **Persona** (simulator) | A behavior profile loaded as the synthetic-operator agent's system prompt. Initial 5: methodical, fast-and-shippy, scope-creeper, ambivalent, hostile. Each has structured response patterns + gate-confirmation policy + override probability + avoid-behaviors. |
| **Realism budget** | Discipline tracking real-operator behaviors the simulator wouldn't have produced. Logged via `/realism log` slash command; periodically reviewed; gaps trigger persona-library extensions or new scenarios. Realism-divergence metric tracks simulator-vs-reality drift over time. |
| **Coverage tag** | Free-form string on a scenario naming the workflow path it exercises (`po-l1-five-phase-conversation`, `sa-cascade-confirmed-impossible`, etc.). Aggregate coverage report identifies workflow paths that no scenario tests. |
| **Simulator mode** (analytics) | When the EventEmitter runs with `JIG_SIMULATOR=true` (or `simulator_mode=True`), every emitted event carries `simulator: true`. Consumer queries filter to `simulator=False` by default; simulator events live in their own logical corpus so they don't pollute real-project analytics. |

## URI scheme

| Term | What it is |
|---|---|
| **`project://` URI** | Stable address for any artifact or sub-element in a jig project. Multi-authority: `project://spec/...` (PO output), `project://arch/...` (SA contracts), `project://design/...` (VD wireframes + system), `project://plan/...` (PM build plan), `project://store/...` (runtime state). See `docs/v2.0/uri-scheme/`. |
| **Authority** (URI) | The first segment after `project://` — names the namespace. One of `spec` / `arch` / `design` / `plan` / `store`. Each has its own per-authority resolver. |
| **Path-style fragment** | `#a/b/c` after a URI; resolves as nested-key lookup into the YAML/JSON content. New for v2 (sub-contract anchoring). Example: `project://arch/modules/catalog-ingest/contracts#owns/products/write_access`. Distinct from anchor-style fragments (`#single-id`) which match `{#anchor}` definitions in markdown sources. |
| **Revision pin** (URI) | `@revision:N` between path and fragment. Resolves to the historical version of the artifact at that revision via the artifact's `change_log`. Used in cascade audit trails, ticket `implements_against` references, reviewer comments — anywhere a stable reference to a specific revision matters. |

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
