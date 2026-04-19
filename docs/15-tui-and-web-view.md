# 15 — TUI and Web View

The human interface to the harness. A terminal UI for devs and participating
roles; a read-only web view for broader team visibility. Agents don't use
either — they talk to the service directly via the sandbox protocol.

## Premise

The TUI is the primary dev surface. It's where devs see what the team is
doing, handle their claims and evaluations, read threads, respond to
proposals, spawn agents, and watch work happen. It's not an editor — it
shells out to the dev's editor for any substantial writing — but it is
where a dev spends most of their coordination time.

The web view is a read-only subset. Dashboard and Kanban views only, for
team members and stakeholders who want visibility without participation.
It's not a reduced TUI; it's a different surface for a different audience.

## Scope and non-goals

**TUI does:**
- Display project, team, and work-unit state in real time.
- Handle common actions (claim, accept, reject, post thread entries,
  spawn agents).
- Let devs chat with Claude for meta-questions and for delegated actions.
- Integrate with the dev's editor for writing.

**TUI does not:**
- Replace an editor. Specs, work-unit descriptions, long-form content
  open in `$EDITOR`.
- Replace the SCM. PR diffs, code review, merges happen on GitHub/GitLab.
- Try to be usable for agents. Agents talk to the service.
- Handle configuration. Project config, workflow definitions, role
  templates are files in the repo; edit them with your editor.

**Web view does:**
- Dashboard and Kanban, read-only.
- Team-wide status visibility.

**Web view does not:**
- Support participation (no claims, no posts, no edits).
- Reproduce every TUI view (ticket detail, chat are TUI-only).

## Framework

The TUI is built on [gridland](https://github.com/thoughtfulllc/gridland).
Framework-specific implementation details are out of scope for this
document; what follows is design in framework-agnostic terms. When
implementing, adapt to gridland's conventions.

## The four views

Four top-level views, rotated via tab navigation:

1. **Dashboard** — what you need to do and what's happening near you.
2. **Ticket** — work-unit-centric drill-in, split-pane (list + detail).
3. **Kanban** — workflow-centric flow, status categories across all
   workflows.
4. **Chat** — conversational interface to Claude and to specific role
   agents, with persistent per-dev history.

Each view has its own state, its own keybindings within the view, and
its own unread counter visible on the tab label when new activity
arrives.

## Dashboard view

The landing view. Answers "what do I need to do?"

Sections, from most to least urgent:

**Awaiting your action.**
- Claims pending for you (any_human or any_human_with_role phases
  where you're eligible).
- Objections and proposals where you're the designated resolver or
  owner.
- Handoffs awaiting your evaluation.
- Escalations routed to you.

**Your active work.**
- Work units you're currently on (as implementer, reviewer, whatever).
- Agent instances you spawned that are running.
- Threads you've posted in recently.

**Team activity (things you might want to know).**
- Recent merges.
- Recent accepted proposals (especially on artifacts you own).
- Work units moving through phases.
- Workflow completions.

**Team status summary.**
- Active work count by size and phase.
- Anything stuck past deadlock timeout (for awareness).
- Deployment state (last merge, last build).

Everything on the dashboard is role-adapted. A PO sees pending proposals
prominently; a dev sees claims. Both see their active work and team
activity. The data is derivable from the dev's identity (who they are,
which roles they fill, which work units they're engaged with).

Realtime updates arrive as new items appearing and counters incrementing
on the tab label. No notifications, no popups.

## Ticket view

Split-pane. Left: ticket list grouped by status. Right: detail of the
selected ticket.

**List pane.**
Tickets grouped by status category (Draft / Active / Waiting / Done /
Abandoned). Collapse/expand groups. Filter by: assignee, size, workflow,
age, work type, owner. Sort within groups by: updated-time, priority,
age.

**Detail pane.**
Sections in the detail pane for a selected ticket:

- Header: title, size, work type, workflow, current phase, parent/child
  links if applicable, PR link if applicable.
- Spec: read-only display of the ticket's spec (both human and
  structured if the ticket-level spec has both; otherwise just what
  exists). `e` on the spec opens it in `$EDITOR`.
- Thread: unified timeline of all thread entries. Entry-type badges.
  Filter-by-type available. Post-entry actions via keybindings (ask,
  answer, object, resolve, note, propose, etc.).
- Checkpoints: collapsed by default (they're operational noise for
  most viewing). Expand to see current phase's checkpoints, including
  recent deferred items.
- Phase history: compact timeline showing which phases have completed,
  who filled which roles, how long each took.
- Verification state: pending checks, completed checks with pass/fail,
  any waivers.

Editing actions that make sense in-TUI:
- Claim / unclaim.
- Accept / reject handoffs.
- Accept / reject proposals (for owners).
- Post thread entries.
- Spawn agent for a phase.
- Mark objections resolved / resolve questions.
- Waive a check (if authorized).

Editing actions that shell out to `$EDITOR`:
- Writing ticket description at creation.
- Writing / editing spec content (both human format Markdown and,
  for direct edits, structured YAML).
- Drafting long thread entries or proposal rationale.

The shell-out pattern: TUI writes a temp file with appropriate initial
content, launches `$EDITOR` on it, waits for the editor to exit, reads
the result, submits. If the user saves an empty file or aborts, the
action cancels.

### Split-pane navigation

Standard terminal split-pane conventions. `h`/`l` or left/right arrow
to switch focus between panes. `j`/`k` or up/down to move within the
focused pane. Enter to drill into a selection. Escape to back out.

## Kanban view

Status-category columns across all workflows. Normalizes different
workflows into a consistent board:

**Columns** (status categories):
- Draft
- Spec
- Build
- Review
- Validate
- Ready to Merge
- Done

Tickets using different workflows (hotfix vs standard vs parent-of-XL)
appear in appropriate columns based on their current phase's status
category. Workflow phase declarations carry a status-category mapping:

```yaml
phases:
  - name: agent-review
    status_category: review
  - name: qa-validation
    status_category: validate
```

Phases without an explicit category get a sensible default inferred
from the name.

**Card content.**
Each card shows:
- Ticket ID and title.
- Size badge.
- Work type icon.
- Current phase name (since multiple phases can share a status).
- Assignee or "unassigned."
- Time in current column (helps spot stuck work).

**Filters.**
Same filter mechanism as ticket view: assignee, size, workflow, age,
work type, owner. Filters persist across tabs — filtering on Kanban
applies to the Ticket view too.

**Interactions.**
- Click/enter on a card drills into ticket view for that ticket.
- No drag-to-change-status. Status is workflow-driven, not manually
  set.
- Cards update in place as tickets advance. New cards slide in when
  tickets are created.

The Kanban view is primarily a read/awareness tool. Actions happen by
drilling into ticket view.

## Chat view

Conversational interface. Two modes:

**Generic Claude.** Ask meta-questions, request summaries, have Claude
take actions on your behalf. "Summarize what's happening on WU-55."
"What specs reference the auth system?" "Create a work unit for
adding pagination, size M, work type feature."

**Role-specific agent interaction.** Direct your conversation at a
specific role's agent. Ask the PO helper for a draft spec expansion;
ask the SA helper about a technical tradeoff; ask a QA agent about
validation approach. The harness spawns (or reuses) an agent of the
specified role with full role context; you chat with it directly.

Switching modes happens via command (`/role po`, `/role sa`, `/role
qa`, `/generic`). The chat pane shows which mode is active.

**Persistent history.**
Per-dev. Your chat history persists across sessions. Compaction
matches the thread compaction pattern: when history grows past a
threshold, older entries roll into summaries while preserving recent
context. Same mechanism, smaller scope.

**Actions from chat.**
When you ask Claude to do something actionable ("create a work unit
for X"), the chat mode confirms the action with a small structured
summary before executing. "I'll create a work unit titled X, size M,
work type feature. Proceed?" Accept/reject. Executed actions are
logged in the chat history and trigger whatever the normal action
path would (spawning agents, updating state, notifying subscribers).

Chat mode never bypasses authorization. A dev chatting with Claude
can only do things they could do via direct action. Claude acting on
the dev's behalf uses the dev's authority; the audit trail records
the dev as the actor with "via chat" metadata.

**Cross-referencing.**
Chat can reference work units, specs, decisions, etc. "Look at WU-55"
resolves the reference and pulls relevant context into Claude's
context window. When Claude produces output that should become a
thread entry, a proposal, or a checkpoint, chat offers to post it
rather than just displaying it.

## Navigation and interaction model

**Tab navigation.** Cycle through the four views via a dedicated
keybinding (gridland convention). Shift+number to jump directly
(1=Dashboard, 2=Ticket, 3=Kanban, 4=Chat).

**Within-view navigation.** Vim-style: h/j/k/l for movement, gg/G for
top/bottom, / for search within the view.

**Global keybindings.**
- `?` — help overlay for the current view.
- `:` — command mode (execute harness commands by name).
- `/` — search within view.
- `Ctrl+/` — global search across everything.
- `q` — quit (confirm first).
- Tab / Shift+Tab — cycle views.

**Command mode.** Typing `:` opens a command input. Harness commands
(`claim WU-55`, `spawn-agent reviewer WU-55`, `refresh`) execute from
here. Tab-completion on command names and arguments.

**Editor integration.** `$EDITOR` is used for substantial writing. TUI
pauses while editor is running, resumes on exit. Content is read from
the saved file; empty or unchanged content cancels the action.

## Real-time updates

The service's WebSocket event channel drives the TUI. Updates arrive
continuously:

- New thread entries appear in open ticket views.
- Counters increment on affected tab labels.
- Dashboard items shift position as priority changes.
- Kanban cards move columns as phases transition.

**Prominence:**
- No popups, no sounds, no notifications.
- Badges on tab labels show unread counts since last visit to that
  tab.
- A subtle status bar at the bottom shows "Last update: 2s ago" so
  the user knows the stream is live.
- If the WS disconnects, the status bar changes to "Disconnected —
  reconnecting..." and updates pause.

**Connection resilience.**
The TUI is a WS client. It follows the replay-since-sequence pattern
from [12 — Service shape](./12-service-shape.md): on reconnect, it
asks the service for events since the last-seen sequence. No lost
events even across network blips.

## Role-based adaptation

The TUI adapts defaults based on roles the dev fills:

- **PO** sees proposals prominently on dashboard. Ticket view emphasizes
  spec sections. Kanban filter defaults to "tickets I own."
- **SA** sees technical proposals and decision records prominently.
  Ticket view emphasizes design and technical-risk sections.
- **Dev** sees claims and active implementation work. Ticket view
  emphasizes thread and current phase.
- **QA** sees validation-pending items. Ticket view emphasizes spec
  behaviors (which validations can be derived from).
- **Observer** (no participating role) sees team status primarily.
  Limited to what's informational.

Adaptation is defaults, not access control. Any authenticated dev can
navigate to any view; the adaptation is about what's prominent and
what's on the dashboard.

## Filters and persistence

Filters persist per dev, per view, across sessions. If a dev filters
Kanban to "size M, work type feature" and quits, the next session
shows that filter applied. A small indicator shows when filters are
active; clear-all is a single keybinding.

Filters apply across Ticket and Kanban views (same filter state). A
filter set on one applies to the other.

## Web view

Web view is a read-only subset intended for:

- Team members who aren't participating in day-to-day work but want
  visibility.
- Stakeholders checking status.
- Anyone who doesn't want to launch a terminal.

**What the web view shows:**

- Dashboard (same content as the TUI Dashboard, read-only).
- Kanban (same content as the TUI Kanban view, read-only).

**What the web view doesn't show:**

- Ticket detail pane with editing. (Drill-in is allowed but actions
  disabled.)
- Chat. (Chat is a participation surface.)
- Configuration or admin views.

**Authentication.**
Same auth backend as the TUI — see [14 — Service internals](./14-service-internals.md).
Views are role-adapted the same way.

**Real-time updates.**
Same WS event channel. Web view subscribes, displays updates as they
arrive. Matches TUI prominence (badges, no interruption).

**Deployment.**
Static assets served by the service. No separate web service to
deploy. The service's HTTP endpoint serves the web UI when accessed
with a browser.

## Interaction patterns

A few common workflows and how they feel:

**Dev claims a review phase.**
1. Dashboard shows "Available claims: 3" in Awaiting-action section.
2. Dev drills in, sees the three claimable items.
3. Picks one, presses `c` to claim. Confirmation appears briefly.
4. Ticket opens in Ticket view with the dev now as assignee.
5. Dev reads the thread, checks spec, reads the handoff.
6. Accepts (`a`) or rejects (`r`). If reject, prompted for objection
   text via editor.

**Dev spawns an implement agent.**
1. In Ticket view, ticket is in Spec-accepted state, ready to start
   implement phase.
2. Dev presses `s` to spawn; menu shows available agent templates for
   the implement role.
3. Dev picks dev agent template, confirms.
4. Agent spawns; thread shows spawn event; ticket moves to Build in
   Kanban.
5. Checkpoints accumulate as the agent works; visible in ticket detail
   when expanded.

**PO handles a proposal.**
1. Dashboard shows "Pending proposals: 2."
2. PO drills into first proposal. It's a refinement to a spec behavior.
3. PO reads the current spec section and the proposed change
   (rendered as a diff).
4. PO accepts (`a`), rejects (`r`, prompted for reason), or refines
   (`f`, prompted for feedback).
5. Next proposal.

**Dev asks Claude to set things up for a new feature.**
1. Chat view. Dev: "Create a work unit for adding labels to todos,
   size M, work type feature. Link to the labels capability in the
   project spec."
2. Claude confirms the action with structured summary.
3. Dev accepts.
4. Work unit created; appears in ticket list and Kanban.
5. Dev continues chat or switches to ticket view.

## What's deliberately not in v1

- **Richer editing in-TUI.** Any substantial writing shells out.
- **Custom views or user-defined dashboards.** Four views, fixed
  structure. If teams want custom queries, they can use the service
  API.
- **Notifications beyond badges.** No desktop notifications, no sounds,
  no pushing into other tools. The TUI is the surface.
- **Mobile web view.** Desktop web only for v1.
- **Advanced chat features** — memory, learned preferences, cross-
  session context sharing across devs. The chat is per-dev, persistent
  within dev, nothing fancier.
- **In-TUI diff rendering.** Shell out to `git diff` or similar for
  diff display.
- **Keyboard shortcut customization.** Shipped shortcuts only. If
  teams want customization, comes later.

## Accessibility

TUI accessibility depends on the terminal. High-contrast themes
(matches gridland conventions), screen-reader compatibility via the
terminal's accessibility features. Not a harness-level concern beyond
choosing a framework that supports it.

Web view follows standard web accessibility practices — semantic HTML,
keyboard navigation, screen-reader-friendly.

## Failure modes

**Service down.** TUI shows "Disconnected" in status bar. Read views
show stale data with clear "last updated" timestamp. Actions disabled.
Reconnects automatically.

**Slow network.** WS connection tolerates lag. TUI shows optimistic
updates for user-initiated actions and rolls back on failure.

**Editor crash during edit.** The in-progress edit is lost (matches
editor crash behavior generally). User retries.

**Chat agent unresponsive.** Chat pane shows "..." indicator with a
cancel option. Timeout after 60s with error message.
