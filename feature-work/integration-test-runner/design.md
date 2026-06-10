---
title: Automated Integration Testing — Design
type: design
status: active
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-10
problem: ./problem.md
---

# Automated Integration Testing — Design

## Summary

Add a `jig eval run <project-id>` CLI command that chains the existing eval infrastructure into a
single zero-touch integration test: create an isolated temp directory, run `jig init --auto
--brief` (via `run_init` with `AutoPromptHandler`), start the orchestrator subprocess with a
dedicated WS port, then open a WebSocket connection to race three conditions — `project_complete`
event (success), a stall verdict from `StallDetector` (failure), or a 90-minute wall-clock timeout
(failure). On success, wait for `analysis_complete` to capture the analyzer output dir, run
`jig eval collect`, and copy analysis into the manifest dir. Exit non-zero on any failure or
tracer SKIP.

## Approach

### Stage 1 — Setup

`jig eval run hn-cli` creates a temp directory at `/tmp/jig-integration-<uuid>/`. The path is
logged immediately so the operator can inspect it if the run fails. A free port is picked for the
orchestrator's WebSocket server (to allow concurrent runs without port collision).

### Stage 2 — Init (unattended)

Calls `await run_init(name=<temp-dir>, brief_file=..., prompts=AutoPromptHandler())` directly
inside the command's async context — no subprocess. `run_init` is an async function; `eval run`
awaits it in the same event loop.

Brief path: `<jig_repo>/evals/projects/<project-id>/brief.md`.

### Stage 3 — Orchestrator subprocess

Starts the orchestrator via `subprocess.Popen`:

```
jig start --no-docker --path <temp-dir> --ws-port <picked-port>
```

The port is pinned so the WS client knows where to connect and concurrent runs don't share the
default 19100. Polls `<temp-dir>/.jig/run/daemon.addr` up to 15 seconds before opening the
WebSocket.

### Stage 4 — Race loop

Opens a WebSocket to `ws://127.0.0.1:<port>`, subscribes to all topics (`tickets`, `spec`,
`agents`, `events`, `prompts`), and runs three concurrent tasks:

1. **Completion watcher**: reads incoming frames; on `{"type": "event", "topic": "events",
   "kind": "project_complete"}`, sets a `project_complete` `asyncio.Event`. Also watches for
   `{"kind": "analysis_complete"}` on the same topic and captures `data["out_dir"]`.
2. **Stall detector**: feeds every frame into `StallDetector.observe()`; on a verdict from
   `StallDetector.check()`, returns failure. Uses default `StallThresholds`.
3. **Wall-clock timeout**: `asyncio.sleep(timeout_seconds)` — default 5400s (90 min).

`asyncio.wait(FIRST_COMPLETED)` cancels the remaining tasks when any one fires.

### Stage 5 — Success path

On `project_complete`:

1. Wait up to 60s for the `analysis_complete` event to arrive (capturing `data["out_dir"]`). If
   it times out, log a warning and proceed — the manifest is still written correctly.
2. Run `collect()` from `jig.eval.collector` directly with `project_id=<id>`,
   `tracer_cmd=["bash", "<jig_repo>/evals/projects/<id>/tracer.sh"]`, and a fresh `run_id`.
   This writes `evals/runs/<id>/<run-id>/manifest.yaml`.
3. Inspect the manifest's tracer result:
   - `exit_code != 0` → exit 4 (tracer FAIL).
   - `exit_code == 0` and `stdout` is empty → exit 4 with message
     `"tracer SKIP — artifact not on PATH, not a real pass"`.
4. If `analysis_complete` was received: copy `out_dir` contents into
   `evals/runs/<id>/<run-id>/analysis/`.
5. Delete the temp dir. Exit 0.

### Stage 6 — Failure path (stall or timeout)

On stall verdict or timeout:

1. SIGTERM the orchestrator subprocess and wait up to 10s for it to exit; SIGKILL if it doesn't.
2. Call `_kill_orphan_subprocesses(temp_dir, log)` from `jig.evals.watcher.run` to clean up
   in-flight `claude` processes (macOS only via `lsof`; no-op on Linux).
3. Log the stall signal and detail, or "wall-clock timeout" if that fired.
4. Leave the temp dir in place for post-mortem inspection.
5. Exit 1 (stall) or 2 (timeout) or 3 (init error).

### Cleanup

Temp dir is removed on clean success (unless `--keep` is passed) and left in place on any
failure.

## Interfaces

```
jig eval run <project-id> [--label TEXT] [--keep] [--timeout-minutes INT]
```

- `project-id`: must match a directory under `evals/projects/` containing `brief.md` and
  `tracer.sh` (`.py` support is forward-looking).
- `--label`: forwarded to `collect()`. Defaults to `"integration-<YYYYMMDD>"`.
- `--keep`: don't delete the temp dir on success.
- `--timeout-minutes`: override the 90-minute ceiling. Default 90.

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Tracer PASS — genuine success |
| 1 | Stall detected |
| 2 | Wall-clock timeout |
| 3 | Init error |
| 4 | Tracer FAIL or SKIP |

## Data model

No new persistent state. Each run produces:

```
evals/runs/<project-id>/<run-id>/
  manifest.yaml          # jig eval collect output (tickets, cost, tracer result)
  analysis/              # copied from out_dir in analysis_complete event
    analysis.md
    metrics.json
```

`<run-id>` is the 8-char uuid minted by `collect()`. The analyzer's internal run-id
(`<project_name>-<UTC-timestamp>`) is used only to locate the source dir; it is not exposed in
the output layout.

## Alternatives considered

### Simplest — Shell script wrapper

A `scripts/run_integration_test.sh` sequences `jig init --auto --brief`, backgrounds `jig start`,
polls the JSONL ticket store until all tickets are resolved or a timeout fires, then calls
`jig eval collect`. No new Python code.

Drawbacks: shell cannot natively consume the WebSocket `project_complete` event, so completion
detection degrades to polling the JSONL store (slow, racy against daemon writes). Process
management is fragile in bash. No integration with `StallDetector`'s per-signal heuristics. Port
collision under concurrent runs is unaddressed.

### Complete — New `jig eval run` CLI command (chosen)

As described above. Reuses `StallDetector`, `AutoPromptHandler`, `run_init`, and `collect()` as
Python library calls. Adds ~200 lines of new code, mostly the race loop and subprocess management.
Handles `project_complete` correctly, integrates stall detection, enforces the 90-minute ceiling,
catches tracer SKIP false-positives, and pins WS ports for concurrent-run isolation.

### Optimal — Configurable multi-project eval harness

A declarative `eval_config.yaml` per project (brief, tracer, profile, timeout, labels), a
scheduler, run queuing, parallelism. Essentially a lightweight CI system for jig.

Out of scope; hn-cli is the only supported project. The `jig eval run` command is already
parameterized by `project-id`, so this is an incremental extension if ever needed.

### Decision

**Complete**. Shell can't cleanly detect `project_complete` or integrate `StallDetector` — those
facts alone push above Simplest. The multi-project harness is explicitly out of scope. The
Complete approach adds a single focused command over the existing eval library with no new
abstractions.

## Risks

- **Daemon address delay**: the orchestrator's `daemon.addr` file may take longer than 15s on a
  slow machine. The poll timeout is configurable via `--timeout-minutes`; log clearly if it
  expires.
- **Analyzer timing**: `analysis_complete` arrives after `project_complete`; the 60s wait is a
  heuristic. If the analyzer takes longer, the analysis copy step is skipped and a warning is
  logged. The manifest is unaffected.
- **`_kill_orphan_subprocesses` is macOS-only**: uses `lsof`; no-op on Linux. On stall, the
  orchestrator subprocess is SIGTERMed directly regardless of platform, so the run still
  terminates cleanly. Orphan `claude` child processes may linger on Linux.
- **Concurrent runs**: each run pins its own WS port (auto-picked free port at startup). The
  `evals/runs/<id>/<run-id>/` output path is uuid-namespaced and collision-safe. The temp dirs
  are separate. Two runs are fully isolated.

## Out of scope

- Running integration tests against projects other than hn-cli.
- CI/CD scheduling or automated triggering.
- Interactive progress beyond what the orchestrator already emits to stdout.
- Parallel runs within a single `jig eval run` invocation.

## Open questions

*None — all resolved in problem.md.*

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
