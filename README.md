# Jig

Agent harness that orchestrates multiple Claude Code agents across a codebase.

## Installation

Install `jig` as a global CLI tool using [uv](https://docs.astral.sh/uv/):

```bash
uv tool install --from . jig
```

Or install in editable mode so code changes are picked up without reinstalling:

```bash
uv tool install --editable .
```

After installation, `jig` will be available on your `PATH`.

## Quick Start

### Initialize a project

```bash
cd /path/to/your/repo
jig init
```

Creates a `.jig/` directory with default agent types (spec, test, dev, review), a default workflow (spec → test → implement → review), and project config.

### Start the orchestrator

```bash
jig start
```

On first run, jig automatically builds a Docker image and launches the orchestrator inside it. Agents are sandboxed with bubblewrap for filesystem isolation. The TUI connects from the host via WebSocket.

To skip Docker during local development:

```bash
jig start --no-docker
```

### Monitor with TUI

In a separate terminal:

```bash
cd tui && bun run src/main.tsx
```

Connects to the WebSocket server (ws://localhost:9100) and displays real-time workflow progress, agent activity, and ticket state.

### Validate and clean up

```bash
jig validate --ticket-id <id>
```

Cleans up git worktrees after you've verified the output.

### Other commands

```bash
jig sync      # Sync new default agent types/workflows from installed jig version
jig reset     # Reset project to clean state (destructive)
jig build     # Build or rebuild the jig Docker image
```

## Architecture

- **Jig Core** (Python) — async orchestrator, agent lifecycle, file-backed message bus, MCP server, WebSocket server
- **Jig TUI** (TypeScript/Bun via Gridland) — real-time workflow monitoring

Agents communicate via a message bus exposed as a local MCP server. Each agent runs in an isolated git worktree. The orchestrator handles all git operations.

### Sandboxing

Jig uses a two-layer isolation model:

| Layer | Tool | Purpose |
|-------|------|---------|
| Outer | Docker | Isolates jig from the host. Provides the Linux environment for bubblewrap. Ensures a reproducible toolchain. |
| Inner | Bubblewrap | Isolates each agent from other agents. Restricts filesystem to the agent's worktree. |

When `jig start` runs on the host, it detects that it's not inside a container and re-execs itself inside Docker with the project directory volume-mounted at `/project`. Inside the container, each agent subprocess is wrapped in bubblewrap:

- **`/workspace`** (rw) — the agent's git worktree
- **`/`** (ro) — full container filesystem for system libs and binaries
- **`/tmp`** (tmpfs) — isolated temp directory
- **PID namespace** unshared — agents can't see other processes

The orchestrator itself runs outside bubblewrap and handles all git operations, ticket state, and inter-agent communication.

### Docker image

The jig Docker image (`Dockerfile`) includes:

- Python 3.12, Node.js 22, bubblewrap
- Claude Code CLI, ruff, gh CLI
- The jig package itself

Build manually with `jig build` or let `jig start` auto-build on first run. Override the image name with `JIG_DOCKER_IMAGE` env var.

### Volume mounts

When launching Docker, jig mounts:

- Project directory → `/project` (rw)
- `~/.claude/` → `/root/.claude` (rw, OAuth tokens)
- `~/.gitconfig` → `/root/.gitconfig` (ro)
- `~/.ssh/` → `/root/.ssh` (ro, for git-over-SSH)

## Development

```bash
# Install Python dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Install TUI dependencies
cd tui && bun install

# Build Docker image
jig build

# Run without Docker (no sandbox)
jig start --no-docker
```
