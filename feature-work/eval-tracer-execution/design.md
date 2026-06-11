---
title: Eval Tracer Execution — Design
type: design
status: draft
owner: Brent Hoover
created: 2026-06-11
updated: 2026-06-11
problem: ./problem.md
---

# Eval Tracer Execution — Design

## Summary

Three contained changes, one per root cause plus the seam between them: (1) `run_eval` scaffolds the project into a
subdirectory named after the eval project id, so the brief's tool name drives all scaffold naming; (2) the project
templates adopt a second placeholder (`my-project`) for the distribution/script name, so a hyphenated directory
yields a hyphenated command (`hn-cli`, not `hn_cli`); (3) after the race settles successfully, the runner runs
`uv sync` in the project dir and hands `collect()` a tracer environment with the project's `.venv/bin` prepended to
PATH. The tracer script and its SKIP-is-fail contract are unchanged; a build failure becomes TRACER_FAIL with the
error logged.

## Approach

### 1. Project directory split (`jig/eval/runner.py`)

Today `run_eval` inits straight into the mkdtemp root, so one path serves as temp root, project dir, and reaping
key. Split it:

```python
temp_path = Path(tempfile.mkdtemp(prefix="jig-integration-"))   # unchanged: cleanup root, RunResult.temp_dir
project_dir = temp_path / project_id                            # new: init/start/collect/reap target
```

(No explicit `mkdir` needed — `run_init` → `create_stub` already does `mkdir(parents=True, exist_ok=True)`, and
`classify_directory` treats a missing/empty dir as FRESH.)

Path-by-path consequences (every current use of `temp_path` audited):

| Use | Now keyed on |
|---|---|
| `run_init(name=...)` | `project_dir` — scaffold derives `pkg_name` from `project_dir.name` (`hn-cli` → `hn_cli`) |
| `jig start --path` | `project_dir` |
| `collect(...)` | `project_dir` |
| `_teardown_proc` / `_kill_orphan_subprocesses` | `project_dir` — the reaper scans `<path>/.jig/worktrees` (`jig/evals/watcher/run.py:62`), which now lives under the project dir. **All seven** `_teardown_proc(proc, temp_path)` call sites flip (`runner.py:237, 298, 311, 336, 353, 372, 384`); rename the parameter `temp_path` → `project_path` so the contract is obvious |
| `shutil.rmtree` on clean success | `temp_path` — removes project and anything else under the root |
| `RunResult.temp_dir` / log lines | `temp_path` — operators inspect the root; the project sits one level down at a predictable name |

`_teardown_proc(proc, project_dir)` is the one signature-visible change inside the module; its parameter rename
(`temp_path` → `project_path`) keeps the reaper contract obvious.

### 2. Two-token template substitution (`jig/init_workflow.py` + templates)

`_apply_template_files` gains a dist-name token alongside the package token:

```python
pkg_name = project_name.replace("-", "_").replace(" ", "_").lower()   # unchanged
dist_name = project_name.replace(" ", "-").lower()                    # keeps hyphens
```

Templates use two explicit placeholders:

- `myproject` — package contexts: package dirs, imports, `python -m myproject`, `packages = ["src/myproject"]`,
  scripts target (`"myproject.cli:app"`). Replaced with `pkg_name`. Unchanged sites.
- `my-project` — distribution contexts: `[project] name`, the `[project.scripts]` key, README command invocations
  (`uv run my-project --help`). Replaced with `dist_name`. New placeholder, edited into the three templates:
  - python-cli: `pyproject.toml` (`name`, scripts key), `README.md` (title, `uv run` lines, entry-point mention).
    Watch `README.md:17` — it mixes both tokens on one line: `python -m myproject` stays package, "the
    \`my-project\` entry point" is dist. Easiest site to get wrong; the scaffold test must assert the README too.
  - python, fastapi: `pyproject.toml` (`name`), `README.md` (title; fastapi's `uvicorn myproject.app:app` is a
    package context and stays `myproject`; neither has a `[project.scripts]` section)

Substitution order is irrelevant: `myproject` is not a substring of `my-project` and vice versa. Path renaming
(`str(rel).replace("myproject", pkg_name)`) is untouched — `my-project` never appears in file paths.

For hyphen-less, space-less project names `dist_name == pkg_name` — byte-identical output to today, which is why no
existing test moves (audit in problem.md: nothing depends on script == package; `example_project` exercises exactly
this degenerate case).

### 3. Build step + tracer PATH (`jig/eval/runner.py` + `jig/eval/collector.py`)

After the race settles on SUCCESS, **inside the success path's existing `try/except Exception` guard**
(`runner.py:306`), before the `collect()` call — placement is load-bearing: it's what makes a `TimeoutExpired`
propagate after teardown rather than escape unguarded:

```python
build = subprocess.run(
    ["uv", "sync"],
    cwd=project_dir, capture_output=True, text=True, timeout=300,
)
if build.returncode != 0:
    log.error("uv sync failed (exit %d):\n%s", build.returncode, build.stderr[-2000:])
    _teardown_proc(proc, project_dir)
    return RunResult(outcome=EvalOutcome.TRACER_FAIL, temp_dir=temp_path)
```

Synchronous and blocking is fine here — the orchestrator subprocess is done producing events and the runner has
nothing else to do (same reasoning as `_teardown_proc`). A `TimeoutExpired` is caught by the success path's existing
`try/except Exception` guard and propagates after teardown.

`collect()` gains one optional parameter, threaded into the existing tracer `subprocess.run`:

```python
async def collect(..., tracer_cmd=None, tracer_env: dict[str, str] | None = None):
    ...
    subprocess.run(tracer_cmd, cwd=project_path, env=tracer_env, ...)   # env=None == inherit, today's behavior
```

The runner passes:

```python
tracer_env = {**os.environ, "PATH": f"{project_dir / '.venv' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
```

`command -v hn-cli` in the tracer then resolves to the venv entry point. `jig eval collect` (the CLI) doesn't pass
`tracer_env` and keeps today's inherit-everything behavior.

Both changes land on the single existing `collect()` call in `run_eval` (`runner.py:314-320`):
`collect(temp_path, ...)` becomes `collect(project_dir, ..., tracer_env=tracer_env)`. Forgetting `tracer_env=`
would silently restore today's SKIP behavior (env defaults to inherit), so the e2e test/criterion is the guard.

### What deliberately does not change

- `tracer.sh` and the SKIP-is-fail contract (`_check_tracer_outcome`). A SKIP after this change means something is
  genuinely wrong (build claimed success but the entry point is missing) and should fail the run.
- Outcome/exit-code surface: build failure maps onto TRACER_FAIL. No manifest is written in that case
  (`manifest_path=None`); the build error lives in the runner log, which problem.md accepts ("manifest or runner
  log").
- Orchestrator, role prompts, auto-responder.

## Interfaces

- `collect(..., tracer_env: dict[str, str] | None = None)` — additive, default preserves current behavior.
- `_teardown_proc(proc, project_path)` — internal rename, same contract (reaper keys on the path containing
  `.jig/worktrees`).
- Template placeholder vocabulary: `myproject` = package name, `my-project` = distribution/script name. Documented
  in a comment at the top of `_apply_template_files`.
- `RunResult.temp_dir` continues to report the mkdtemp root.

## Data model

None. No persistent state changes; the manifest schema is untouched (`TracerResult` already captures
exit/stdout/stderr).

## Alternatives considered

### Tracer expects the underscored name (`hn_cli`)

No scaffold change, but the eval would validate a command the brief never mentions, every future fixture inherits
the mismatch, and the underlying wart (hyphenated dir → underscored command) stays for normal `jig init` users.
Rejected in problem.md open questions.

### Context-aware substitution instead of a second placeholder

Keep one `myproject` token and special-case pyproject's `name =` line, the scripts key, and README `uv run` lines in
`_apply_template_files`. Rejected: per-file parsing rules are fragile and invisible in the templates themselves; an
explicit `my-project` placeholder makes each occurrence's intent readable in the template source.

### Thread an explicit project-name parameter through `run_init`

Decouples naming from the directory entirely, but touches the shared init path and all its callers for an eval-only
need; the subdirectory achieves the same with zero shared-surface change. Rejected in problem.md open questions.

### Build inside `collect()`

Everything tracer-related in one function, but `collect()` is also the engine of `jig eval collect`, which operators
point at arbitrary finished projects — giving it a write side effect (creating `.venv`, mutating uv state) changes
that command's contract. Rejected; the runner owns lifecycle mutations.

### `uv run <tool>` in the tracer instead of PATH injection

Tracer-side fix (`uv run hn-cli top ...`) avoids the env plumbing but still needs the naming fix, makes every future
tracer remember the `uv run` incantation, and implicitly runs `uv sync` inside the tracer's 120s timeout. PATH
injection keeps tracers plain (`hn-cli top ...`) and puts the build under its own timeout.

### Chosen: subdir naming + two-token templates + runner-owned build with PATH injection

Each root cause fixed at its narrowest point; the only shared-surface change (template placeholders) is degenerate
for all existing hyphen-less projects and fixes a real wart for hyphenated ones.

**Decision:** this is problem.md's "Simplest possible solution" taken essentially wholesale. The alternatives above
are per-sub-decision rejections rather than a simplest/complete/optimal spectrum because no complication in the
problem statement forces anything beyond the simplest option — the one genuine fork (scaffold fidelity vs
fixture-side workaround) was resolved by the operator in problem.md's open questions.

## Risks

- **Agents touch the venv during the run.** Dev agents already run `uv sync`/`uv run pytest` in worktrees; the
  post-run `uv sync` in the project root is idempotent and authoritative (it rebuilds whatever state the run left).
  Low risk.
- **`uv sync` needs network.** Already true mid-run (agents install deps; `uv.lock` exists in the finished
  project). A sandbox/egress change that breaks this breaks the build step loudly (TRACER_FAIL + logged stderr).
- **Two-token edit misses a template site.** A missed `my-project` site leaves a literal `myproject` (or
  `my-project`) string in scaffolded output. Covered by extending the template scaffold tests with a hyphenated
  project name asserting both the scripts key and the package path.
- **Hidden consumers of `RunResult.temp_dir` assuming it IS the project.** Inside this repo the only consumers are
  `cli.py`'s echo and the tests; the analysis copy uses `analysis_out_dir` from the event, not temp_dir. Checked —
  none assume project-at-root. External muscle memory ("cd into temp_dir and poke around") still works; the project
  is one `cd hn-cli` deeper and the log line will print both paths.
- **mkdtemp prefix matching in orphan cleanup elsewhere.** `_kill_orphan_subprocesses` is called with explicit
  paths only; nothing globs `jig-integration-*` for reaping. Verified via grep.

## Out of scope

- Why the hn-cli run's E2E-validation ticket failed (separate investigation).
- Non-Python templates / generalized tracer environments.
- Renaming existing eval fixtures or changing the tracer contract.

## Open questions

- None.

## Change log

- 2026-06-11: Initial draft (Brent Hoover)
- 2026-06-11: Review fixes — pinned the collect() call-site change, all seven _teardown_proc sites, build-step
  placement inside the success-path guard, mixed-token README line, Decision paragraph (Brent Hoover)
