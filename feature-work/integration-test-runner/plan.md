---
title: Automated Integration Testing — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-10
design: ./design.md
---

# Automated Integration Testing — Implementation Plan

## Overview

We implement `jig eval run <project-id>` in two pieces that ship together in one PR: a
`jig/eval/runner.py` module containing all async pipeline logic, and a thin CLI command registered
under the existing `eval_group` in `jig/cli.py`. Tests cover the race loop, stall-detection poll,
subprocess teardown, and tracer SKIP detection in isolation. Order: core module (Step 1) + CLI
wiring (Step 2) together as one shippable unit, then tests (Step 3), then lint (Step 4).

## Preconditions

- [x] `design.md` approved
- [x] `collect()` signature: `(project_path, *, run_id, project_id, label, tracer_cmd)`
- [x] `project_complete` / `analysis_complete` WS envelope: `{"type": "event", "topic": "events",
  "kind": "...", "data": {...}}`
- [x] `jig start` options: `--path <dir>` and `--ws-port <N>` (not a positional arg)
- [x] `run_init` signature: `(*, name, force, console=None, prompts=None, brief_file=None,
  profile_name=None)` — `force` is required

## Steps

### 1 + 2. Add `jig/eval/runner.py` and wire `jig eval run` (ship together)

**What — `jig/eval/runner.py`:**

- `_free_port() -> int` — binds a socket on port 0, reads the assigned port, closes socket.
- `_wait_for_addr_file(path, timeout=15.0)` — polls `daemon.addr` until it appears (readiness
  gate only — the port for the WS connection is the separately pinned `_free_port()` value).
- `EvalOutcome` enum: `SUCCESS`, `STALL`, `TIMEOUT`, `INIT_ERROR`, `TRACER_FAIL`.
- `RunResult` dataclass: `outcome`, `stall_verdict`, `manifest_path`, `analysis_dir`, `temp_dir`.
- `async def _watch_completion(ws) -> tuple[dict, str | None]` — iterates WS frames; on
  `kind == "project_complete"` sets a flag; on `kind == "analysis_complete"` captures
  `data["out_dir"]`; returns when both received or `analysis_complete` times out (60s after
  `project_complete`).
- `async def _watch_stall(ws, detector: StallDetector) -> StallVerdict` — runs two concurrent
  sub-tasks mirroring `run.py`: a consumer that calls `detector.observe(frame)` for each frame,
  and a poll loop that calls `detector.check(now=time.monotonic())` every
  `detector.thresholds.poll_interval_seconds` and returns the verdict when one fires.
- `async def run_eval(project_id, *, label, keep, timeout_minutes, jig_repo) -> RunResult`:
  1. Validates `jig_repo / "evals/projects" / project_id / "brief.md"` exists; returns
     `INIT_ERROR` immediately if not.
  2. Creates temp dir, picks free port, logs both.
  3. Calls `await run_init(name=temp_dir, force=False, brief_file=..., prompts=AutoPromptHandler())`
     — wrapped in try/except; returns `INIT_ERROR` on exception.
  4. Starts `subprocess.Popen(["jig", "start", "--no-docker", "--path", str(temp_dir),
     "--ws-port", str(port)])`. Waits for addr file (15s timeout).
  5. Connects WS, subscribes to all topics, races `_watch_completion`, `_watch_stall`, and
     `asyncio.sleep(timeout_seconds)` via `asyncio.wait(FIRST_COMPLETED)`.
  6. **Success path** (completion wins): calls `collect()`, checks tracer result (exit_code != 0
     or empty stdout → `TRACER_FAIL`), copies analysis dir if `analysis_complete` arrived, removes
     temp dir (unless `keep=True`), returns `SUCCESS` or `TRACER_FAIL`.
  7. **Failure path** (stall or timeout wins): SIGTERMs the orchestrator subprocess, waits up to
     10s, SIGKILLs if still alive; calls `_kill_orphan_subprocesses(temp_dir, log)` from
     `jig.evals.watcher.run`; leaves temp dir in place; returns `STALL` or `TIMEOUT`.

**What — `jig/cli.py`:**

Add `@eval_group.command("run")` with `--label`, `--keep`, `--timeout-minutes` options. Calls
`asyncio.run(run_eval(...))` and maps `RunResult.outcome` to exit codes: SUCCESS→0, STALL→1,
TIMEOUT→2, INIT_ERROR→3, TRACER_FAIL→4 via `sys.exit`.

**Why:** All logic in `runner.py` keeps the CLI layer thin and makes the pipeline independently
testable. These two files are co-dependent and land in one PR — Step 1 alone would be unreachable
dead code.

**Verify:** `uv run jig eval run --help` shows command, options, and exit-code legend.

---

### 3. Tests

**What:** Add `tests/test_eval_runner.py`:

- `test_free_port_returns_usable_port` — binds to the returned port to confirm it was available.
- `test_watch_completion_returns_out_dir` — feeds mock WS frames with `project_complete` then
  `analysis_complete` into `_watch_completion`; asserts it returns the `out_dir`.
- `test_watch_completion_timeout_no_analysis` — feeds only `project_complete` with no follow-up;
  asserts it returns `(data, None)` after the 60s analysis wait (use tiny timeout override or
  mock `asyncio.sleep`).
- `test_watch_stall_poll_fires_on_silence` — constructs `StallThresholds(bus_silence_seconds=0.1,
  poll_interval_seconds=0.05)`, feeds no frames, asserts `_watch_stall` returns a
  `bus_silence` verdict within ~1s.
- `test_stall_teardown_sigterms_subprocess` — passes a mock `Popen` into the failure path of
  `run_eval`; asserts `terminate()` is called on stall/timeout, and `kill()` is called if the
  process doesn't exit within the grace period.
- `test_tracer_skip_is_tracer_fail` — calls the tracer-result check logic with
  `exit_code=0, stdout=""` and asserts the returned outcome is `TRACER_FAIL`.
- `test_run_eval_missing_project_returns_init_error` — calls `run_eval` with a non-existent
  project-id; asserts `RunResult.outcome == EvalOutcome.INIT_ERROR` with no subprocess started.

**Why:** The stall poll loop, subprocess teardown, and tracer SKIP detection are the
highest-risk behaviors; unit tests pin them without running a real orchestrator.

**Verify:** `uv run pytest tests/test_eval_runner.py -v` — all tests pass.

---

### 4. Lint and type-check

**What:** Run `uv run ruff check jig/eval/runner.py jig/cli.py` and
`uv run ruff format --check jig/eval/runner.py jig/cli.py`. Fix any issues. Run
`uv run pytest tests/ -v` to confirm no regressions.

**Why:** CI gate. Ruff format check is separate from lint check; both must pass.

**Verify:** All three commands exit 0, test count does not decrease.

## Rollback

Delete `jig/eval/runner.py`, remove the `eval run` command block from `jig/cli.py`, delete
`tests/test_eval_runner.py`. No schema changes or data migrations.

## Out of scope for this plan

- Support for `tracer.py` (Python tracers) — forward-looking per design.
- Configurable per-signal stall thresholds via CLI flags.
- CI/CD integration.

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
