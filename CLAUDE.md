# Jig

## Instructions for Claude
1. Follow the rules for creating documentation as defined in docs/README.md
2. Do not write anything to ./superpowers or any other Claude-specific location

Multi-agent orchestrator that spawns Claude Code agents across a codebase. Python async core + TypeScript/Bun TUI.

## Project Structure

```
jig/                  # Python package — orchestrator, agents, stores, MCP
tui/                  # TypeScript TUI (Gridland JSX on Bun)
tests/                # pytest suite (async)
templates/            # Project init templates
Dockerfile            # Container image (Python 3.12 + Node 22 + bwrap)
```

### Key modules

| Module | Purpose |
|--------|---------|
| `cli.py` | Click CLI: init, start, build, sync, validate, reset |
| `orchestrator.py` | Singleton async orchestrator — ticket dispatch, agent lifecycle, bus routing |
| `agent.py` | Spawns Claude Code agents via `claude-agent-sdk` with streaming |
| `sandbox.py` | Bubblewrap transport — wraps each agent in a bwrap namespace |
| `container.py` | Docker launcher — re-execs jig inside container from host |
| `mcp_server.py` | Per-agent MCP server (ticket CRUD, comments, memory, commits) |
| `ticket_mcp.py` | MCP tool handlers (create/update/list tickets, comments, questions) |
| `prompt_builder.py` | Assembles agent prompts from role config, project context, skills |
| `worktree.py` | Git worktree lifecycle — create, commit, lint, cleanup |
| `ws_server.py` | WebSocket server (port 9100) relaying events to TUI |
| `store/` | JSONL-backed stores: tickets, comments, memory, message bus |

### Data flow

Orchestrator subscribes to the `"orchestrator"` bus topic. Agents communicate via MCP tools that publish to the bus. The WebSocket server relays events to the TUI. All state lives in `.jig/store/` as JSONL files.

## Tech Stack

- **Python 3.12+**, async throughout
- **pydantic v2** for models, **click** for CLI, **pyyaml** for config
- **claude-agent-sdk** for agent execution (SubprocessCLITransport)
- **websockets** for TUI connection
- **Bun + Gridland** for the TUI (TypeScript JSX)
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
cd tui && bun install           # Install TUI deps
cd tui && bun run src/main.tsx  # Start TUI
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
