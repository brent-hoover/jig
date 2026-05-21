---
title: Human Project Management — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-20
updated: 2026-05-20
---

# Human Project Management — Problem Statement

## Context

Jig was conceived as lightweight project management software that agents can also use — not purely as
an agent orchestrator. The core data model (tickets, statuses, comments, parent/child hierarchy) is
well-suited to this dual-use vision. Agents create and advance tickets via MCP tools; the TUI provides
a read-only view of the current state. But the human-facing PM experience has significant gaps: a human
cannot meaningfully manage a project through jig without agent intermediaries for most operations.

This matters because real projects involve a mix of human decisions and agent execution. A human should
be able to create a ticket, group tickets into epics, annotate a ticket mid-run, see a board view of
project state, and declare a dependency between tickets — all without triggering an agent.

## Problem

Jig is not usable as a standalone PM tool for humans today. The specific gaps:

1. **No manual ticket creation**: Tickets are created by the PM agent via MCP tools. A human wanting
   to add a ticket must run the PM agent or edit JSONL files directly. There is no CLI command or TUI
   action for creating a ticket.

2. **No epics**: There is no way to group a set of tickets under a thematic unit (an "epic") that
   represents a feature or capability area. The parent/child hierarchy from PM planning is close, but
   parent tickets are themselves implementation tickets, not organizing containers. Epics are persistent
   groupings that survive ticket completion and help humans understand what has been shipped.

3. **No board view**: The TUI shows tickets as a list. There is no kanban-style column layout showing
   where everything is — backlog → in progress → review → done. The board view is the most immediately
   readable representation of project state for a human.

4. **No ticket dependencies**: Two sibling tickets with a sequencing dependency ("ticket B cannot start
   until ticket A is merged") have no way to express that relationship. The parent/child model handles
   decomposition but not cross-ticket ordering constraints.

5. **No human comments**: The comment store exists for agents to record findings, questions, and
   decisions. There is no CLI/TUI path for a human to annotate a ticket directly — e.g. adding context,
   a decision, or a note mid-run.

## Simplest possible solution

CLI commands for each gap:
- `jig ticket create <title>` — creates a ticket in backlog state
- `jig ticket comment <id> <text>` — adds a human comment to a ticket
- `jig epic create <title>` / `jig epic add <epic-id> <ticket-id>` — creates epics and assigns tickets
- `jig ticket block <id> --on <other-id>` — declares a dependency
- Board view as a new TUI screen toggled by a key

No new data stores — epics and dependencies can be expressed as fields on the existing ticket JSONL
records.

## Complications considered

- **Scale**: N/A — ticket and epic counts are bounded by project size; no performance concerns.

- **Concurrency**: Agent and human may modify the same ticket simultaneously (human adds a comment
  while an agent is advancing the ticket). The JSONL store is append-only so comment records don't
  conflict. Status changes could conflict — the design should not require locking but should document
  the expected behavior (last write wins on status, comments are additive).

- **Failure modes**: N/A — CLI commands fail loud if the ticket or epic ID doesn't exist. No silent
  failures needed.

- **Cross-cutting policies**: N/A — no PII, auth, or secrets involved.

- **Agent compatibility**: Epics and dependency fields added to ticket records must not break existing
  agent MCP tools that read ticket data. New fields should be optional with safe defaults so agents
  that don't know about epics continue to work correctly.

- **Sprints / time-boxing**: Explicitly deferred. Epics (thematic grouping) cover the organizing use
  case without requiring calendar concepts. Sprint-style time-boxing can be added later if the human PM
  use case demands it.

## Constraints

- No new external services or dependencies.
- New ticket fields (epic membership, dependencies) must be backward-compatible with existing agent
  MCP tools and JSONL store format.
- The board view must work within the existing Textual TUI architecture.

## Requirements

- A human can create a ticket from the CLI without running any agent.
- A human can add a comment to any ticket from the CLI.
- Epics exist as a grouping construct; tickets can be assigned to an epic; the TUI shows epic
  membership.
- Tickets can declare `blocks` / `blocked_by` relationships; the TUI and board view reflect blocked
  state visually.
- A board view exists in the TUI showing tickets grouped by status in columns.

## Non-goals

- Sprints / time-boxed iterations (deferred).
- Human assignment of tickets to specific people or agents (out of scope for this iteration).
- Due dates or estimates.
- Integration with external PM tools (GitHub Issues, Linear, Jira).
- Bulk operations (mass status change, bulk epic assignment).

## Success criteria

- An operator can set up a new project, create tickets manually, assign them to an epic, declare
  dependencies between them, and view the board — all without triggering any agent.
- Agents continue to create and advance tickets exactly as they do today; epic and dependency fields
  are ignored by agents that don't use them.
- The board view makes the state of a 10–20 ticket project immediately readable in the TUI.

## Open questions

- [ ] Should epics be a new record type in their own JSONL file, or a field on existing ticket records
      (e.g. a "container" ticket type)? A separate record type is cleaner but adds a new store; a
      ticket field reuses existing infrastructure.
- [ ] How does the board view handle tickets with no explicit status column mapping (e.g. tickets in
      jig-specific states like NEEDS_INFO or FAILED)? They need a column or a visual indicator.
- [ ] Should `jig ticket create` go through the orchestrator bus (so TUI updates in real time) or
      write directly to the JSONL store?

## Change log

- 2026-05-20: Initial draft (brent)
