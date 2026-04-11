# Orchestrator + Ticket Model Redesign — Design Spec

## Overview

Reshape the Jig orchestrator and data model around two ideas:

1. **A service-shaped orchestrator** that owns scheduling and recovery routing for every ticket in a project, but runs deterministic phase loops as plain code — with LLM judgment only at decision points.
2. **A unified ticket model** where every user-story-driven unit of work (features, bugs, chores, tasks, questions) is a first-class ticket with typed comments for progress, rationale, and history.

Together these replace the current `Issue` / `Task` / `AgentInstance` / `AgentPool` / per-phase worktree design. Agents become ephemeral, the orchestrator dispatches fresh agents for unaddressed bus messages, and conversations flow through the Claude Agent SDK's streaming input mode so real-time agent-to-agent messaging doesn't need a custom blocking MCP tool.

This spec does not cover: proactive agents (e.g. a Product Manager that wakes on timers), cross-project scheduling, or multi-orchestrator coordination. Those are explicit non-goals for MVP.

## Goals

- Collapse the orchestrator/walker/pool/bus-monitor quartet into one service per project.
- Make every structured work unit a ticket with queryable status, hierarchy, and history.
- Replace polling-based inter-agent messaging with streaming-input conversations.
- Give agents a proper memory model: role-scoped (persistent) and ticket-scoped (via comments).
- Keep the happy path deterministic; reserve LLM decisions for scheduling and recovery routing.

## Non-Goals (MVP)

- Proactive/scheduled agents (no timer-based wake-ups).
- Cross-project scheduling — one orchestrator process handles one project.
- Multi-orchestrator coordination / locking — assume one orchestrator process per project at a time.
- Claude session resumption — agents resume via context continuity (comment history), not conversation continuity.
- Semantic/vector search over memory — naive filtering only.
- Shared project-wide memory outside the per-ticket comment stream (role memory is enough).
- Multi-prompt-template-per-role beyond the phase/response split (see open decision below).
- Dynamic or learned skills — the jig skills library is a static set of markdown files shipped with jig and authored by maintainers. Agents do not author or modify skills at runtime.
- Skills that duplicate Claude's default training — skills cover jig-specific preferences and non-obvious tooling conventions only.

## Runtime Components

Two runtime entities, one shared message bus.

### Orchestrator (singleton per project process)

- One instance per running Jig process, which maps to one project directory.
- Owns a `MessageBus`, a `Database` (for ticket/comment collections), and a `project: Project` handle.
- Maintains `self._running_tickets: dict[ticket_id, asyncio.Task]` for in-flight top-level tickets.
- Maintains `self._live_subscribers: dict[(ticket_id, role), asyncio.Task]` — the authoritative table of which running agents are currently subscribed to which tickets. Every spawn registers here; every agent exit deregisters.
- Runs several concurrent asyncio loops inside a single object:
  - **Service loop** — subscribes to the `orchestrator` bus topic for external wake-ups (new tickets from CLI/TUI, shutdown requests).
  - **Per-ticket loops** — one `_run_ticket(ticket_id)` task per in-flight top-level ticket, walks its workflow.
  - **Dispatch loop** — `_run_dispatch_loop()` watches all bus traffic and spawns fresh agents for unaddressed messages (see below).
- Uses the LLM (Opus) for two decision points only:
  - `_decide_scheduling(ticket)` — should this new ticket start now, or wait for conflicting work to finish?
  - `_decide_recovery(ticket, task, last_phase_run)` — a phase just failed; retry, reroute to an earlier phase, or abort?
- The happy path (walking through workflow phases on success) is plain deterministic code inside `_run_ticket`; no LLM involved.

#### Dispatch loop responsibility

The dispatch loop is how agents come into existence in response to bus traffic that no running agent is already handling. It lives inside the orchestrator rather than a separate `BusMonitor` class because it needs the same bus subscription, the same `_live_subscribers` table, and the same `run_agent` spawning machinery the orchestrator already owns.

- Subscribes to all bus traffic at startup.
- For each message (ticket created, ticket assigned, comment posted): if the addressed role has **no live subscriber** in `_live_subscribers` for that ticket, spawns a fresh agent of that role as an asyncio task and feeds the triggering message in as its initial prompt.
- Does not spawn when a live subscriber exists; messages flow naturally via streaming input to the running agent.
- **Never spawns for `assignee="user"`.** The TUI is the only subscriber for the `"user"` role and must be running for user-assigned tickets to be answered. If no TUI is attached, user-assigned tickets remain OPEN until one connects.
- Agnostic about *who* sent the message. It handles agent-to-agent clarification ("dev asks spec-writer a question"), external-to-agent messages, orchestrator-to-agent spawns, and TUI delivery of user questions through the same single code path.
- This is the single mechanism for "an agent needs to come into existence because someone's asking it something."

### Agents (ephemeral, one per work unit)

- Spawned fresh every time via `run_agent(role, ticket, worktree, bus, spawn_reason)`.
- No persistent `AgentInstance` model. No pool. No session_id reuse.
- `spawn_reason` is an enum — `PHASE_PRIMARY` or `QA_RESPONDER` — that tells the runtime which prompt shape to build for the initial turn. Two invocation shapes, same function:
  - **Phase primary** — invoked by the orchestrator's per-ticket loop to execute a workflow phase on a task ticket. Initial prompt is the task description plus acceptance criteria.
  - **Q&A responder** — invoked by the orchestrator's dispatch loop when a message targets a role with no live subscriber on that ticket. Initial prompt is the triggering message plus parent context.
- Uses the Claude Agent SDK in **streaming input mode**: `run_agent` constructs an async generator that yields user turns over time, driven by:
  - The initial prompt (first yield)
  - Incoming bus messages addressed to this agent (subsequent yields)
  - A terminal signal when the agent calls `update_ticket(status=RESOLVED)` or `update_ticket(status=BLOCKED/NEEDS_INFO)` — generator closes and Claude's conversation wraps up.
- Loads role memories and parent-ticket comment history at spawn time and injects them into the initial prompt. See **Environment Context** and **Jig Skills Library** sections below for the full injection order.

### TUI (user-facing client)

The TUI is a plain bus-and-database client, not a privileged component. It runs in the same Jig process as the orchestrator and has the same read and write access to the ticket store and message bus.

- **Subscribes to `tickets.*`** — receives every ticket lifecycle event, comment, commit, and status change in real time and renders a stream view per ticket.
- **Registers as the live subscriber for `role="user"`** — the TUI inserts itself into the orchestrator's `_live_subscribers` table under the `"user"` role, for every ticket the user should be able to answer on. This prevents the dispatch loop from trying to spawn an agent for user-assigned work and is the mechanism for agent→user questions (see Protocol E).
- **Creates tickets** — the TUI is the primary UI for humans to file FEATURE, BUG, and CHORE tickets. A "new ticket" action opens a form (type, title, description) and calls `create_ticket(..., created_by="user")`, which publishes to the `orchestrator` bus topic and triggers scheduling through the normal path.
- **Posts comments and status updates** — same ticket CRUD surface agents use. When the user answers a QUESTION ticket or interrupts a running agent, it's a `comment_on_ticket` call (plus `update_ticket(status=RESOLVED)` for questions).
- **Event types rendered**: `ticket_created`, `ticket_status_changed`, `comment_posted`, `commit_recorded`, `agent_spawned`, `agent_exited`. All of these are already bus events under the new model — the TUI just subscribes and displays them.

The TUI carries no orchestration logic. If the TUI is absent (headless mode, CI, daemon run), the system still functions; user-assigned QUESTION tickets simply sit in OPEN until a TUI attaches.

## Data Model

### Project

```python
class Project(StoreModel):
    id: str                  # stable identifier (slug or UUID)
    name: str
    path: str                # absolute path to the project root
    default_branch: str = "main"
    description: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    test_command: str = ""
    build_command: str = ""
```

For MVP there is one `Project` per orchestrator process, persisted at `.jig/project.json`. This field carries the API shape for future multi-project daemon support without forcing us to build it now.

Tickets reference the project implicitly — every ticket is scoped to the process's single project.

### Ticket

```python
class TicketType(str, Enum):
    FEATURE = "feature"    # top-level: new functionality
    BUG = "bug"            # top-level: broken functionality
    CHORE = "chore"        # top-level: maintenance, deps, refactors
    TASK = "task"          # sub-unit: a phase execution within a parent ticket
    QUESTION = "question"  # sub-unit: clarification needed during work

class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"        # waiting on external input or unresolved question
    NEEDS_INFO = "needs_info"  # agent paused pending clarification from another role
    FAILED = "failed"          # orchestrator gave up after recovery attempts
    RESOLVED = "resolved"      # work complete, awaiting merge
    CLOSED = "closed"          # merged and torn down

class Ticket(StoreModel):
    type: TicketType
    status: TicketStatus = TicketStatus.OPEN
    title: str
    description: str = ""
    assignee: str | None = None          # role name, or "orchestrator"
    parent_id: str | None = None         # sub-units point at their top-level parent
    blocks: list[str] = []
    blocked_by: list[str] = []
    labels: list[str] = []
    created_by: str                      # role name or "user"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Top-level types (FEATURE, BUG, CHORE) are the things a user files and the orchestrator schedules. Each has its own worktree. Sub-unit types (TASK, QUESTION) live under a parent and use the parent's worktree.

### Comment

All rationale, progress, phase history, and ticket-scoped conversation live as comments.

```python
class Comment(StoreModel):
    ticket_id: str
    author: str                          # role name
    content: str
    kind: Literal[
        "comment",        # free-form chat / narration
        "commit",         # tied to a git commit from commit_progress
        "phase_run",      # durable record of a phase execution attempt
        "decision",       # recorded rationale for a design choice
        "status_change",  # audit trail for status transitions
    ] = "comment"
    commit_sha: str | None = None        # set when kind=="commit"
    phase_result: Literal["success", "failed", "blocked", "needs_info"] | None = None
    phase_branch: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

`phase_run` comments carry structured fields so the orchestrator can query them directly for recovery decisions without parsing free-form text. All prior `PhaseHistoryEntry` data survives in this shape.

### Models being deleted

- `Issue` — replaced by `Ticket(type=FEATURE|BUG|CHORE)`
- `Task` — replaced by `Ticket(type=TASK)`
- `PhaseHistoryEntry` — replaced by `Comment(kind="phase_run")`
- `AgentInstance` — no persistent agent identity
- `AgentStatus` enum — no dormant/active tracking
- `AgentPool` class (`jig/pool.py`) — delete entire file
- `IssueMemoryEntry` — redundant with Comment
- `CompletionReport` — replaced by `update_ticket(status=..., comment=...)` flow
- `MessageDirection`, `AgentMessage` — replaced by ticket comments

### AgentTypeConfig (modified)

```python
class AgentTypeConfig(BaseModel):
    role: str
    phase_prompt: str                    # system prompt when spawned as phase primary
    response_prompt: str = ""            # system prompt for Q&A responder; empty = derive from phase_prompt
    allowed_tools: list[str] = []
    can_message: list[str] = []          # allowlist of roles this agent can initiate messages to
    default_context: list[str] = []
```

- `system_prompt` renamed to `phase_prompt` for clarity about its dual-use intent.
- `response_prompt` is new and optional; when empty, the agent runtime wraps `phase_prompt` with a "you are answering a question" preamble.
- `can_message` enforces communication topology at the MCP tool layer. `"orchestrator"` is always implicitly allowed.

## Persistence Layout

```
.jig/
  project.json                   # single Project record for this directory
  environment.md                 # optional: user-authored environment quirks (Layer 2 of env context)
  store/
    tickets.jsonl                # all tickets
    comments.jsonl               # all comments (including phase_run records)
    messages.jsonl               # MessageBus change feed
    memories.jsonl               # role-scoped memories
  worktrees/{ticket_id}/          # one per top-level ticket (FEATURE/BUG/CHORE)
  agent_types/{role}.json
  workflows/{name}.json
```

Jig skills ship inside the `jig` Python package at `jig/skills/*.md` and are discovered via `importlib.resources` — they do not live under `.jig/`.

- `.jig/issues/` is gone. Tickets live in one collection.
- `.jig/worktrees/` is one level shallower — no more per-phase subdirectories.
- `.jig/environment.md` is new; absent by default, loaded verbatim into every agent prompt when present.
- The bus log and memory store paths are unchanged.

## Collections and Indexes

Under `jig/store/`:

- `tickets` — `TypedCollection[Ticket]`, indexed on `type`, `status`, `assignee`, `parent_id`
- `comments` — `TypedCollection[Comment]`, indexed on `ticket_id`, `kind`, `author`
- `memories` — existing role-scoped memory store in `jig/store/`
- `messages` — existing `MessageBus`

Index choices are driven by known query patterns:
- Orchestrator recovery: `comments.find_where(ticket_id=task_id, kind="phase_run")`
- Dispatch loop lookup: `tickets.find_where(assignee=role, status=OPEN)` to see pending work
- Resumption scan: `tickets.find_where(type=FEATURE|BUG|CHORE, status=IN_PROGRESS)`

## Protocols

### Protocol A — Ticket lifecycle

The unified replacement for `report_completion`, `send_message`, and half of the current bus semantics.

- `create_ticket(type, title, description, assignee=None, parent_id=None, labels=[]) -> ticket_id`
- `read_ticket(ticket_id) -> Ticket`
- `update_ticket(ticket_id, **fields) -> Ticket` — supports all field updates, including status transitions to any value in `TicketStatus`. Status transitions auto-emit a `status_change` comment.
- `comment_on_ticket(ticket_id, content, kind="comment"|"decision") -> comment_id` — agents may only write `kind="comment"` or `kind="decision"`; `commit`, `phase_run`, and `status_change` comments are emitted by system primitives (`commit_progress`, the orchestrator, and `update_ticket` respectively) and are rejected from this tool.
- `list_tickets(type=None, status=None, assignee=None, parent_id=None) -> list[Ticket]`
- `read_comments(ticket_id, kind=None) -> list[Comment]`

All of these publish bus events on the `tickets.{ticket_id}` topic. The orchestrator and any running agents subscribed to the ticket see changes in real time.

### Protocol B — Mid-work progress checkpoints

- `commit_progress(ticket_id, message) -> {sha, comment_id}`
- Three-in-one: `git commit` in the ticket's worktree, `comment_on_ticket(kind="commit", commit_sha=sha)`, bus publish.
- Agents are expected to call this after every meaningful chunk of work, not just at phase end.
- Produces the rich narrative that powers crash-recovery resumption prompts.

### Protocol C — Real-time agent-to-agent conversation via streaming input

- No `await_response` MCP tool. Incoming messages are conversation input, not tool results.
- `run_agent` constructs an async generator for the Claude Agent SDK's streaming-input mode:
  1. Yields the initial prompt (role system text + task/question + memories + ticket context)
  2. Subscribes to the bus for events addressed to this agent on this ticket
  3. Yields each incoming bus event as a user turn, translated to human-readable form (`[comment from dev on ticket T-42]: <content>`)
  4. Terminates when the agent calls `update_ticket(status=...)` with any terminal status on its primary ticket — `RESOLVED`, `BLOCKED`, or `NEEDS_INFO`. `FAILED` is reserved for the orchestrator's recovery path and is not set by agents directly.
- Outbound messages remain tool calls: the agent uses `comment_on_ticket` or `create_ticket(type=QUESTION, ...)` to speak.
- Communication allowlists are enforced in `create_ticket` and `comment_on_ticket` handlers: reject if `AgentTypeConfig.can_message` doesn't cover the target role (for comments, the target is the ticket's assignee).

### Protocol D — Orchestrator wake-up from outside

- External callers (CLI, TUI, test harness) publish to the `orchestrator` bus topic with a typed payload:
  - `schedule_request(ticket_id)` — a new top-level ticket is ready for scheduling
  - `shutdown_request()` — graceful shutdown
- The orchestrator's service loop: `while running: msg = await bus.next(topic="orchestrator"); handle(msg)`
- The orchestrator also subscribes to `tickets.*` to react to status changes on in-flight tickets (e.g. a task finishing).

### Protocol E — Agent ↔ user conversations

Users participate in the ticket/comment graph as a special role named `"user"`. This keeps the conversation machinery uniform — no separate channel, no special tool surface — while preventing the orchestrator from ever trying to spawn a "user agent."

**Agent → user narration** (status updates, progress, commits, free-form thinking):
- No special case. Agents write via `comment_on_ticket` and `commit_progress` as usual.
- The TUI is already subscribed to `tickets.*`, so the user sees narration land in the per-ticket stream view in real time.

**Agent → user questions** (clarification the agent can't resolve on its own):
- Agent calls `create_ticket(type=QUESTION, parent_id=<current_ticket>, assignee="user", description="...")`.
- Ticket creation publishes to the bus. The TUI, registered as the `"user"` live subscriber, receives the event and surfaces a prompt to the human.
- The asking agent's streaming-input generator blocks waiting for the response — it's still on the bus, just not terminating because it hasn't hit a terminal status.
- The dispatch loop sees the new QUESTION ticket but skips it because `assignee="user"` (see dispatch loop above). If no TUI is running, the question sits in OPEN indefinitely; the asking agent can still make progress on other work or choose to set its own status to `NEEDS_INFO` and exit.

**User → agent answer**:
- TUI displays the QUESTION ticket and collects the user's response.
- TUI calls `comment_on_ticket(question_ticket_id, content=answer, author="user")`, then `update_ticket(question_ticket_id, status=RESOLVED)`.
- Both events publish to the bus. The asking agent (which subscribed to the QUESTION ticket topic when it created the ticket) receives the comment as a streaming-input turn and continues.

**User → agent interrupt** (user wants to inject guidance into a running agent without a structured question):
- TUI calls `comment_on_ticket(task_ticket_id, content=<user text>, author="user")` directly on the task the agent is currently working on.
- Agent receives it as a streaming-input turn, same shape as any other bus event.
- No special tool surface — user interrupts are structurally identical to agent-to-agent comments.

**Communication allowlist implication**: `"user"` should always be present in every role's `can_message` allowlist by convention, so any agent can escalate to a user question. This is documented in the agent type config template rather than hard-coded in the MCP tool layer.

## MCP Tool Surface

The MCP server exposed to agents (via `jig/mcp_server.py`):

**Ticket operations**
- `create_ticket(type, title, description, assignee=None, parent_id=None, labels=[])`
- `read_ticket(ticket_id)`
- `update_ticket(ticket_id, **fields)`
- `comment_on_ticket(ticket_id, content, kind="comment")`
- `list_tickets(type=None, status=None, assignee=None, parent_id=None)`
- `read_comments(ticket_id, kind=None)`

**Work operations**
- `commit_progress(ticket_id, message)` — git + comment + bus
- `record_learning(content)` — writes to role memory store (not a ticket)

**Read operations for context**
- `read_file(path)` / other Claude Code default tools (controlled by `allowed_tools`)

**Deleted tools**
- `send_message` — **outbound messages now go through ticket operations.** To speak on an existing ticket, agents call `comment_on_ticket(ticket_id, content)`. To ask another role something new, agents call `create_ticket(type=QUESTION, parent_id=..., assignee="<role>", description="...")`. Both auto-publish to the bus, so the recipient (or the orchestrator's dispatch loop if no live recipient exists) sees them immediately. Messages are no longer a separate channel — they *are* comments on tickets.
- `check_messages` — **inbound messages are no longer a tool surface at all.** Under streaming input mode, `run_agent` yields incoming bus events as user turns in the Claude conversation. The agent reads them the same way it reads the initial task prompt — no polling, no tool call, no await. The dispatch loop guarantees a running agent exists to receive them; if not, it spawns one and feeds the message in as the first turn.
- `await_response` — never built, obsolete under streaming input for the same reason as `check_messages`.
- `report_completion` — replaced by `update_ticket(status=RESOLVED, ...)` plus a final comment. Terminal status on the agent's primary ticket also closes the streaming-input generator, which ends the conversation cleanly.
- `request_context` — **keep**. Currently wired through `jig/mcp_tools.py`, `jig/agent.py`, and `jig/mcp_server.py`; used by agents to fetch project/issue context on demand. Migrate it to read `Project` + ticket comments under the new model rather than deleting it.

## Memory Model

Two scopes in MVP, both per-project:

1. **Role memory (persistent, cross-ticket, per-project)** — `MemoryStore` keyed by role, persisted at `.jig/store/memories.jsonl`. Agents call `record_learning(content)` at the end of successful work. Loaded into the initial prompt on every spawn. A dev agent's memories on Project A are separate from its memories on Project B; this matches the "one orchestrator per project" scoping and the cross-project non-goals.
2. **Ticket-scoped rationale** — lives as comments on the ticket (`kind="comment"`, `kind="commit"`, `kind="decision"`). Loaded into the initial prompt on every spawn for the relevant ticket.

Out of scope for MVP:
- **Cross-project shared role memory** — would require a location outside any project's `.jig/` (e.g. `~/.jig/memories.jsonl`). Revisit once single-project memory is working and there's a concrete use case for sharing.
- **Project-wide shared memory outside the comment stream** — revisit if agents start duplicating knowledge across tickets within the same project.

## Environment Context

A recurring failure mode in earlier versions: agents spawn, spend turns guessing at the environment ("is this pip or uv?", "is it pytest or vitest?"), run the wrong tool, install packages into the wrong place, and only recover after wasted effort. Giving agents ground-truth environment information at spawn time is the single highest-leverage context investment.

Three layers, all injected into every agent's initial prompt:

### Layer 1 — Structured `Project` fields

Already defined in the Data Model section. Covers machine-readable basics — language, framework, package_manager, test_command, build_command, default_branch. Used both to build the prompt section and to select skills (see below).

### Layer 2 — `.jig/environment.md`

A markdown file at the project root, authored by the user (or by a scaffolding agent during initial project setup) and loaded verbatim into every agent prompt. Covers project-specific quirks that can't be boiled down to structured fields:

- Exact setup commands with gotchas ("always `uv run python`, never bare `python` — the system Python is 3.9 and breaks our deps")
- Tool invocation conventions specific to this repo
- Known traps ("don't touch `legacy/`", "`openapi.yaml` is hand-maintained, don't regenerate", "pydantic is v2 here")
- Environment variables required for tests or builds
- Directory conventions and layout rules

`environment.md` is optional. If it doesn't exist, the prompt section is simply omitted.

### Layer 3 — Environment memory (deferred)

A dedicated `MemoryStore` scope for environment discoveries agents make during work (e.g. "the README's `pytest` command is stale; `uv run pytest` is what actually works"). Populated via a hypothetical `record_environment_fact` MCP tool, loaded into every agent prompt alongside `environment.md`.

**Not in MVP.** Revisit once Layers 1 and 2 are stable and we have evidence of which discoveries recur across agents. This scope can be added later without structural changes — it's just another `MemoryStore` and another prompt section.

### Injection order into the initial prompt

```
1. Role system prompt (phase_prompt or response_prompt)
2. Structured Project fields (from _build_project_section)
3. Matching jig skills (see Jig Skills Library)
4. .jig/environment.md contents verbatim
5. Role memories (record_learning entries)
6. Ticket context (task description + relevant comments from parent ticket)
7. Acceptance criteria and instructions
```

`environment.md` slots in **after** jig skills so per-project overrides beat jig's generic defaults — if a project says "ignore the uv skill, we use poetry here," the later text wins by virtue of being last among environment context.

## Jig Skills Library

A static, jig-shipped library of tech-stack-specific guidance. Analogous to `environment.md` in purpose but project-agnostic and maintained by jig itself.

### Rationale

Without skills, every agent on every Python/uv project relearns the same conventions ("use `uv run pytest`, not bare `pytest`"). With skills, jig maintainers author the guidance once and it gets injected wherever it applies.

### File layout

```
jig/
  skills/
    python.md
    uv.md
    pytest.md
    ruff.md
    typescript.md
    pnpm.md
    vitest.md
    git-conventions.md
    jig-mcp-tools.md     # highest-value: how to use ticket ops, commit_progress, etc.
```

Each skill file is a markdown document with YAML frontmatter declaring its match criteria:

```markdown
---
name: python-uv
applies_to:
  language: python
  package_manager: uv
---

# Python + uv conventions

- Always use `uv run python`, never bare `python`
- Always use `uv run pytest`, never bare `pytest`
- Use `uv add <pkg>` to add deps, not `pip install`
- Lock file is `uv.lock`; commit it
- ...
```

### Match semantics

At orchestrator startup, load all skill files and build a list. For each agent spawn:

1. Read the `Project` fields (language, framework, package_manager, test_command, etc.).
2. For each skill, check its `applies_to` block: every key in `applies_to` must match the corresponding `Project` field exactly.
3. Skills with `applies_to: {}` (empty) always match — these are universal skills like `jig-mcp-tools.md` and `git-conventions.md`.
4. Concatenate matching skills in filename order and inject them into the prompt at position 3 (see Environment Context injection order).

No DSL, no regex, no priority ordering beyond filename sort. If two skills conflict, fix the skills — don't build resolution logic.

### What belongs in a skill

- Jig-specific conventions for this tool/language
- Non-obvious gotchas the tool's own docs don't cover
- "Use X, not Y" preferences where both would technically work
- Commands the agent is expected to invoke (with exact flags)

### What doesn't belong in a skill

- Content Claude already knows from training ("Python is a dynamically typed language…")
- Project-specific quirks (those go in `environment.md`)
- Role-specific instructions (those go in `phase_prompt` / `response_prompt`)
- Learned facts (those would go in environment memory, which is MVP+1)

### Initial MVP skill set

- `jig-mcp-tools.md` — universal. How to use `create_ticket`, `update_ticket`, `comment_on_ticket`, `commit_progress`, `record_learning`.
- `git-conventions.md` — universal. Conventional commits format, branch naming, when to commit.
- `python.md`, `uv.md`, `pytest.md`, `ruff.md` — for Python projects
- `typescript.md`, `pnpm.md`, `vitest.md` — for TS projects

Grow the set based on real friction observed during agent runs, not speculatively.

### Authoring and versioning

Skills ship inside the `jig` Python package and are discovered via `importlib.resources`. Updating skills means updating jig itself — no per-project customization in MVP. If a project wants to override a skill, the right mechanism is `environment.md`, which comes later in the prompt and wins by position.

## Worktree Strategy

- One worktree per top-level ticket, at `.jig/worktrees/{ticket_id}/`, checked out to branch `jig/{ticket_id}`.
- First TASK to run for a top-level ticket creates the worktree from `project.default_branch`. Subsequent TASKs reuse it.
- All phases commit onto the single `jig/{ticket_id}` branch. Linear history.
- Q&A responders and sub-unit agents operate in the parent's worktree — no separate checkout.
- When a ticket is resolved and merged back to `project.default_branch`, the worktree is torn down.

Concurrent access within one worktree: only the phase primary agent is expected to modify files. Q&A responders are implicitly read-only; if a responder determines the answer requires edits, it escalates via `update_ticket(status=BLOCKED)` so the orchestrator reroutes to a proper phase.

## Orchestrator Control Flow

### On startup

1. Load `Project` from `.jig/project.json`.
2. Load existing ticket and comment collections.
3. Load the bus from its log.
4. Initialize `_live_subscribers` as empty.
5. Spawn `_run_dispatch_loop()` as a background asyncio task.
6. Subscribe to the `orchestrator` topic.
7. Scan `tickets.find_where(type∈{FEATURE,BUG,CHORE}, status=IN_PROGRESS)` and spawn `_run_ticket(t.id)` for each.
8. Enter the main service loop.

### Main service loop

```python
async def run(self):
    while self._running:
        msg = await self._bus.next(topic="orchestrator")
        if msg.kind == "schedule_request":
            await self._handle_schedule(msg.ticket_id)
        elif msg.kind == "shutdown_request":
            self._running = False
```

`_handle_schedule` consults `_decide_scheduling` (LLM) to decide whether to start immediately or defer until conflicting work finishes. If approved, it spawns `_run_ticket(ticket_id)`.

### Per-ticket loop

```python
async def _run_ticket(self, ticket_id):
    ticket = self._tickets.get(ticket_id)
    workflow = self._load_workflow_for(ticket.type)
    worktree = await self._ensure_worktree(ticket)

    while not self._is_done(ticket_id):
        phase = self._next_phase(ticket_id, workflow)
        task_ticket = await self._create_task_ticket(ticket_id, phase)
        result = await run_agent(
            role=phase.role,
            ticket=task_ticket,
            worktree=worktree,
            bus=self._bus,
        )
        await self._write_phase_run_comment(task_ticket, result)
        if result.status == "success":
            continue
        command = await self._decide_recovery(ticket, task_ticket, result)  # LLM
        if command.action == "abort":
            await self._tickets.update(ticket_id, status=TicketStatus.FAILED)
            return
        # retry or reroute: continue the loop; _next_phase consults phase_run comments
```

- `_next_phase` reads `phase_run` comments on prior task tickets to figure out where we are in the workflow.
- `_decide_recovery` is the LLM call; it returns a retry/reroute/abort command.
- `self._write_phase_run_comment` produces the `kind="phase_run"` comment with structured result/branch fields.

### Dispatch loop

```python
async def _run_dispatch_loop(self):
    queue = await self._bus.subscribe_all()
    while self._running:
        event = await queue.get()
        target = self._resolve_target(event)  # (ticket_id, role) or None
        if target is None:
            continue
        if target[1] == "user":
            continue  # TUI is the only valid subscriber; never spawn an agent
        if target in self._live_subscribers:
            continue  # running agent will receive via streaming input
        # Spawn fresh agent for this unaddressed message
        task = asyncio.create_task(
            run_agent(
                role=target[1],
                ticket=self._tickets.get(target[0]),
                worktree=self._worktree_for(target[0]),
                bus=self._bus,
                spawn_reason=SpawnReason.QA_RESPONDER,
                initial_message=event,
            )
        )
        self._live_subscribers[target] = task
        task.add_done_callback(lambda _: self._live_subscribers.pop(target, None))
```

### Shutdown

- Set `_running = False`.
- Cancel `_run_dispatch_loop` task.
- Cancel all tasks in `_running_tickets`.
- Cancel all tasks in `_live_subscribers`.
- Persist any final state and exit.

## Crash Recovery

Durable state (survives process death):
- `tickets.jsonl`, `comments.jsonl`, `messages.jsonl`, `memories.jsonl`
- Worktrees and git branches (git is authoritative for code state)
- `.jig/project.json`

In-memory only (lost on crash):
- The orchestrator process and all asyncio tasks
- Running agent subprocesses
- Live streaming-input subscriptions

### Restart procedure

1. Load bus log, tickets, comments.
2. Scan `tickets` for `type ∈ {FEATURE, BUG, CHORE}` and `status == IN_PROGRESS`.
3. For each, spawn `_run_ticket(ticket_id)`. Same code path as fresh work — no separate "resume" branch.
4. Inside `_run_ticket`, the first pass of the loop reads `phase_run` comments to determine the latest phase state:
   - If the last `phase_run` has `phase_result == "success"`, advance to the next phase.
   - If there's a TASK ticket in progress with no terminal `phase_run`, that phase was mid-flight — spawn its agent with a **resumption-augmented prompt**.

### Resumption prompt shape

When a TASK ticket's agent is spawned mid-flight, the initial prompt includes:
- The normal task description and acceptance criteria
- All `kind="commit"` comments from the TASK ticket (commit messages with rationale)
- All `kind="comment"` comments from the parent ticket (design decisions, context)
- The raw `git log` for the ticket branch
- A preamble: "A previous agent was interrupted while working on this task. Above is what was accomplished before the interruption. Continue from that state."

The fresh Claude conversation reads this as context and picks up without needing session resumption. The worktree already has the surviving commits; no checkout gymnastics.

### What we accept as lossy on crash

- In-flight streaming-input subscriptions — agents resume from ticket comments instead.
- Partial `commit_progress` calls where the git commit landed but the comment didn't — git remains the source of truth; the next `commit_progress` re-establishes comment-log alignment.
- Questions in flight — if a QUESTION ticket had an open responder that never answered, the scan treats it as IN_PROGRESS and the dispatch loop respawns the responder on the first new bus event addressed to that role.

## Migration from the Current Code

This redesign touches most of the core modules. High-level impact:

- **Delete**: `jig/pool.py`, `jig/bus_monitor.py` (functionality absorbed into the orchestrator's dispatch loop), `AgentInstance` / `AgentStatus` / `Issue` / `Task` / `PhaseHistoryEntry` / `AgentMessage` models, `CompletionReport` model, `handle_check_messages`, `handle_report_completion`, `handle_send_message`.
- **Add**: `Project`, `Ticket`, `Comment` models. Ticket CRUD MCP tools. `commit_progress` MCP tool. Streaming-input shape in `run_agent`. `jig/skills/` directory with the MVP skill set. Skill loading and matching in the orchestrator startup path. `environment.md` loader. TUI ticket-creation form and `"user"` role subscriber registration.
- **Rewrite**: `jig/orchestrator.py` around the per-ticket loop, dispatch loop, ticket collections, and `_live_subscribers` tracking. `jig/mcp_tools.py` and `jig/mcp_server.py` around the ticket surface. `jig/agent.py` around streaming input and the new injection order (structured fields → skills → environment.md → memories → ticket context). `jig/worktree.py` around per-ticket (not per-phase) worktrees. TUI around ticket-centric events and user-role conversation flow.
- **Rewrite tests**: `tests/test_orchestrator.py`, `tests/test_bus_monitor.py`, `tests/test_mcp_server.py`, `tests/test_models.py`, `tests/test_persistence.py`, and anything that references the deleted models. Add `tests/test_skills.py` for skill loading and matching. Add `tests/test_environment_context.py` for `environment.md` loading and injection.

This will roll out as a single coordinated change with the old code removed in the same commit range as the new code lands. The existing jig-store library (`jig/store/`) is unaffected.

## Open Design Questions (to resolve during implementation)

1. **Prompt shape split** — `phase_prompt` vs `response_prompt` per role. The default when `response_prompt` is empty is to wrap `phase_prompt` with a preamble. If that wrapping turns out to work badly for some roles, we'll add explicit `response_prompt` entries to their configs. Not a blocker for starting implementation.
2. **Workflow-per-type mapping** — FEATURE vs BUG vs CHORE probably use different workflows. MVP can start with a single shared workflow and introduce per-type workflows when a real use case demands it. Workflows remain declared in `.jig/workflows/{name}.json`.
3. **Question-ticket scoping** — should QUESTION tickets always have `parent_id` pointing at the ticket the question is about, or can they be standalone? MVP: always parented. Simplifies context injection.

## Success Criteria

The redesign is successful when:
- A user can file a FEATURE ticket from the TUI (or CLI), the orchestrator schedules it, agents walk through the workflow phases on the happy path, and the result merges back to main without any agent pool machinery involved.
- A validator agent reporting a failure causes the orchestrator to wake (via bus), consult Opus for routing, and relaunch an earlier phase with structured context about what went wrong — all visible as ticket comments.
- A dev agent asking a clarifying question spawns a fresh spec-writer via the orchestrator's dispatch loop, which answers via comments, and both agents' conversations have the Q&A visible as streaming input.
- A dev agent asking the user a question via `create_ticket(type=QUESTION, assignee="user", ...)` surfaces in the TUI, the user answers, and the dev agent continues — all through the ticket comment stream with no separate message channel.
- Spawned agents do not waste turns guessing at the environment. A fresh Python/uv project agent uses `uv run pytest` from its first tool call because the injected skills and `environment.md` tell it exactly what to do.
- Killing the orchestrator process mid-run and restarting it resumes all in-progress tickets from their last committed state with no manual intervention.
- Running the full test suite passes after the rewrite with no references to `AgentInstance`, `AgentPool`, `Issue`, `Task`, `PhaseHistoryEntry`, `send_message`, or `check_messages` in production code.
