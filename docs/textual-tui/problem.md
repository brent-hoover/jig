---
title: Textual TUI — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-04-28
updated: 2026-04-28
---

# Textual TUI — Problem Statement

## Context

Today jig is split across two languages and two toolchains: Python core (orchestrator, agents, MCP, stores, init flow) plus a Bun + Gridland JSX TUI (`tui/`) that connects to the orchestrator over a WebSocket. To use jig you `uv sync` *and* `cd tui && bun install`. The TUI is panel-based — kanban view, ticket list, ticket detail, agent panel, event log, answer form, new-ticket form — all rebuilt as React-style components with custom Gridland primitives.

The recent project-spec-schema work added rich-rendered terminal output to the `jig init` CLI flow: spawn rules, question panels, dim spinners, brief-preview rendering. That work demonstrated that a conversation-driven shape (scrolling transcript with structured prompts inline) is a much better fit for jig's actual interaction model than panel-based monitoring.

## Problem

Three things are wrong:

1. **Two languages, two ecosystems, two ways to drift.** Pydantic models in the orchestrator have to be reimplemented as TypeScript types in the TUI. When the schema changes, both sides do work. Type safety isn't shared. Tests don't share infrastructure. Releases require both `uv sync` and `bun install` to be wired correctly.

2. **The panel model fights the workload.** jig is conversational at its core — operator answers PO questions, accepts SA proposals, picks templates, reviews briefs. A scrolling transcript with structured prompts is the natural shape; kanban panels and pinned regions are over-engineered for that. The recent rich-based init UX confirmed the conversational model works well.

3. **No always-on operator helper.** The current TUI is reactive — it shows what's happening, lets you click things, but provides no LLM-backed assistance for the operator. "What's the status?", "Why is this ticket stuck?", "Create a ticket for due-dates" — the operator has to figure out the right command or panel to use.

## Constraints

- jig is a single-user tool today; no multi-tenant concerns.
- Background work matters: agents may run for minutes-to-hours. The TUI must be able to detach (close laptop, exit terminal session) without killing in-flight work.
- Scripting / CI access must remain possible — the recently added `Setup log: jig story brief --path dogfood` line is a real use case.
- Python 3.12+, async-first throughout. The orchestrator uses asyncio extensively.
- The WebSocket layer (`jig/ws_server.py`) already exists and works; this work extends it, doesn't replace it.

## Requirements

- Single language for the entire system (Python).
- Single launch command (`jig`) that gives the operator everything they need.
- Conversational interaction model: a "Now" view that interleaves agent activity, structured prompts, and operator input as a scrolling transcript.
- Tabbed navigation to other views (Tickets, Spec, Events) without leaving the TUI process.
- Background work: orchestrator runs as a daemon; TUI is a client that can connect/disconnect freely.
- Scripting escape hatch: `jig --print "<command>"` runs a single slash command and exits, returning text/JSON.
- Always-on LLM helper: free-text input that isn't a slash command flows through a "concierge" agent that translates intent to action OR answers conversationally.
- All Pydantic models from the orchestrator are usable in the TUI directly — no wire-format reimplementation.

## Non-goals

- Multi-tenant / remote operator support. The protocol can support it later; v1 doesn't.
- Web view of the same daemon. Same protocol, different client — defer.
- Auth / transport security beyond a local Unix socket.
- Vim-mode key bindings, theme customization beyond Textual's defaults.
- A scripting story richer than `--print` (e.g., a Python SDK for jig).
- Notification surfaces (system tray, sound) when an agent needs an answer.
- Free-text concierge dispatch for *every* operation — v1 limits to a curated set of safe-to-execute actions.

## Success criteria

- Running `jig` (no args) launches the Textual TUI and connects to (or starts) the orchestrator daemon.
- Closing the TUI doesn't stop the orchestrator; reopening picks up where state left off.
- The full `jig init <name>` flow plays out inside the TUI's Now screen — questions, brief approval, spec generation, branch choice, SA proposal, scaffold confirm — using rich-rendered widgets that mirror the recent CLI polish.
- The operator can switch tabs to view tickets, spec, or events at any time without interrupting in-flight work.
- Free-text input ("create a ticket for the due-dates capability") is parsed by the concierge agent, proposed as a structured action, and executed on confirmation.
- `jig --print "/spec list-capabilities"` (or similar) produces machine-readable output for scripts.
- The Bun TUI (`tui/`, `bun.lock`, `package.json`) is deleted from the repo.

## Open questions

(All resolved during brainstorming — see design doc.)

## Change log

- 2026-04-28: Initial draft (brent)
