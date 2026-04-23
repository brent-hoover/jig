# Per-Ticket Story Logs — Design

**Status:** Draft · 2026-04-23
**Owner:** Brent
**Supersedes:** nothing; first observability pass

## Goal

Reading the full story of what happened to a ticket — what the orchestrator
decided, which agents ran, which tools they called, how long each phase
took, why something failed — without `grep` magic or jumping between
three files.

Concretely, `jig story <ticket-id>` returns a time-ordered, human-
readable narrative for a single ticket. The same data is available
programmatically so the TUI can render a ticket-detail view in a
hosted deployment where no CLI is reachable.

## Non-goals (explicitly deferred)

- **Log rotation.** One file per `jig start` invocation is enough
  until a single run fills a disk.
- **Cross-ticket story.** Debugging *N* tickets at once is a separate
  need; `jig story` takes a single ticket id.
- **OpenTelemetry / external log backends.** Overkill for a
  single-user-at-a-time tool. Revisit if jig grows a multi-tenant
  hosted mode.
- **Redaction policy.** No secrets flow through logs today. If that
  changes, a redaction layer lands with the change that introduces
  the secret.
- **TUI rendering.** Deferred to a follow-on. This spec defines the
  library contract the TUI will consume.
- **Log level tuning per module.** Global DEBUG/INFO stays as-is.

## Why this, why now

The current log pipeline is fine for watching a single run live but
falls apart post-mortem:

| Source                       | Correlation with ticket                     |
| ---------------------------- | ------------------------------------------- |
| `.jig/logs/jig-<ts>.log`     | Text prefix `[role:tid8]` — agent lines only |
| Thread entries (JSONL)       | Ticket id is primary key                    |
| Agent SDK stream             | Wrapped into the text-prefix log lines      |

Orchestrator log lines about a ticket (scheduling decisions, phase
advances, handoff routing, dep resolution) don't share the
`[role:tid8]` prefix, so `grep <ticket-id>` misses them. The thread
holds the *what* but not the *why* — it records a `phase_run` event
but not the tool calls the agent made inside that phase.

## Architecture

Six layers, ordered by dependency. Each is useful on its own; the
story view at the top needs all of them.

```
┌─────────────────────────────────────────────────┐
│  F. TUI view (future — not in this spec)        │
├─────────────────────────────────────────────────┤
│  E. `jig story` CLI                             │  thin shell over D
├─────────────────────────────────────────────────┤
│  D. `jig.story` library                         │  merge + render
├─────────────────────────────────────────────────┤
│  C. SystemEvent additions: phase_start,         │  thread-carried
│     phase_end, agent_run                        │  timing
├─────────────────────────────────────────────────┤
│  B. Richer agent-output capture                 │  tool results, inputs
├─────────────────────────────────────────────────┤
│  A. Structured JSON logs + contextvar           │  correlation
│     correlation fields                          │
└─────────────────────────────────────────────────┘
```

### Layer A — structured logs with contextvars

**Problem:** correlation is a textual prefix only, and only agent
lines get it.

**Solution:** `contextvars.ContextVar` slots for `ticket_id`, `phase`,
`role`, `agent_id`, `spawn_reason`. A `logging.Filter` copies the
current values onto every `LogRecord`. A `JsonFormatter` writes them
as a JSON object per line.

- **Where set:** at the boundaries where a per-ticket code path begins.
  - `Orchestrator._handle_schedule(tid)` — sets `ticket_id`.
  - The per-phase loop inside the orchestrator — sets `phase`.
  - `run_agent(ctx, ...)` — sets `role`, `agent_id`, `spawn_reason`.
  - **MCP tool handlers.** The MCP server runs in-process (stdio
    transport, same event loop) but each tool call is a new async
    task initiated by the SDK, so it does NOT automatically inherit
    the ContextVars set by the orchestrator task that spawned the
    agent. `create_agent_mcp_server(...)` already closes over
    `agent_role`, `phase_name`, and the ticket context — wrap each
    tool handler so it sets the four ContextVars at entry and
    resets them at exit. This is the one place the design requires
    a mechanical wrapper; everything else relies on natural async
    ContextVar inheritance.
- **Where used:** one logging `Filter` attached to every handler;
  every record gets the four fields (or `None` if unset).
- **Unsetting:** use `contextvars.Token` / `reset()` in `finally`
  blocks to avoid leaking a ticket context into the next task on
  the same asyncio loop.

**File layout:**

- `.jig/logs/jig-<ts>.jsonl` — one JSON object per line, written by
  the FileHandler. Replaces the text log file. Schema:

  ```json
  {
    "ts": "2026-04-23T10:14:22.314Z",
    "level": "INFO",
    "logger": "jig.orchestrator",
    "msg": "advancing ticket abc12345 to phase gated",
    "ticket_id": "abc12345...",
    "phase": "gated",
    "role": null,
    "agent_id": null,
    "spawn_reason": null,
    "extra": { ... optional structured payload }
  }
  ```

- Console handler stays human-readable for live watching. Format:
  `%(asctime)s %(levelname)-7s %(name)s [%(ticket_short)s] %(message)s`
  where `ticket_short` is the first 8 chars of `ticket_id` (or empty
  brackets `[        ]` when unset — fixed width keeps columns
  aligned). The filter computes both `ticket_id` (full) and
  `ticket_short` (truncated) on each record.

**Module boundary:** extract the logging setup from `jig/cli.py`
into `jig/logging_setup.py`. `cli.py` imports and calls
`configure_logging(project_path)`. Keeps the CLI file from being the
junk drawer it's becoming.

### Layer B — richer agent-output capture

**Problem:** tool inputs are truncated to a TUI-friendly detail,
tool results are dropped entirely. Post-mortem, you can see the
agent called `Bash` but not what command ran or what it produced.

**Solution:** inside `run_agent`'s streaming loop, log the full
tool input and the tool result at DEBUG, keep the short
`_sanitize_for_tui` line at INFO (for live watching).

- `AssistantMessage` + `ToolUseBlock` →
  - INFO `tool: <name> <detail>` (unchanged)
  - DEBUG `tool_input: <json.dumps(block.input)>`
- `UserMessage` + tool_result block → new handling
  - DEBUG `tool_result: <id=block.tool_use_id, text=...>`
  - Result text is NOT sanitized for the TUI — it goes to the log
    raw (JSON formatter will escape as needed). Truncate at 32KB;
    log a separate DEBUG `tool_result_truncated` with the full
    length for longer ones.
- `ResultMessage` — promote to layer C (see below).

**Emitter note:** the existing `JigEvent` emit-to-TUI path stays as
is — it's the live-watch stream, not the post-mortem story.

### Layer C — SystemEvent additions for timing

**Problem:** the thread tells you *what* happened but not *when* or
*how long*. `phase_run` exists but doesn't carry start/end times
separately.

**Solution:** extend the `SystemEvent.event_type` literal union with
three values and post them at the right lifecycle boundaries.

- `phase_start` — posted by `Orchestrator` just before invoking the
  primary agent for a phase. Fields: `content` = role name,
  `payload.phase`, `payload.spawn_reason`.
- `phase_end` — posted by `Orchestrator` after the phase's handoff
  resolves (accepted, rejected, or failed). Fields: `content` =
  outcome (`"accepted" | "rejected" | "failed" | "timeout"`),
  `payload.duration_ms`, `payload.phase`.
- `agent_run` — posted by `run_agent` when the SDK emits a
  `ResultMessage`. Fields: `payload.num_turns`, `payload.duration_ms`,
  `payload.role`, `payload.spawn_reason`, `payload.result_preview`
  (first 500 chars of `final_text`, sanitized).

`phase_run` stays as a successful-completion marker (existing
consumers depend on it); `phase_start` / `phase_end` / `agent_run`
are additive.

**Why SystemEvents and not just log lines:** the story renderer has
to work in a hosted deploy where the log file isn't directly
reachable by the TUI. The thread store is reachable (it's already
the source of truth the TUI reads). Carrying timing on the thread
means timing survives even if the log file is rotated/deleted.

### Layer D — `jig.story` library

**Problem:** the TUI needs to render the same narrative as the CLI,
but without shelling out to `jig`.

**Solution:** a library module with a clean API. CLI wraps it; TUI
(eventually) calls it via the existing WebSocket server.

- **Module:** `jig/story.py`
- **Public API:**

  ```python
  from dataclasses import dataclass
  from datetime import datetime
  from enum import Enum
  from pathlib import Path

  class StorySource(str, Enum):
      thread = "thread"    # Thread entry (Handoff, Question, ...)
      log = "log"          # Log line from .jig/logs/jig-*.jsonl

  @dataclass(frozen=True)
  class StoryEvent:
      ts: datetime
      source: StorySource
      kind: str              # thread: entry kind; log: logger name
      level: str             # "INFO"|"DEBUG"|... (log); "info" for thread
      message: str           # one-line summary, ready to print
      ticket_id: str
      phase: str | None
      role: str | None
      raw: dict              # original record (thread entry dict OR log dict)

  async def build_story(
      ticket_id: str,
      *,
      project_path: Path,
      threads,               # ThreadStore
      include_children: bool = False,
      since: datetime | None = None,
  ) -> list[StoryEvent]:
      """Return a time-ordered list of events for a ticket.

      Merges:
        - thread entries for ``ticket_id``
        - log lines from every ``.jig/logs/jig-*.jsonl`` under
          ``project_path`` whose ``ticket_id`` field matches
        - (if ``include_children``) the same for child tickets

      Sorted ascending by ``ts``.
      """
  ```

- **Rendering:** `StoryEvent.message` is the human-readable line
  the CLI/TUI displays. Each entry kind has a simple formatter:

  ```
  thread/handoff        → "📦 HANDOFF phase={phase} by={author} outputs={n}"
  thread/question       → "❓ QUESTION target={target}: {question[:80]}"
  thread/escalation     → "🚨 ESCALATION target={target}: {reason}"
  thread/system_event   → dispatch by event_type (phase_start, etc.)
  log/INFO              → "{logger}: {msg}"
  log/DEBUG             → dimmed in terminal rendering
  ```

  (Emoji/unicode: strictly ASCII fallback when `JIG_NO_UNICODE=1`.)

- **Log file scan:** `jig.story` iterates `.jig/logs/jig-*.jsonl`,
  reads each line as JSON, filters by `ticket_id`. Since we only
  keep logs from `jig start` invocations and one file per
  invocation, the usual case is a single file and a linear scan —
  no indexing needed. If perf becomes an issue, add a sidecar
  `.jig/logs/index/<ticket-id>.jsonl` later.

- **Follow mode:** `build_story` has a sibling `stream_story` that
  yields new `StoryEvent`s as they land. Implementation:
  - New thread entries come in via the `EventEmitter` the
    orchestrator already publishes to (ws_server consumes it).
  - New log lines come from tailing the current log file.
  - The stream merges both and yields in timestamp order with a
    500 ms watermark (to handle out-of-order arrival between the
    two sources).

### Layer E — `jig story` CLI command

**Problem:** exercise the library; give me a debugging tool.

**Solution:** new Click command in `jig/cli.py`:

```
jig story <ticket-id> [--json] [--follow] [--include-children]
                      [--since <ISO8601>] [--level DEBUG|INFO]
```

- Default: pretty-print `StoryEvent.message` per line with
  timestamp (HH:MM:SS.mmm) and elapsed-since-first-event column.
  Phase boundaries get a visual separator.
- `--json` — dump `StoryEvent` as JSON per line. Stable schema for
  piping to `jq`.
- `--follow` — tail mode (uses `stream_story`).
- `--include-children` — merge child-ticket stories.
- `--since <iso>` — start from a wall-clock time (useful when the
  same ticket id has been through multiple runs).
- `--level` — floor; DEBUG = everything, INFO = skip DEBUG log
  lines (but keep all thread entries).

**Pipeable:** output goes to stdout, exits non-zero if the ticket
doesn't exist. No ANSI colors when stdout isn't a TTY.

### Layer F — TUI integration (deferred)

Not in this spec. The contract the TUI will consume:

- `ws_server.py` adds a handler for `{"type": "story.request",
  "ticket_id": "..."}` that calls `build_story` and streams back
  the events.
- A `story.subscribe` variant does the same but keeps the
  connection open and pushes new events (backed by `stream_story`).

Adding that handler is ~30 lines once the library is in place; it
doesn't change the library's API and doesn't need to be designed
up front.

## Data flow

For a single ticket going through two phases with one bounce:

```
┌──────────────┐     JSONL append       ┌──────────────────────────┐
│ Orchestrator │──────────────────────► │ .jig/store/threads/*.    │
└──────┬───────┘                        │ jsonl                    │
       │ contextvar:ticket_id set       └──────────▲───────────────┘
       │                                           │
       ▼                                           │ build_story reads
┌──────────────┐     JSON line append              │
│ logging      │──────────────────────► ┌──────────┴───────────────┐
│ FileHandler  │                        │ .jig/logs/jig-<ts>.jsonl │
└──────┬───────┘                        └──────────────────────────┘
       │
       ▼                                     build_story merges
┌──────────────┐                                    │
│ run_agent    │──── JigEvent ────────► EventEmitter│(live view only,
│ emits        │                                    │ not persisted)
└──────────────┘                                    ▼
                                            WebSocketServer → TUI
```

The post-mortem story is reconstructed entirely from the two
persisted stores (thread JSONL + log JSONL). The live event stream
is separate and ephemeral.

## What happens to the old `.log` file

Replaced, not kept alongside. The JSON file is both more useful
(queryable) and the same information. If someone wants to read it
raw, `jq -r '"\(.ts) \(.level) \(.logger): \(.msg)"'` reproduces
the old format.

Console output is unchanged.

## Testing

Per-layer, TDD:

- **Layer A:** `tests/test_logging_correlation.py` — spawn an
  `Orchestrator`, emit a log record from inside a per-ticket path,
  assert the written JSONL line carries the expected `ticket_id`.
- **Layer B:** extend the Phase 5 harness tests (the ones that
  fake `run_agent`) to assert that real `run_agent` — mocked SDK
  stream — writes the expected DEBUG records. Unit-test
  `_format_tool_result` / `_format_tool_input` helpers directly.
- **Layer C:** assert each of the three new SystemEvents lands on
  the thread with the expected fields. Should slot into the
  existing Phase 5 E2E tests as additional assertions on the same
  thread.
- **Layer D:** unit tests with synthetic thread + log fixtures;
  assert ordering, filtering, include_children, since. Integration
  test that runs a real orchestrator and calls `build_story`
  against it.
- **Layer E:** CLI tests with `click.testing.CliRunner` against a
  real project fixture.

Coverage target: every new public function. No new mutation
testing unless something looks brittle.

## Implementation order

Layers build on each other; do them in order A → B → C → D → E.

1. **A (structured logs).** Replace text log with JSONL + contextvars.
   Existing tests keep passing because console output is unchanged.
2. **B (agent capture).** Additive; doesn't change existing
   behavior, just adds DEBUG lines.
3. **C (SystemEvents).** Additive to the thread. Existing consumers
   of `phase_run` are unaffected.
4. **D (library).** Greenfield module. No wiring yet.
5. **E (CLI).** Thin wrapper; ~50 lines.

Layer F (TUI) is a separate spec/plan once the library is exercised
through the CLI.

## Open questions

_None left after the brainstorm. If any surface during planning,
they go here with a resolution before the implementation plan is
finalized._

## Alternatives considered

- **Per-ticket log files (`.jig/logs/ticket-<id>.log`).**
  Duplicate writes, harder to keep in sync, and splits the answer
  to "what was happening in the orchestrator overall" across *N*
  files. Rejected in favor of filter-at-read over a single
  structured log.
- **OpenTelemetry / external backend.** Too much operational
  weight for a single-user tool with no multi-tenant ambition.
  Deferred.
- **Log everything through the bus instead of stdlib logging.**
  Conflates "events the orchestrator must react to" with "records
  humans will read later." Keeping them separate means the bus
  stays lean.
- **Render story in the TUI first, CLI later.** Rejected: the CLI
  is the debugging tool you reach for when something is broken,
  and it's cheaper to iterate on the format in text before
  committing to a TUI layout.
