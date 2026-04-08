# Jig: Agent Harness Design Spec

## Overview

Jig is an agent harness that orchestrates multiple Claude Code agents across a codebase. It uses the `claude_code_sdk` (Python) to spawn and manage agents, coordinates them through a workflow engine, and provides a terminal UI via Gridland.

**v1 scope:** Single issue, single project, linear workflow. One agent active at a time.

## Architecture

Two-process split:

1. **Jig Core** (Python, async) — orchestrator, agent lifecycle, message bus, state persistence, MCP server, WebSocket server
2. **Jig TUI** (TypeScript/Bun via Gridland) — real-time display of workflow status, agent output, message log. Connects to core via WebSocket. Also renders in browser for future web dashboard.

## Domain Model

### Project

- 1:1 with a local git repo
- Fields: repo path, default branch, active issues
- State: `.jig/config.yaml`

### Issue

- A unit of work within a project
- Fields: ID, title, status (`pending`, `in_progress`, `completed`, `failed`), current phase, base branch
- Artifacts: design doc (markdown), implementation plan (YAML), message log
- State: `.jig/issues/<id>/issue.yaml`

### Agent

- A managed `claude_code_sdk` session
- Fields: name, type, assigned issue, assigned worktree, status, context sources
- Configured via agent type definitions
- State: `.jig/agents/` (runtime only — agents are ephemeral, recreated from type config on resume)

### Task

- A discrete unit of work assigned to an agent by the orchestrator
- Fields:
  - `id` — unique task ID
  - `description` — what the agent should do
  - `acceptance_criteria` — how to determine success
  - `agent_type` — which agent type should execute this
  - `input_context` — references to files, artifacts, prior outputs
  - `completion_state` — enum + reason:
    - `success` — task completed, acceptance criteria met
    - `needs_info` — agent needs clarification (includes question)
    - `blocked` — agent hit a dependency or conflict (includes blocker description)
    - `failed` — agent could not complete (includes error details)
- State: `.jig/issues/<id>/tasks/<task-id>.yaml`

### Workflow

- A DAG of phases defining how an issue moves from inception to completion
- Each phase declares: agent type, entry criteria, exit criteria, available tools
- Defined as YAML in `.jig/workflows/`
- v1 workflow: `spec -> test -> implement -> review` (linear)

## Orchestrator

The orchestrator is a Python async process — not a Claude agent. It owns:

### Workflow Execution

- Walks the phase DAG for the current issue
- v1 phases: `spec -> test -> implement -> review`
- Each phase has entry criteria (previous phase completed successfully) and exit criteria (agent task `completion_state: success`)
- Phase transitions require explicit criteria being met — no skipping or freelancing across boundaries

### Hybrid Discretion Within Phases

Within a phase, the orchestrator has discretion. For example, during `implement`, it could:
- Spawn the dev agent
- Review the output
- Loop back if tests fail
- Request additional context

This happens without defining sub-phases — the orchestrator manages intra-phase logic in Python code.

### Agent Lifecycle

- Spawns agents via `claude_code_sdk` as async tasks
- Configures each agent with: system prompt, allowed tools, Jig MCP server, injected context
- Monitors task completion via `completion_state`
- Routes `needs_info` back to user or another agent
- Handles `failed`/`blocked` states (retry, escalate, or abort)

### Git Operations

All git activity flows through the orchestrator. Agents have no git access.

- Creates worktrees: `.jig/worktrees/<issue-id>/<phase>`
- Commits changes at phase completion
- Creates issue branch from base
- On workflow completion: merges to target branch or opens PR (user-configurable)
- Worktree cleanup: on validation (not on completion or failure — worktrees persist until validated)

## Message Bus

### Architecture

Pub/sub built on asyncio queues with file-backed persistence. Every message is written to `.jig/issues/<id>/messages.jsonl` before being delivered. The in-memory queues handle routing and delivery; the file log is the source of truth. Exposed to agents as a local MCP server.

### Topics

Scoped to an issue:
- `issue.<id>.orchestrator` — messages to the orchestrator
- `issue.<id>.agent.<name>` — messages to a specific agent
- `issue.<id>.broadcast` — all subscribers

### Message Schema

```yaml
id: string              # unique message ID
sender: string          # agent name, "orchestrator", or "user"
recipient: string       # agent name, "orchestrator", or "broadcast"
type: enum              # task_assignment, task_completion, question, answer, context_update, status
payload: object         # structured content depending on type
timestamp: datetime
correlation_id: string  # links related messages (e.g. question -> answer)
```

> Note: Uses `sender`/`recipient` instead of `from`/`to` to avoid Python reserved word conflicts.

### Persistence

Every message appended to `.jig/issues/<id>/messages.jsonl`. On crash recovery, the orchestrator replays the message log to reconstruct state.

### TUI Event Stream

The bus publishes to a local WebSocket server. Additional event types for the TUI:
- `workflow_phase_changed`
- `agent_spawned`
- `agent_completed`
- `user_input_required`

## Jig MCP Server

A local MCP server run by the orchestrator. Each agent's `claude_code_sdk` session is configured with this server. Scoping is per-session: the orchestrator starts a separate MCP server instance (or connection) per agent, filtering which issue artifacts and context are visible based on the agent's type and current phase.

### Tools

- `publish_message` — send a message to another agent or the orchestrator
- `report_completion` — signal task done with a `completion_state`
- `request_context` — ask for additional files or artifacts to be loaded

### Resources

- `issue://design` — the design doc for the current issue
- `issue://plan` — the implementation plan
- `issue://messages` — message history for this issue
- Agent-specific context files (loaded based on agent type config and orchestrator injection)

## Agent Types

Defined in YAML config files at `.jig/agent_types/<type>.yaml`.

### Config Schema

```yaml
name: string
system_prompt: string       # role and behavioral instructions
allowed_tools: list         # whitelist of Claude Code tools
denied_tools: list          # blacklist overriding defaults
default_context: list       # context sources this type typically needs
                            # (files, globs, issue artifacts, custom snippets)
```

Custom tools are provided via the Jig MCP server — not defined per agent type. All agents get the same MCP tools; the orchestrator controls what each agent can see via scoping.

### v1 Agent Types

Kept simple for v1. Granular tool restrictions deferred to later versions.

- **spec** — drafts the design doc from the issue description
- **test** — writes tests based on the design doc and plan
- **dev** — implements code to pass the tests
- **review** — reviews the final output for correctness and quality

### Context Injection

Each agent type defines `default_context` — what it typically needs. The orchestrator augments this per-phase:

- **spec**: issue description, existing codebase structure
- **test**: design doc, implementation plan, relevant source files
- **dev**: design doc, implementation plan, test files, relevant source files
- **review**: all of the above plus test results

Context sources can be: file paths, globs, issue artifact references, or prior agent outputs.

## Workspace & Git Model

### Worktree Isolation

One worktree per active agent. For the v1 linear flow, only one worktree exists at a time per issue.

1. Orchestrator creates worktree from issue's base branch
2. Agent's `claude_code_sdk` session points at the worktree as working directory
3. On phase completion, orchestrator commits changes in the worktree
4. Next phase's worktree is created from the previous phase's commit
5. Sequential commits form clean history: spec -> test -> implementation -> review

### Bubblewrap Isolation

Each agent runs in a bubblewrap container for process-level isolation from other agents. The bubblewrap sandbox restricts:
- Filesystem access to the agent's worktree only
- No access to other agents' worktrees or `.jig/` state files
- Network access as needed for the MCP server connection

### Cleanup

Worktrees are cleaned up after validation — they persist through completion and review until the user validates the output.

## TUI & CLI

### WebSocket Server

Jig core runs a local WebSocket server on a configurable port. Protocol: JSON messages using the same schema as the message bus, plus TUI-specific event types.

### Gridland TUI

TypeScript/Bun process using the Gridland framework. Connects to the WebSocket on startup.

v1 display:
- Current workflow phase
- Active agent and its status
- Streaming agent output
- Message log
- Prompt for user input when `user_input_required`

Can compile to standalone binary via `bun build --compile`. Also renders in browser via Gridland's web support for future dashboard use.

### CLI

Thin Python CLI for one-shot commands:
- `jig init` — initialize `.jig/` in a project
- `jig start <issue>` — begin a workflow for an issue
- `jig status` — show current workflow state (reads `.jig/` directly)
- `jig stop` — stop the current workflow
- `jig validate <issue>` — mark an issue as validated, trigger worktree cleanup

## State Persistence

### Directory Structure

```
.jig/
├── config.yaml              # project-level config
├── agent_types/             # agent type definitions
│   ├── spec.yaml
│   ├── test.yaml
│   ├── dev.yaml
│   └── review.yaml
├── workflows/
│   └── default.yaml         # v1 linear workflow definition
├── issues/
│   └── <issue-id>/
│       ├── issue.yaml       # metadata, status, current phase
│       ├── design.md        # design doc (spec agent output)
│       ├── plan.yaml        # implementation plan
│       ├── messages.jsonl   # full message log
│       └── tasks/
│           └── <task-id>.yaml
└── worktrees/               # managed by orchestrator
    └── <issue-id>/
        └── <phase>/         # git worktree for this phase
```

### Crash Recovery

On startup, the orchestrator:
1. Scans `.jig/issues/` for issues with status `in_progress`
2. Replays `messages.jsonl` to reconstruct workflow position
3. Resumes from the last completed phase

What's NOT persisted: in-memory queue state, agent process handles. These are reconstructed from file state.

## Security

- Agents run in bubblewrap containers for isolation from each other
- Agents have no git access — all git operations go through the orchestrator
- Tool access restricted per agent type via `allowed_tools`/`denied_tools`
- The entire system runs in Docker, with optional docker-compose for dependencies
- Bubblewrap containers restrict filesystem access to the agent's worktree only

## v1 Constraints

- Single issue at a time, single project
- Linear workflow only (no parallel agents)
- File-based persistence (no database)
- TUI is minimal: phase display, agent output, message log, user input prompt
- Agent types are simple — granular tool restrictions deferred
- No web dashboard (Gridland's browser rendering reserved for future)
