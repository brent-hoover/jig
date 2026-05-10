# Jig

## Instructions for Claude
1. Follow the rules for creating documentation as defined in feature-work/README.md
2. Do not write anything to ./superpowers or any other Claude-specific location

Multi-agent orchestrator that spawns Claude Code agents across a codebase. Python async core + Textual TUI.

## Project Structure

```
jig/                  # Python package — orchestrator, agents, stores, MCP, Textual TUI
tests/                # pytest suite (async)
templates/            # Project init templates
Dockerfile            # Container image (Python 3.12 + Node 22 + bwrap)
```

### Key modules

| Module              | Purpose                                                                      |
|---------------------|------------------------------------------------------------------------------|
| `cli.py`            | Click CLI: init, start, build, sync, validate, reset                         |
| `orchestrator.py`   | Singleton async orchestrator — ticket dispatch, agent lifecycle, bus routing |
| `agent.py`          | Spawns Claude Code agents via `claude-agent-sdk` with streaming              |
| `sandbox.py`        | Bubblewrap transport — wraps each agent in a bwrap namespace                 |
| `container.py`      | Docker launcher — re-execs jig inside container from host                    |
| `mcp_server.py`     | Per-agent MCP server (ticket CRUD, comments, memory, commits)                |
| `ticket_mcp.py`     | MCP tool handlers (create/update/list tickets, comments, questions)          |
| `prompt_builder.py` | Assembles agent prompts from role config, project context, skills            |
| `worktree.py`       | Git worktree lifecycle — create, commit, lint, cleanup                       |
| `ws_server.py`      | WebSocket server (port 9100) relaying events to TUI                          |
| `store/`            | JSONL-backed stores: tickets, comments, memory, message bus                  |

### Data flow

Orchestrator subscribes to the `"orchestrator"` bus topic. Agents communicate via MCP tools that publish to the bus. The WebSocket server relays events to the TUI. All state lives in `.jig/store/` as JSONL files.

## Tech Stack

- **Python 3.12+**, async throughout
- **pydantic v2** for models, **click** for CLI, **pyyaml** for config
- **claude-agent-sdk** for agent execution (SubprocessCLITransport)
- **websockets** for TUI connection
- **Textual** for the TUI (Python; daemon + client architecture)
- **Docker + bubblewrap** for sandboxing

## Commands

```bash
uv sync                        # Install deps
uv run pytest tests/ -v        # Run tests
uv run ruff check jig/         # Lint
jig build                      # Build Docker image
jig start                      # Start orchestrator (auto-builds Docker on first run)
jig start --no-docker          # Run without sandbox
jig story <ticket-id>          # Print merged thread+log story for a ticket
jig                            # Launch the Textual TUI (auto-starts daemon)
jig daemon start|stop|status   # Control the background daemon directly
jig --print "/<command>"       # One-shot non-interactive slash command
```

## Conventions

- Async by default for all I/O
- Type hints on all new code
- `ruff` for lint and format
- `pytest` with `asyncio_mode = "auto"`
- Conventional commits
- Stores are append-only JSONL — no SQL, no external services
- Agent roles defined as YAML in `.jig/roles/`
- Workflows defined as YAML in `.jig/workflows/`

## Sandboxing

Two-layer model: Docker (outer, host isolation) + bubblewrap (inner, per-agent isolation). Agents see a read-only root filesystem with their worktree writable at `/workspace`. The orchestrator runs outside bwrap.

Auth into Docker uses `CLAUDE_CODE_OAUTH_TOKEN` env var (from `claude setup-token`), not API keys or keychain extraction.

## Important Patterns

- The orchestrator subscribes to the `"orchestrator"` bus topic — events must be published there for it to react
- `ticket_mcp.py` publishes to both `tickets.{id}` (for subscribers) and `"orchestrator"` (for dispatch)
- Agents are stateless — all coordination happens through the bus and ticket store
- Each agent gets its own git worktree and MCP server instance
- `BwrapTransport` overrides `SubprocessCLITransport._build_command()` to prepend bwrap args
- The Docker container runs as non-root user `jig` (Claude Code refuses bypassPermissions as root)
- GPG signing is disabled in the container via `GIT_CONFIG_COUNT` env vars

## Workflow

Non-trivial work follows: problem statement → design (if warranted) → plan → code.

- Do not write a design doc until the problem statement exists and is approved.
- Do not write a plan until the design doc is approved.
- Do not start code until the plan is approved.
- For trivial work (one file, obvious change), skip straight to code.
- If unsure whether work is trivial, ask.

## Documentation

Documentation should be hard-wrapped at 120 characters, not 80

Docs are organized by feature/component under `feature-work/<feature>/`:

- `problem.md` — what we're solving and why
- `design.md` — how we're solving it (or `design-<aspect>.md` if multiple)
- `plan.md` — implementation plan (throwaway when work is done)
- `specs/` — component specs, EARS-style, one per file ONLY IF NECESSARY
- `notes.md` — scratch/working notes (not canonical)

Cross-cutting docs live at the top level:

- `docs/adrs/` — ADRs (numbered, e.g. `0001-<slug>.md`)
- `docs/runbooks/` — operational guides
- `docs/reference/` — long-lived reference material
- `feature-work/_templates/` — templates for new docs; copy these when creating

Full conventions in `feature-work/README.md` — read it before creating or modifying
docs if you haven't this session.

### Frontmatter

Every doc starts with frontmatter. Required on every doc:

```yaml
---
title: <human-readable title>
type: <problem | design | plan | spec | decision | runbook | reference | notes>
status: <see vocabulary below>
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Status vocabulary (fixed — do not invent new values):

- **problem, design, plan, notes, runbook, reference:**
  `draft | active | superseded | archived`
- **spec:** `draft | approved | implemented | verified | superseded`
- **decision:** `proposed | accepted | superseded | deprecated`

Per-type extras:

- **design:** `problem: <relative path to problem.md>`
- **plan:** `design: <relative path to design.md>`
- **spec:** `id: REQ-<AREA>-<NUM>`, optional `depends_on: [ids]`,
  optional `implements: [paths]`
- **decision:** `id: ADR-<NUM>`, `supersedes: []`, `superseded_by: null`
- **runbook:** `service: <service-name>`

### Rules

- New feature work starts with `feature-work/<feature>/problem.md`.
- Copy the appropriate template from `feature-work/_templates/` when creating a doc.
- Do not create docs outside this structure.
- Do not create generic-named docs (`notes.md` at top level, `thoughts.md`,
  `ideas.md`, `implementation.md`, `TODO.md`).
- Update the `updated` field whenever you edit a doc.
- Specs and ADRs are not modified without explicit instruction.
- When superseding a doc, set its `status: superseded` and fill `superseded_by`
  before creating the replacement.
- If unsure where a doc belongs or which type fits, ask before creating.

### Legacy note

`feature-work/archive/implementation-plans/` holds plans from before this
structure. New plans go in `feature-work/<feature>/plan.md`. Don't write into
the archive unless explicitly asked.


## Git handling

- All work runs on a worktree in `.worktrees/` at the project root unless instructed otherwise.
- Never `git push` without permission.
- Run other git operations freely.
- Commit messages: use the conventional commit skill.
- Never commit or push to `develop`, that branch is locked. You must open a PR

## PR Handling
- Submit a PR with:
  - Conventional commit style PR Title
  - A Problem/Fix section that explains the issue being addressed and how this PR resolves it
  - A summary of the PR's changes
  - Any areas that deserve special attention
  - A checklist of tasks to be completed before merging
  - Manual Test Steps
