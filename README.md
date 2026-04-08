# Jig

Agent harness that orchestrates multiple Claude Code agents across a codebase.

## Quick Start

### Initialize a project

```bash
jig init --path /path/to/your/repo
```

Creates a `.jig/` directory with default agent types (spec, test, dev, review), a default workflow (spec → test → implement → review), and project config.

### Start a workflow

```bash
jig start --issue-id my-feature --title "Add authentication"
```

Creates the issue and runs the full workflow. The orchestrator walks each phase sequentially, spawning a Claude Code agent in an isolated git worktree for each.

To resume a paused or interrupted workflow:

```bash
jig start --issue-id my-feature
```

### Check status

```bash
jig status
```

### Monitor with TUI

In a separate terminal:

```bash
cd tui && bun run src/main.tsx
```

Connects to the WebSocket server (ws://127.0.0.1:9100) and displays real-time workflow progress.

### Validate and clean up

```bash
jig validate --issue-id my-feature
```

Cleans up git worktrees after you've verified the output.

## Architecture

- **Jig Core** (Python) — async orchestrator, agent lifecycle, file-backed message bus, MCP server, WebSocket server
- **Jig TUI** (TypeScript/Bun via Gridland) — real-time workflow monitoring

Agents communicate via a message bus exposed as a local MCP server. Each agent runs in an isolated git worktree. The orchestrator handles all git operations.

## Development

```bash
# Install Python dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Install TUI dependencies
cd tui && bun install
```
