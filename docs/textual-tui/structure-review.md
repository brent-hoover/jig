---
title: Textual TUI — Structure Review
type: notes
status: draft
owner: brent
created: 2026-05-10
updated: 2026-05-10
design: ./design.md
plan: ./plan.md
---

# Textual TUI — Structure Review

## Summary

The Textual TUI has a reasonable top-level package split:

- `app.py` owns the shell and global key bindings.
- `screens/` owns pane-specific UI.
- `widgets/` owns reusable chrome.
- `daemon_client.py` owns websocket transport.
- `commands/` owns daemon-side handlers for slash commands.

That structure is a good starting point, but the internal boundaries are still too informal. `JigApp` has become a central switchboard for layout, daemon lifecycle, websocket subscription, message fan-out, sidebar updates, modal command dispatch, hotkey gating, and clipboard/file side effects. Several screens also render raw daemon payloads directly into Rich markup. This works while the surface area is small, but it will get harder to maintain as more panes, events, and prompt types are added.

The main goal of the next cleanup should be to make the TUI a set of thin views over normalized state, with a small routing layer between daemon messages and screen methods.

## Recommended Changes

### 1. Extract daemon message routing from `JigApp`

`JigApp._handle_daemon_message` currently knows every topic, message shape, screen, sidebar subzone, and special case. This is the biggest structural pressure point.

Create a focused routing module, for example `jig/tui/event_router.py`, responsible for:

- Normalizing daemon envelopes.
- Applying topic/kind dispatch.
- Calling screen/sidebar update methods.
- Keeping routing behavior testable without booting the full Textual app.

`JigApp` should only wire the client to the router:

```python
self.router = TuiEventRouter(app=self)
...
on_message=self.router.handle
```

This also gives one place to handle malformed frames, ignored topics, and future protocol compatibility.

### 2. Normalize daemon payloads before screens see them

Screens currently accept heterogeneous dicts and compensate locally. Tickets and sidebar code both handle ticket data that may be bare or wrapped under `ticket`. Events code looks for kind in `payload.kind`, but live websocket event envelopes use `kind` and `data` at the top level.

Add a boundary layer that converts daemon messages into stable view events:

- `TicketSnapshot`
- `TicketChanged`
- `SpecSnapshot`
- `AgentEvent`
- `PromptRequest`
- `LifecycleEvent`

These can be dataclasses, Pydantic models, or plain normalized dicts. The important constraint is that screens should not need to know transport envelope quirks.

### 3. Keep modal commands structured

The composer needs slash parsing, but modal forms already collect structured data. Today `NewTicketModal` returns a dict, `JigApp.action_new_ticket` converts it to slash-style flags, and `cmd_ticket` parses those flags back into structured data.

Prefer structured command arguments for UI-originated actions:

```python
await self.client.send_command(
    "ticket",
    {"subcommand": "new", "title": title, "work_type": work_type, "size": size},
)
```

Then keep slash parsing isolated to `slash.py` and the Now composer. If backwards compatibility is needed, command handlers can temporarily accept both `{"args": [...]}` and structured payloads.

### 4. Add shared rendering helpers for escaped markup

Many widgets use `markup=True` and interpolate daemon/project strings directly into Rich markup. This makes display data able to affect styling. It also means malformed brackets in titles, descriptions, prompts, or spec text can produce broken rendering.

Add a helper module, for example `jig/tui/rendering.py`:

```python
from rich.markup import escape

def esc(value: object) -> str:
    return escape("" if value is None else str(value))
```

Use it for all untrusted or project-authored text while preserving deliberate style tags around it:

```python
f"[bold]{esc(ticket['title'])}[/bold]"
```

Primary places to update:

- Ticket list/detail titles and descriptions.
- Spec tree labels and detail text.
- Prompt panel questions and options.
- Sidebar queue, needs-you, activity detail, and tail subject text.
- Event rows and detail labels.

### 5. Fix ANSI-vs-Rich render stream contract

`console_stream.py` emits ANSI-rendered text from `rich.Console(force_terminal=True)`, but `NowScreen.handle_daemon_event` writes `agent_render` content directly to a `RichLog(markup=True)`. That causes ANSI escape sequences to render literally.

Pick one contract and enforce it:

- If `agent_render` carries ANSI, consume it with `rich.text.Text.from_ansi(content)`.
- If `agent_render` carries Rich markup, stop forcing terminal ANSI in `console_stream.py`.

The current producer comments say the payload is ANSI, so the likely fix is in `NowScreen`.

Add a regression test with actual ANSI content, not just plain text:

```python
content = "\x1b[1mbold text\x1b[0m"
```

Assert that the scrollback contains `bold text` without literal `\x1b`.

### 6. Feed live events into the Events screen

The daemon emits live lifecycle events on the `events` topic. `JigApp` currently appends those to the sidebar but does not update `EventsScreen`, so the Events pane can become stale after the initial snapshot.

Add an `EventsScreen.handle_event(kind, data)` or `append_event(event)` method and route live `events` topic messages to it. Normalize the shape so snapshot rows and live rows are rendered by the same code path.

### 7. Separate screen state from screen rendering

`TicketsScreen`, `SpecScreen`, `EventsScreen`, and `Sidebar` all combine state mutation, sorting/filtering, and markup construction. Extract small pure helpers for view data:

- `ticket_rows(tickets) -> list[TicketRow]`
- `ticket_detail(ticket) -> str`
- `spec_tree_model(spec) -> SpecTreeModel`
- `event_rows(events, filter) -> list[EventRow]`
- `sidebar_model(state) -> SidebarModel`

This makes it possible to test behavior without driving Textual widgets, and leaves the Textual classes focused on mounting/updating widgets.

### 8. Avoid broad `except Exception` swallowing in core paths

Some UI lookups can reasonably fail during mount/unmount, but broad exception swallowing also hides real rendering and protocol bugs.

Keep narrow guards around optional widgets, but let unexpected errors surface in:

- Event routing.
- Snapshot handling.
- Command dispatch from modal submissions.
- Renderer helpers.

If a failure should not crash the TUI, log it with enough context: topic, kind, target screen, and payload keys.

### 9. Clarify ownership of daemon lifecycle

`JigApp` currently auto-starts the daemon when the project appears initialized, while `DaemonClient` retries forever when no address file exists. This is a pragmatic operator experience, but the lifecycle policy is split between app and client.

Consider a small `DaemonSupervisor` or `DaemonConnectionManager` that owns:

- Autostart decision.
- Status polling.
- Address resolution.
- Retry state.
- User-visible connection notifications.

`DaemonClient` can then remain a plain websocket transport.

### 10. Add focused regression tests

Recommended test additions:

- `agent_render` ANSI is rendered correctly and escape sequences are not visible.
- Live `events` topic messages append to `EventsScreen`.
- Markup-like ticket titles render literally.
- Markup-like spec capability titles render literally.
- New-ticket modal dispatch can use structured command args.
- Router normalizes bare and wrapped ticket event payloads identically.

## Suggested Implementation Order

1. Fix concrete behavioral bugs first: ANSI render handling and live Events pane updates.
2. Add `rendering.esc()` and cover the highest-risk markup surfaces.
3. Introduce an event router while preserving existing screen method names.
4. Normalize ticket/events payloads in the router.
5. Move modal command dispatch to structured payloads.
6. Extract pure view helpers from the largest screens as follow-up cleanup.

This order keeps the changes reviewable: first user-visible bugs, then safety, then architecture.
