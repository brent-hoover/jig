# Jig

Agent harness that orchestrates multiple Claude Code agents across a codebase.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (agents run inside a sandboxed container)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Bun](https://bun.sh/) (for the TUI)
- A Claude subscription — jig uses OAuth, not API keys

## Installation

```bash
uv tool install --from . jig
```

Or editable for development:

```bash
uv tool install --editable .
```

## Setup

### 1. Generate an auth token

Jig needs a long-lived OAuth token to authenticate Claude Code agents inside Docker.

```bash
claude setup-token
```

Export the token in your shell (e.g. in `~/.zshenv` or a secrets file):

```bash
export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...'
```

### 2. Initialize a project

```bash
cd /path/to/your/repo
jig init
```

Creates a `.jig/` directory with default agent types (spec, test, dev, review), a default workflow, and project config.

### 3. Install the TUI

```bash
cd tui && bun install
```

## Usage

### Start the orchestrator

```bash
cd /path/to/your/repo
jig start
```

On first run, jig automatically builds a Docker image and launches the orchestrator inside it. Agents are sandboxed with bubblewrap for per-agent filesystem isolation.

To skip Docker (no sandbox, for local development):

```bash
jig start --no-docker
```

### Monitor with TUI

In a separate terminal:

```bash
cd tui && bun run src/main.tsx
```

Connects to `ws://localhost:9100` and displays real-time workflow progress, agent activity, and ticket state.

### Other commands

```bash
jig build     # Build or rebuild the Docker image
jig sync      # Sync new default agent types/workflows from installed jig version
jig validate --ticket-id <id>  # Clean up worktrees after verifying output
jig reset     # Reset project to clean state (destructive)
```

## Architecture

- **Jig Core** (Python) — async orchestrator, agent lifecycle, file-backed message bus, MCP server, WebSocket server
- **Jig TUI** (TypeScript/Bun via Gridland) — real-time workflow monitoring

Agents communicate via a message bus exposed as a local MCP server. Each agent runs in an isolated git worktree. The orchestrator handles all git operations.

### Sandboxing

Jig uses a two-layer isolation model:

| Layer | Tool | Purpose |
|-------|------|---------|
| Outer | Docker | Isolates jig from the host. Provides Linux for bubblewrap. Reproducible toolchain. |
| Inner | Bubblewrap | Isolates each agent from other agents. Restricts filesystem to the agent's worktree. |

When `jig start` runs on the host, it re-execs itself inside Docker with the project directory volume-mounted at `/project`. Inside the container, each agent subprocess is wrapped in bubblewrap:

- **`/workspace`** (rw) — the agent's git worktree
- **`/`** (ro) — full container filesystem for system libs and binaries
- **`/tmp`** (tmpfs) — isolated temp directory
- **PID namespace** unshared — agents can't see other processes

The orchestrator itself runs outside bubblewrap and handles all git operations, ticket state, and inter-agent communication.

### Container details

The Docker image includes Python 3.12, Node.js 22, bubblewrap, Claude Code CLI, ruff, and gh. Build manually with `jig build` or let `jig start` auto-build on first run. Override the image name with `JIG_DOCKER_IMAGE` env var.

Volume mounts:

| Host | Container | Mode |
|------|-----------|------|
| Project directory | `/project` | rw |
| `~/.claude/` | `/home/jig/.claude` | rw |
| `~/.claude.json` | `/home/jig/.claude.json` | rw |
| `~/.gitconfig` | `/home/jig/.gitconfig` | ro |
| `~/.ssh/` | `/home/jig/.ssh` | ro |

Auth is passed via `CLAUDE_CODE_OAUTH_TOKEN` env var (not volume-mounted — macOS Keychain tokens can't be shared with Linux containers).

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
