---
title: Eval Tracer Execution — Implementation Plan
type: plan
status: archived
owner: Brent Hoover
created: 2026-06-11
updated: 2026-06-11
design: ./design.md
---

# Eval Tracer Execution — Implementation Plan

## Overview

Three steps matching the design's three changes, ordered shared-surface-first: (1) two-token template substitution
(touches `_apply_template_files` + all three templates, fully unit-testable in isolation), (2) runner project-dir
split + build step + `collect(tracer_env=)` (the eval-side wiring), (3) E2E `jig eval run hn-cli` aiming for the
first green run. One PR; steps 1–2 are commits within it.

## Preconditions

- [x] problem.md approved (open questions resolved 2026-06-11)
- [x] design.md approved (one review round; all code claims verified)
- [x] Worktree `.worktrees/feat-eval-tracer-execution` on branch `feat/eval-tracer-execution` off origin/develop

## Steps

### 1. Two-token template substitution

**What:**

- `jig/init_workflow.py` `_apply_template_files` (`:2063`): add
  `dist_name = project_name.replace(" ", "-").lower()` and substitute `my-project` → `dist_name` alongside the
  existing `myproject` → `pkg_name` (order irrelevant — neither token is a substring of the other; path renaming
  untouched). Document the placeholder vocabulary in the function docstring.
- Template edits (`jig/defaults/project_templates/`):
  - `python-cli/pyproject.toml`: `name = "my-project"`, scripts key `my-project = "myproject.cli:app"`
  - `python-cli/README.md`: title, `uv run my-project --help`, and the mixed-token line 17 (`python -m myproject`
    stays; "the `my-project` entry point")
  - `python/pyproject.toml`, `fastapi/pyproject.toml`: `name = "my-project"`
  - `python/README.md`, `fastapi/README.md`: title only (fastapi's `uvicorn myproject.app:app` stays package token)
- Tests (`tests/test_apply_template_files.py`): new test scaffolding python-cli with `project_name="hn-cli"`
  asserting in the output: `name = "hn-cli"`, scripts key `hn-cli = "hn_cli.cli:app"`, package dir `src/hn_cli/`
  exists, README contains `uv run hn-cli` and no literal `myproject`/`my-project` remains anywhere in rendered
  text files. Existing tests must stay green unchanged (hyphen-less degenerate case).

**Why:** Shared-surface change first, provable in isolation before the eval wiring depends on it.

**Verify:** `uv run pytest tests/test_apply_template_files.py tests/test_template_smoke.py tests/test_init_workflow.py -q`
green; `uv run ruff check jig/ tests/` + `uv run ruff format --check` on touched files clean.

### 2. Runner project-dir split, build step, tracer env

**What:**

- `jig/eval/runner.py`:
  - `project_dir = temp_path / project_id` after mkdtemp (no explicit mkdir — `create_stub` handles it).
  - Re-key per the design table: `run_init(name=str(project_dir))`, `jig start --path project_dir`,
    `collect(project_dir, ...)`. All seven `_teardown_proc(proc, temp_path)` call sites become
    `_teardown_proc(proc, project_dir)` — do this via the parameter rename (`temp_path` → `project_path`) plus
    find/replace, treating "exactly 7 call sites" as a checklist count, not line addresses (the pre-edit line
    numbers in design.md drift as soon as the build step is inserted). `shutil.rmtree(temp_path)` and
    `RunResult(temp_dir=temp_path)` stay on the root. Log both paths at start
    (`log.info("eval: ... temp=%s project=%s ...")`).
  - Build step inside the success path's `try/except Exception` guard, before `collect()`: `uv sync` with
    `cwd=project_dir, capture_output=True, text=True, timeout=300`; non-zero → log ERROR with stderr tail,
    `_teardown_proc`, `return RunResult(outcome=EvalOutcome.TRACER_FAIL, temp_dir=temp_path)`. Catch
    `subprocess.TimeoutExpired` explicitly at the call: log, teardown, return TRACER_FAIL (a hung build must not
    crash the runner); dedicated test for the timeout path.
  - `tracer_env = {**os.environ, "PATH": f"{project_dir / '.venv' / 'bin'}{os.pathsep}..."}` passed via
    `collect(..., tracer_env=tracer_env)` — both changes land on the single call at `:314-320` (pre-edit).
- `jig/eval/collector.py`: `collect(..., tracer_env: dict[str, str] | None = None)`; thread `env=tracer_env` into
  the tracer `subprocess.run` (`:112-120`). Default `None` preserves inherit behavior for `jig eval collect`.
- Tests:
  - `tests/test_eval_runner.py`: the two success-path tests `test_run_eval_none_tracer_is_tracer_fail` and
    `test_run_eval_auto_responds_to_question_prompt` MUST gain `patch("jig.eval.runner.subprocess.run")`
    (returncode 0) — they currently only patch `collect`, so without this they shell out to a real `uv sync`
    against the fake tmp dir. (Verified: `subprocess` is module-level in runner.py, and patching `.run` doesn't
    conflict with the existing `.Popen` patches. The `run_init` assertions use `name=ANY` and survive the subdir
    change unchanged.)
  - New test: `uv sync` returning returncode 1 → TRACER_FAIL, `_teardown_proc` called, `temp_dir` set, `collect`
    NOT called, and the build stderr is logged at ERROR (assert via `caplog` — this is what satisfies problem.md's
    "error captured and visible" criterion, since no manifest exists on this path).
  - New test: `collect` receives `tracer_env` whose PATH starts with `<project_dir>/.venv/bin`.
  - **Create** `tests/test_eval_collector.py` (no collector test file exists today): one test that `tracer_env`
    reaches the tracer `subprocess.run` (patch and assert `env=`), one that omitting it passes `env=None`.

**Why:** Completes both root-cause fixes behind the seams step 1 established.

**Verify:** `uv run pytest tests/test_eval_runner.py tests/test_eval_responder.py tests/test_eval_collector.py -q`
green; full `uv run pytest tests/ -q` no regressions; ruff clean.

### 3. End-to-end validation

**What:** `uv run jig eval run hn-cli` from the worktree.

**Gate (deterministic, what this feature controls):**

- Generated project lands at `<temp>/hn-cli/` with `pyproject.toml` containing `name = "hn-cli"` and scripts key
  `hn-cli = "hn_cli.cli:app"` (inspect the kept temp dir or a `--keep` run).
- The manifest `tracer` block shows the tracer EXECUTED the CLI: stdout/stderr is PASS or a concrete FAIL — never
  `SKIP: hn-cli not in PATH`.

**Non-gating note:** if the agents happen to build working software, `outcome: success` / exit 0 is the first green
run. A tracer FAIL on real output bugs still passes this step's gate (the tracer ran the CLI); file such findings
separately.

**Verify:** Run output + manifest `tracer` block per the gate above; kept temp dir inspection on failure.

### 4. PR

**What:** Push `feat/eval-tracer-execution`, open PR per repo conventions. Flip problem.md and design.md
`status: draft → active` (bump their `updated:` fields), plan to `archived`, in the final commit. Address
roborev/Claude/greptile findings.

**Verify:** CI green; review findings addressed.

## Rollback

Revert the PR. Step 1 is degenerate for hyphen-less names (existing projects unaffected); step 2 is contained to
the eval path. No data migrations.

## Out of scope for this plan

- Investigating the hn-cli run's failed E2E-validation ticket
- New outcome codes, non-Python tracer support, fixture changes
- Any orchestrator/role/auto-responder changes

## Change log

- 2026-06-11: Initial draft (Brent Hoover)
- 2026-06-11: TimeoutExpired handling + test added to step 2 (roborev job 515)
- 2026-06-11: Review fixes — create test_eval_collector.py explicitly, named the two success-path tests needing the
  uv-sync patch, caplog assertion for build stderr, deterministic step-3 gate, line numbers demoted to checklist
  count (Brent Hoover)
