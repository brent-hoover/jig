# Persistent Agents Refactor — Design Spec

## Overview

Refactor Jig to decouple agents from workflow phases. Agents become persistent entities with identity, memory, and session history. The orchestrator becomes a dormant Claude Code agent that wakes only to handle exceptions. The happy path is still driven by Python workflow code.

## Agent Model

### Agent Type

A template for creating agent instances. Stored as YAML in `.jig/agent_types/<role>.yaml`.

Fields:
- `role` — the agent's role (e.g. `spec`, `dev`, `test`, `review`)
- `system_prompt` — structured as identity + capabilities + constraints
- `allowed_tools` — list of Claude Code tools this agent can use
- `default_context` — context sources loaded at session start (files, globs, issue artifacts)

No model field — all agents use Claude Code with the default model. Model selection deferred until needed.

### Agent Instance

A running (or dormant) entity spawned from an agent type. Lives in `.jig/agents/<instance-id>/`.

Directory structure:
```
.jig/agents/<instance-id>/
├── instance.yaml    # type reference, instance ID, status, session_id
├── memory/          # persistent memory files (markdown)
```

Fields in `instance.yaml`:
- `id` — unique instance ID (e.g. `dev-1`, `test-1`)
- `agent_type` — reference to the agent type role
- `status` — `idle`, `active`, `dormant`
- `session_id` — Claude Code session ID for resumption (null if no active session)
- `current_task_id` — the task currently assigned (null if idle)

### Agent Pool

All instances live under `.jig/agents/`. Multiple instances can share the same type (e.g. `dev-1`, `dev-2` for parallel work). For the initial implementation, one instance per type is created at `jig init`.

### Lifecycle

1. **Created** at `jig init` — one instance per agent type, status `idle`
2. **Assigned a task** — server picks an idle instance with matching role, sets status `active`
3. **Session started** — Claude Code session begins, agent memory files loaded into context
4. **Works** — agent operates in its worktree, uses MCP tools, communicates on the bus
5. **Reports completion** — calls `report_completion`
6. **Goes dormant** — status set to `dormant`, session_id preserved for resumption
7. **Resumed** — if needed for follow-up (questions from other agents, retries), session is resumed with full history

Memory persists across workflows. Sessions persist within a workflow run (via `resume=session_id`). Between workflow runs, agents start fresh sessions but load their memory files.

## Workflow + Happy Path

### Workflow Config

YAML in `.jig/workflows/<name>.yaml`. Defines ordered phases and exception handling guidelines.

```yaml
name: default
phases:
  - name: spec
    role: spec
    task_template: "Draft a design document for: {issue_title}"
    acceptance_criteria: "Design doc covers all requirements"
  - name: test
    role: test
    task_template: "Write tests based on the design doc for: {issue_title}"
    acceptance_criteria: "Tests cover specified behavior and edge cases"
  - name: implement
    role: dev
    task_template: "Implement code that passes the tests for: {issue_title}"
    acceptance_criteria: "All tests pass"
  - name: review
    role: review
    task_template: "Review implementation for: {issue_title}"
    acceptance_criteria: "No critical issues found"

exception_flows:
  needs_info:
    - "Check if another agent can answer the question via the message bus"
    - "If another agent can help, route the question to that agent"
    - "If no agent can help, pause the workflow and ask the user"
  blocked:
    - "Analyze the blocker and determine if a different agent or retry can resolve it"
    - "If resolvable, retry the phase with additional context"
    - "If unresolvable, pause workflow and report to user"
  failed:
    - "Retry once with additional context from the failure reason"
    - "If retry also fails, pause workflow and report to user"
```

Phase names are now free-form strings (not an enum). Phases reference agent roles, not specific instances.

### Happy Path Execution

Driven by Python code (the server). No orchestrator agent involved.

1. Server loads workflow config
2. For each phase:
   a. Find an idle agent instance from the pool with the matching role
   b. Create a task (from the phase's template + issue context)
   c. Assign the task to the agent instance (set instance status to `active`)
   d. Create/reuse a worktree for the agent
   e. Start or resume the agent's Claude Code session with: system prompt, memory files, task description, worktree as cwd
   f. Agent works, calls MCP tools, communicates on bus
   g. Agent calls `report_completion(status: success)`
   h. Server commits worktree, marks phase done, sets agent to `dormant`
   i. Advance to next phase
3. All phases complete → issue marked completed

This path is free (no LLM tokens for orchestration), deterministic, and fast.

## Orchestrator Agent

A special Claude Code agent that exists **only to handle exceptions**. Dormant during the happy path.

### When It Wakes Up

The Python server detects:
- An agent reports `needs_info`, `blocked`, or `failed`
- A configurable timeout expires with no progress
- Agents are in a conversation loop (>N exchanges without resolution)

The server resumes the orchestrator's session (or starts a new one) with:
- The exception context (which agent, what phase, what happened)
- Recent message bus history
- The workflow's `exception_flows` guidelines
- The orchestrator's own memory files

### Orchestrator MCP Tools

- `get_workflow_status()` — current phase, agent states, issue state
- `get_message_history(issue_id)` — bus history for catch-up
- `assign_task(role, description)` — create a task and assign to an agent with that role
- `send_message(agent_id, message)` — direct message to an agent
- `resume_agent(agent_id)` — wake up a dormant agent
- `pause_workflow(reason)` — stop and report to user
- `advance_phase()` — push the workflow to the next phase
- `retry_phase(additional_context)` — re-run the current phase with more context

**No code tools.** No Read, Edit, Write, Bash. The orchestrator coordinates, it does not code.

### After It Acts

The orchestrator goes back to sleep. The server resumes normal workflow execution if the orchestrator resolved the issue.

### Orchestrator Memory

Lives in `.jig/agents/orchestrator/memory/`. It learns from exceptions over time — which solutions worked, which agents struggle with what.

## Agent Communication

### Direct Messaging

Agents communicate directly via the message bus using MCP tools:
- `send_message(recipient, type, payload)` — send a message to another agent or broadcast
- `check_messages()` — read incoming messages for this agent

### Conversation Protocol

When agent A needs something from agent B:
1. A calls `send_message(recipient: "dev-1", type: "question", payload: {...})`
2. The server sees the message on the bus. If B is dormant, the server resumes B's session with the message as context.
3. B reads the message, responds via `send_message(recipient: A's ID, type: "answer", ...)`
4. The server routes the answer back to A (resumes A if dormant)

### Stall Detection

The server monitors conversations:
- If agents exchange >N messages without resolution (configurable), wake the orchestrator
- If no messages flow for a configurable timeout during an active phase, wake the orchestrator
- The orchestrator can break deadlocks by providing direction, reassigning work, or pausing for user input

## Changes from Current Implementation

### Stays the Same
- Message bus (`bus.py`) — pub/sub + JSONL persistence
- Worktree management (`worktree.py`) — create/commit/remove
- MCP tool handlers (`mcp_tools.py`) — publish_message, report_completion, request_context (extended with new tools)
- WebSocket server (`ws_server.py`) + event emitter (`events.py`)
- CLI structure (`cli.py`) — init, start, status, validate
- Core models (Issue, Task, Message, ProjectConfig, CompletionState, etc.)

### Changes
- `AgentTypeConfig` — renamed field from `name` to `role`, drops `denied_tools`
- `orchestrator.py` — becomes thinner: happy-path-only Python workflow engine. Exception handling delegates to orchestrator agent.
- `agent.py` — supports session resumption (`resume=session_id`), memory file loading, agent instance state management
- Persistence — agent instances get their own directory under `.jig/agents/`
- Workflow config — phases reference roles (free-form strings, not enum), gains `task_template`, `acceptance_criteria`, and `exception_flows` section
- `jig init` — creates agent instances (one per type) in addition to agent types

### New
- `AgentInstance` model — tracks instance ID, type, status, session_id, current task
- Agent pool management — find idle agents by role, manage lifecycle
- Orchestrator agent type + instance — special agent with coordination MCP tools
- `check_messages` MCP tool — agents can read incoming messages
- Server-side message monitoring — wakes dormant agents when messages arrive, detects stalls
- Agent memory directory and loading logic
- Orchestrator MCP tools (get_workflow_status, assign_task, resume_agent, pause_workflow, etc.)

### Removed
- `WorkflowPhase` enum — phases are free-form strings in workflow YAML
- Hard coupling between phase names and agent type names
