---
title: Humans as first-class participants alongside agents
type: problem
status: draft
owner: brent
created: 2026-05-30
updated: 2026-05-30
---

# Problem: humans in the loop, not just watching it

## Summary

Today jig is a cockpit a single operator *watches*: agents do the work, the operator answers questions and
manually closes tickets. We want the operator to *direct from* that screen and to be able to take a seat in the
work itself — picking up a ticket, getting it reviewed by the agent reviewer federation, and acting on review
feedback — while still running the agent fleet. The human should be a first-class participant in the same
workflow agents already use, not a special case bolted on the side. This is the epic tracked in
[#116](https://github.com/brent-hoover/jig/issues/116).

## Context

What exists today (verified in the codebase, 2026-05-30):

- **Ticket model** (`jig/ticket.py`): 8-state `TicketStatus` (`open`, `in_progress`, `blocked`, `needs_info`,
  `failed`, `merge_conflict`, `resolved`, `closed`); an `assignee: str | None` field that exists but is
  **unused**; grouping fields already present (`parent_id`, `epic_id`, `suite_id`, `module_id`, `labels`,
  `blocked_by`/`blocks`). No `rank`/explicit ordering field.
- **Dispatch** (`jig/orchestrator.py`, `jig/store/tickets.py`): fully automatic. `find_ready()` picks `open`
  top-level tickets whose deps are `resolved` and spawns agents per workflow phase. No assignee routing.
- **Closing**: no auto-close. Tickets land in `resolved`/`failed`/`blocked`; the operator closes manually via
  `update_ticket(status="closed")`. There is no "validator" role with exclusive close authority.
- **TUI** (`jig/tui/`): a read-mostly Textual UI. A **Kanban `BoardView` already exists** (status columns,
  status icons) but has no drag and no human actions. Talks to the backend over a WebSocket (`ws_server.py`).
- **Review federation** (`jig/orchestrator.py`, `jig/reviewers/`, `reviewer_mcp.py`, `finding_ack_mcp.py`):
  multiple agent reviewers run in parallel at the review phase; findings post to a `ReviewCommentsStore` with a
  `critical`/`important`/`notable` severity model that gates the ticket (`failed`/`blocked`/`resolved`). **No
  human touchpoint.**
- **Human input today**: agents `thread_ask` (target can be `any_human`); the operator answers in the `/now`
  TUI pane, which drives the `answer_questions` WS command and resumes the ticket. This is the only existing
  human-in-the-loop path, and it is *pull* (agent asks) not *direct* (human acts).
- **No HTTP/web surface.** CRUD is MCP-tool-only (agent-shaped) plus the TUI form.

## Why now / why it matters

jig's distinctive bet is the **manager-of-a-fleet seat** — one person directing many agents. That seat is
currently passive: you watch and unblock. The leverage is in making it active: the same person can prioritize,
assign, gate quality, and personally take the tickets agents can't, all on one board. Without this, jig stays a
"watch the agents go" demo rather than a tool you actually run a project from. It also future-proofs the model
for a small team without committing to building team UX now.

## Goals

A solution must achieve:

- **One type-agnostic actor model.** An actor's *type* (human / agent) is orthogonal to its *role* (worker,
  reviewer, validator). "Agent reviews human's branch", "human reviews agent's branch", "human works a ticket"
  are the same code paths over a type-agnostic actor — humans are not special-cased in the workflow.
- **Assignment to humans or agents**, with the human able to field a ticket themselves.
- **Human work is orchestrated like agent work**: when a ticket is assigned to a human, jig still scaffolds the
  worktree/branch and routes the result into the *same* review federation an agent's work goes through. jig owns
  the lifecycle; the human just writes the code on their own time.
- **Priority, grouping, and explicit ordering** on the board (item 3/4 of #116). Concretely: add a `rank`
  ordering field; reuse existing `epic_id`/`labels` for grouping/priority rather than new enums.
- **Full ticket CRUD + reordering available to a human** across surfaces.
- **A configurable validator with exclusive close authority** (item 7): only the actor holding the validator
  role for a ticket may move it to `closed`.
- **Humans in the review flow** (items 8/9): a human can be added as an optional reviewer in the federation
  (findings post into the same store/severity model), and a human can be the validator who signs off.
- **Surface parity.** CLI, TUI, and a new Web board are all first-class clients of one surface-agnostic action
  layer. The TUI must remain first-class — the web board is an additional surface, not a replacement.

## Non-goals

- **No authentication / multi-tenant security.** This is a single-user / trusted-team tool. "Who am I" is
  *selected* (env var / flag / picker), not authenticated.
- **No symmetric assignment.** Agents never assign tickets to the human. Work flows down (human → agent,
  human → human) and never up into the human's queue. An agent that thinks the human should act may *surface* a
  request (the existing question/comment path) but cannot create an assignment. Only the human assigns.
- **No full team UX yet.** The roster may contain a single entry. Multi-person boards, notifications to other
  people, and team governance beyond "validator can close" are deferred.
- **Not replacing JIRA/Linear.** We are not building a general-purpose issue tracker; we are making jig's
  existing agent workflow include a human seat.
- **No drag-and-drop in the TUI.** Terminal drag is out; TUI reordering is key-driven. Drag belongs to the web
  board.

## Constraints

- Build on the existing JSONL stores, message bus, and WebSocket relay — no SQL, no external services
  (per project conventions).
- The action layer must sit over the existing WS (port 19100) plus a small HTTP layer for the web client; it
  must not fork backend logic per surface.
- Respect the sandbox model: agents run in bwrap/Docker; the orchestrator (and human-facing surfaces) run
  outside it.
- `assignee` should become a **namespaced actor reference** (`agent:dev`, `human:brent`) so dispatch can route
  on type without special-casing.
- No migration scripts — jig has no production deployments; existing `.jig/store` data is regenerable.

## Open questions

- **Actor identity shape.** Thin roster (`.jig/humans.yaml`: handle, display name, allowed roles) vs. deriving
  from git identity vs. a single generic "operator". Leaning toward a thin roster that may hold one entry, with
  git identity seeding the default. (To be settled in the actor-model sub-project design.)
- **How "only the human assigns" coexists with automatic dispatch.** Proposal: today's auto-dispatch is the
  human's *standing delegation* — agents self-dispatch only from the pool the human has left agent-assignable;
  assigning to `human:*` parks the ticket in a "waiting on human" state and pushes it to the human's inbox.
  Needs validation.
- **Validator close-gate mechanics.** Does the validator role attach per-ticket, per-workflow, or per-project?
  How does it interact with the federation's existing `resolved` disposition?
- **Human inbox.** Generalize the existing `/now` Q&A pane into a push inbox (assigned work + review requests +
  questions), or keep questions separate from assigned work?
- **Web board scope for v1.** Minimum viable: render columns + cards + assign + reorder + ready-for-review +
  review inbox. What's cuttable?

## Success criteria

- A human can be assigned a ticket from any surface (CLI/TUI/Web); jig scaffolds a worktree/branch for it and
  parks it in a "waiting on human" state in the human's inbox.
- When the human marks that ticket ready, it runs through the **same** agent review federation an agent ticket
  does, and review findings surface back to the human.
- A human can be added as a reviewer on a ticket and post findings that gate it identically to an agent
  reviewer's findings.
- A configured validator (and only that actor) can move a ticket to `closed`; non-validators are refused.
- Tickets can be reordered (rank), grouped, and prioritized, and the ordering is reflected consistently across
  CLI, TUI, and the web board.
- The TUI retains every capability it has today plus the new human actions — it is not downgraded by the web
  board's existence.

## Proposed decomposition (sub-projects)

Dependency-ordered; foundation is (1) and (2). Each becomes its own `problem → design → plan` cycle.

1. **Actor model** — namespaced actor refs (`agent:dev` / `human:brent`), thin roster, trust-based current-actor
   selection. Makes `assignee` meaningful and type-aware. *Foundation.*
2. **Surface-agnostic action layer** — one backend module (assign, reassign, set-priority/rank, reorder, claim,
   ready-for-review, submit-review, validate/close) over the existing WS + a small HTTP layer; CLI/TUI/Web are
   thin clients. *Foundation.*
3. **Dispatch routing for human tickets** — `human:*` assignee parks in "waiting on human" + emits to the inbox;
   `agent:*` keeps current behavior.
4. **Ticket model: ordering** — add `rank`; wire priority/grouping to existing `epic_id`/`labels`.
5. **Human inbox** — generalize the `/now` pane into a push inbox (assigned work, review requests, questions).
6. **Humans in the review flow** — optional human federation reviewers + configurable validator with exclusive
   close authority.
7. **Web board** — draggable Kanban, ticket CRUD, inbox; a client of (2). Builds last, on stable foundations.
