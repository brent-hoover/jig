# Jig Requirements

## Overview

Jig is an agent harness that orchestrates multiple Claude Code agents across a codebase using the `claude_code_sdk` (Python). It coordinates agents through a workflow engine, communicates via a file-backed message bus exposed as a local MCP server, and provides a terminal UI via Gridland.

## Architecture

- Two-process split: Jig Core (Python async) + Jig TUI (TypeScript/Bun via Gridland)
- Async orchestrator with file-backed message bus (asyncio queues + JSONL persistence)
- Agents spawned via `claude_code_sdk` as async tasks
- TUI connects to core via WebSocket for real-time updates
- Gridland renders in terminal and browser (future web dashboard)

## Projects

1. Projects map 1:1 to a local git repo
2. Project config stored in `.jig/config.yaml`

## Issues

1. You can have agents working on multiple issues across multiple projects (v2 — v1 is single issue/single project)
2. Issues have a design doc (markdown) and an implementation plan (YAML)
3. Issues track status, current workflow phase, and base branch

## Agents

1. Orchestrate multiple Claude Code agents across a codebase
2. Agent types defined in YAML config: spec, test, dev, review, plus custom types
3. Each agent type has a system prompt, allowed/denied tool lists, and default context sources
4. Agents receive relevant context injection — files, globs, issue artifacts, prior agent outputs
5. Custom tools provided via a Jig MCP server (publish_message, report_completion, request_context)
6. Agent types are simple for v1, with granular restrictions deferred

## Workflows

1. Workflow defined as a DAG of phases (v1: linear spec -> test -> implement -> review)
2. Each phase declares agent type, entry/exit criteria
3. Hybrid orchestrator: deterministic phase transitions with discretion within phases
4. Orchestrator is Python code (not a Claude agent) — handles workflow logic, agent lifecycle, git ops
5. Orchestrator handles all git operations — agents have no git access
6. Agents communicate via structured message passing over a file-backed message bus
7. Message bus exposed as a local MCP server per agent session
8. Task schema: id, description, acceptance_criteria, agent_type, input_context
9. Completion state schema: success, needs_info, blocked, failed — each with reason

## Message Bus

1. File-backed pub/sub: asyncio queues for routing, JSONL files as source of truth
2. Topic-based routing scoped to issues
3. Message schema: id, from, to, type, payload, timestamp, correlation_id
4. Exposed to agents as MCP server tools
5. Publishes to WebSocket for TUI event stream
6. Full message log replayed on crash recovery

## Workspace & Git

1. One git worktree per active agent, created by orchestrator
2. Agents work in isolated worktrees — no access to each other's workspaces
3. Sequential commits per phase form clean history
4. Worktrees cleaned up on validation (not on completion)
5. On workflow completion: merge to target branch or open PR (configurable)

## Security

1. Must run in Docker, with optional docker-compose for dependencies
2. Each agent runs in a bubblewrap container for isolation from other agents
3. Bubblewrap restricts filesystem to agent's worktree only
4. No git access for agents — orchestrator only
5. Tool access restricted per agent type

## TUI & CLI

1. Gridland TUI (TypeScript/Bun) connects via WebSocket for real-time updates
2. Displays: workflow phase, active agent, streaming output, message log, user input prompts
3. Compiles to standalone binary via `bun build --compile`
4. CLI commands: jig init, jig start, jig status, jig stop, jig validate

## State Persistence

1. All state lives under `.jig/` in the project root (file-based, no database)
2. Crash recovery: replay JSONL message log, resume from last completed phase
3. In-memory state (queues, process handles) reconstructed from files on restart

## v1 Constraints

- Single issue at a time, single project
- Linear workflow only (no parallel agents)
- File-based persistence
- Minimal TUI
- Simple agent types
- No web dashboard
