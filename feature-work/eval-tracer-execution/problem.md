---
title: Eval Tracer Execution — Problem Statement
type: problem
status: active
owner: Brent Hoover
created: 2026-06-11
updated: 2026-06-11
---

# Eval Tracer Execution — Problem Statement

## Context

With the auto-responder merged (PR #158), `jig eval run hn-cli` completed its first unattended end-to-end run on
2026-06-11: planning gate auto-answered, 3 of 4 dev tickets resolved, `project_complete` received, manifest written.
The outcome was still `tracer_fail`, because the tracer skipped:

```
exit_code: 0
stdout: (empty)
stderr: SKIP: hn-cli not in PATH (project not yet built)
```

Empty tracer stdout is deliberately treated as TRACER_FAIL (`_check_tracer_outcome`, `jig/eval/runner.py`) so a
skipped tracer can't masquerade as a pass. The tracer is the eval's only check that the generated software actually
works; until it can execute the built CLI, every eval run ends in `tracer_fail` regardless of output quality.

## Problem

The tracer (`evals/projects/hn-cli/tracer.sh`) needs `hn-cli` on PATH. That can never happen today, for two
independent reasons (both verified against the kept run dir `jig-integration-ln02u9gs`):

1. **The generated CLI is not named `hn-cli`.** The python-cli scaffold derives all naming from the project
   directory: `_apply_template_files` (`jig/init_workflow.py:2063`, called with `project_name=project_path.name` at
   `:2196`) substitutes the template token `myproject` with `pkg_name = name.replace("-", "_").lower()` in every
   file — including the `[project.scripts]` key. The eval runner creates the project at
   `tempfile.mkdtemp(prefix="jig-integration-")`, so the generated entry point was
   `jig_integration_ln02u9gs = "jig_integration_ln02u9gs.cli:app"`. Two layers to this:
   - The brief's tool name (`hn-cli top --limit N`, stated throughout) never reaches the scaffold.
   - Even a directory named `hn-cli` would produce a script named `hn_cli` (single underscored token used
     everywhere), still not matching the tracer's `command -v hn-cli`.
   Dev agents implemented features in the scaffolded package and did not rename the entry point to match the brief.
2. **Nothing builds or installs the project before the tracer runs.** `collect()` runs the tracer with
   `cwd=project_path` and the inherited environment (`jig/eval/collector.py:112-120`). The finished project has a
   `uv.lock` but no `.venv` — no `uv sync` happens after the agents finish, and no venv bin dir is put on PATH.

Likely the same gap inside the run: the E2E-validation ticket's `validate` phase went `blocked` twice
(check-failure-fallback) and the ticket finished `failed` — the one ticket whose job was to run the tool.

## Simplest possible solution

Fix each cause at its narrowest point:

1. Scaffold into a subdirectory named after the eval project id (`<mkdtemp>/hn-cli`) so naming derives from the
   brief's tool name instead of the random temp dir. This alone yields script `hn_cli` (the scaffold uses the
   underscored token everywhere), so either:
   - the substitution learns two tokens — hyphenated name for `[project] name` and the script key, underscored for
     package paths (a small `_apply_template_files` change that fixes the same wart for every init: a project dir
     `my-tool` currently gets a `my_tool` command), or
   - eval fixtures accept the underscored name (tracer checks `hn_cli`) — no scaffold change, but the eval then
     tests a tool name the brief never mentions.
2. Before invoking the tracer, run `uv sync` in the project dir and prepend `<project>/.venv/bin` to the tracer's
   PATH (a build step in the runner just before `collect()`, or an `env=` change at the tracer `subprocess.run`).

With both in place the tracer script stays unchanged — `command -v hn-cli` finds the venv entry point.

## Complications considered

- **Scale**: N/A — one build per eval run, seconds of wall clock against a multi-minute run.
- **Concurrency**: N/A — the build runs after the orchestrator subprocess is done writing the project, before the
  tracer; strictly sequential.
- **Failure modes**:
  - The generated project fails to build (`uv sync` errors — bad pyproject, unresolvable deps). That is itself an
    eval signal: the agents produced a non-installable project. It must surface as TRACER_FAIL with the build error
    captured, not crash the runner.
  - Agents rename the project or break the scripts entry mid-run. Same as above — build or tracer fails loudly with
    captured output.
  - The dir-name approach assumes the scaffold's name derivation stays directory-based. If a template later reads
    the name from the brief instead, the subdirectory trick becomes redundant but harmless.
- **Cross-cutting policies**: `uv sync` downloads dependencies from PyPI — network access in the eval path. The
  agents already install deps during the run (uv.lock exists), so this adds no new class of egress.
- **Temp-dir hygiene**: today the project dir IS the mkdtemp root (`run_eval` inits straight into it,
  `runner.py:193-204`), so cleanup, orphan reaping, and `temp_dir` reporting are all keyed on one path. Any
  solution that separates "project dir" from "temp root" must keep cleanup and `_kill_orphan_subprocesses` keyed
  correctly and the failure-path keep-temp-dir behavior intact.

## Constraints

- No changes to agent role prompts or the orchestrator.
- Eval project ids must be limited to chars that slugify cleanly through the scaffold's existing rules
  (`replace("-", "_").replace(" ", "_").lower()`).
- The tracer contract stays: SKIP/empty stdout = TRACER_FAIL; tracer scripts stay self-contained bash.
- `uv` is the package manager (already required by the generated projects' workflow).

## Requirements

- After a completed orchestration, the tracer can execute the generated CLI by the name the brief uses.
- A build/install failure surfaces as TRACER_FAIL with the build error visible in the manifest (or runner log), not
  as a runner crash.
- The runner's failure-path behavior (keep temp dir, teardown subprocess) is unchanged.
- `jig eval run hn-cli` on a well-behaved run produces `outcome: success`, exit code 0 — the first green run.

## Non-goals

- Fixing why the E2E-validation ticket failed inside the run (agent/check behavior is its own investigation).
- Generalizing tracers beyond Python/uv projects (the only template in use is python-cli).
- Adding new outcome codes — build failure maps onto the existing TRACER_FAIL semantics.
- Changing how `mkdtemp` temp roots are created, cleaned, or reaped.

## Success criteria

- `jig eval run hn-cli` reaches the tracer with `hn-cli` resolvable, and the tracer emits PASS/FAIL (not SKIP).
- A deliberate broken-build case (e.g. corrupt pyproject in a scratch copy) lands as TRACER_FAIL with the error
  captured and visible in the manifest or runner log.
- The generated project's command name matches what the brief advertises (`hn-cli`), verifiable by inspecting the
  generated `pyproject.toml` in a kept run dir.
- Unit tests exist for whatever seams the design introduces; existing runner/collector tests stay green.

## Open questions

All resolved 2026-06-11 (operator decisions):

- [x] **Name source**: `run_eval` scaffolds into a subdirectory named after the eval project id
      (`<mkdtemp>/hn-cli`). No new parameters through the shared init path; runner bookkeeping splits "temp root"
      (cleanup/reaping) from "project dir" (init/start/collect target).
- [x] **Script name**: two-token substitution in `_apply_template_files` — hyphenated dist name for
      `[project] name`, the `[project.scripts]` key, and README command invocations; underscored package name for
      package dirs, imports, `python -m`, hatch paths, and the scripts target. Fixes the same wart for every init
      run in a hyphenated directory, not just evals.
- [x] **Build owner**: the runner, after the race settles and before `collect()` — `collect()` stays
      side-effect-free on the project; a build failure short-circuits to TRACER_FAIL with captured output.
- [x] ~~Does scaffold slugification produce `hn-cli` as the script name?~~ Verified: no. `_apply_template_files`
      uses one underscored token everywhere, so a dir named `hn-cli` yields script `hn_cli` — which is why the
      two-token substitution above is needed.
- [x] **Audit for script == package assumptions**: done 2026-06-11. Nothing in jig's tests depends on it
      (`tests/test_apply_template_files.py` uses hyphen-less `example_project`, where the names coincide). The
      hyphenated-name sites are `[project] name`, the scripts key, and the user-facing strings: README title and
      command invocations (`uv run myproject --help`) and the Typer help text in the template `cli.py`; package
      dirs, imports, `python -m`, hatch paths, and scripts targets stay underscored. Token mechanics (second placeholder vs
      per-file substitution rules) is a design detail.

## Change log

- 2026-06-11: Initial draft (Brent Hoover)
- 2026-06-11: Audit note expanded with user-facing placeholder sites (README title, Typer help text) per
  roborev job 513 (Brent Hoover)
- 2026-06-11: Resolved all open questions — subdir name source, two-token substitution, runner-owned build step;
  audit found no script==package dependencies (Brent Hoover)
