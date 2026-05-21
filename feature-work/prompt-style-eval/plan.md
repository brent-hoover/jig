---
title: Prompt Style Eval — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-05-12
updated: 2026-05-21
design: ./design.md
---

# Prompt Style Eval — Implementation Plan

## Overview

Build the harness bottom-up: pydantic models → store → sandbox → SDK wrapper → classifier → metrics → judge →
runner → CLI → reporter. Get an end-to-end skeleton working against a single hand-stubbed task and prompt before
authoring the real v1 content. Then write the three v1 tasks (todo_cli, word_stats, url_router) with reference
solutions and prompt pairs, freeze rubric v1, and run the first real eval comparing `yaml_spec` vs `prose_spec`
across all three tasks. Phase boundaries are deliberate checkpoints — each phase ends with something testable in
isolation so a stall in a later phase doesn't unwind earlier work.

## Preconditions

- [x] Problem and design docs approved (drafts in `feature-work/prompt-style-eval/`).
- [ ] `claude-agent-sdk` already on the project's dependency list (it is — used by `jig/agent.py`).
- [ ] `CLAUDE_CODE_OAUTH_TOKEN` available in the shell (per existing project setup).
- [x] `radon` added to project deps for cyclomatic complexity (`radon>=6.0.1`, added 2026-05-12).
- [ ] Decision committed on v1 tasks (todo_cli, word_stats, url_router) and v1 checklist items (see Step 9).

## Resolved design open questions (locked for v1)

- **v1 task set**: `todo_cli`, `word_stats`, `url_router`. Three shapes — state + I/O, file processing + text
  counting, pattern matching.
- **v1 prompts per task**: `yaml_spec.md` and `prose_spec.md`.
- **v1 checklist (rubric v1)**:
  1. `type_hints_present` (bool) — all function/method signatures have type hints
  2. `no_bare_except` (bool) — no `except:` without a specific exception class
  3. `function_names_descriptive` (likert_5) — function names communicate intent
  4. `scope_appropriate` (likert_5) — neither under-built nor over-engineered for the task
  5. `no_obvious_dead_code` (bool) — no unused imports, unreferenced functions, commented-out blocks
- **Candidate model**: `claude-opus-4-7`, temperature `0.0`.
- **Judge model**: `claude-sonnet-4-6`, temperature `0.0`.
- **Transcript shape**: full SDK message list. Gzip the JSONL store if size becomes a problem (defer).
- **Reference solutions**: in-tree at `tasks/<id>/reference.py`. Never read by runner, judge, or SDK call — they
  exist to sanity-check the hidden tests.

## Steps

### 1. Scaffold the package

**What:** Create `jig/evals/prompt_style_eval/` with `__init__.py`, `__main__.py`, `cli.py` stub (click app with
empty `run`/`report`/`rescore` subcommands), and a `results/` dir (gitignored except for a `.gitkeep`).

**Why:** Establishes import path and `python -m jig.evals.prompt_style_eval` entrypoint.

**Verify:** `uv run python -m jig.evals.prompt_style_eval --help` prints the three subcommands.

### 2. Pydantic v2 models

**What:** `models.py` defining `Task`, `Prompt`, `RubricItem`, `Rubric`, `Outcome` (Literal), `TestResult`,
`StaticMetrics`, `JudgeScore`, `Cell`, `RunRecord`. Models match the data-model section of the design.

**Why:** Every other module is typed against these; getting them wrong cascades.

**Verify:** `uv run pytest tests/evals/test_models.py` — unit tests construct each model, round-trip through JSON,
reject invalid combinations (e.g. `outcome="code"` but no `test_result`).

### 3. Store (JSONL append + filtered read)

**What:** `store.py` with `append(record: RunRecord)`, `read_all() -> Iterator[RunRecord]`, `count_matching(cell:
Cell) -> int`, `query(filters) -> Iterator[RunRecord]`. Append is atomic per line.

**Why:** Sample-count semantics in the runner depend on `count_matching`. Append-only contract is load-bearing.

**Verify:** `tests/evals/test_store.py` — append N records, read them back identically; filtered queries return
correct subsets; concurrent appends (two `asyncio.create_task` writers) don't corrupt lines.

### 4. Sandbox

**What:** `sandbox.py` with `run_tests(code: str, task: Task) -> TestResult`. Creates a `TemporaryDirectory`,
writes the code to `task.entrypoint`, copies `tasks/<id>/tests/` into the tmpdir, runs `task.test_command` via
`asyncio.create_subprocess_exec` with `task.timeout_s` timeout and SIGKILL on overrun, captures stdout/stderr,
parses pytest's summary line for counts.

**Why:** Test execution is the load-bearing correctness signal. Has to be reliable.

**Verify:** `tests/evals/test_sandbox.py` — run a known-passing snippet returns `passed=True`; a known-failing
snippet returns `passed=False`; an infinite-loop snippet times out and gets killed; a snippet that writes to
`/etc/passwd` fails harmlessly because cwd is the tmpdir.

### 5. SDK wrapper

**What:** `sdk.py` with `invoke(prompt: str, model: str, temperature: float) -> Transcript`. Uses
`claude-agent-sdk` (the same SDK `jig/agent.py` uses). Single turn. Captures the full message list. Returns
`(transcript, tokens, cost_usd)`.

**Why:** The model call is the unit being measured. Has to record everything needed to replay or rescore.

**Verify:** `tests/evals/test_sdk.py` — mock the SDK transport, confirm a simple prompt produces a transcript
with the expected shape; record tokens and cost from a known fake response.

### 6. Outcome classifier

**What:** `classify.py` with `outcome(transcript: Transcript) -> Outcome`. Implements the priority rules from
the design (error → timeout → malformed → refusal → question → code → malformed).

**Why:** Every run record needs an outcome bucket. Classification must be deterministic and replayable.

**Verify:** `tests/evals/test_classify.py` — table-driven cases for each outcome bucket, including ambiguous
"question with a code block also present" (resolves to `code`, per the design).

### 7. Static metrics

**What:** `metrics.py` with `compute(code: str) -> StaticMetrics`. Runs `ruff check --output-format=json` as a
subprocess, counts LoC (non-blank, non-comment) directly, computes max cyclomatic via `radon`.

**Why:** Objective signal that doesn't depend on the judge.

**Verify:** `tests/evals/test_metrics.py` — known-clean snippet returns `ruff_findings=0`; snippet with a bare
`except:` gets flagged; LoC matches manual count.

### 8. Judge

**What:** `judge.py` with `score(code: str, rubric: Rubric, model: str) -> JudgeScore`. Builds a structured
prompt asking the judge to fill out the checklist, calls the SDK with temperature `0.0`, parses the response into
a `dict[item_id, bool|int]`. Records tokens and cost.

**Why:** One of the three quality signals. Has to be reproducible across runs.

**Verify:** `tests/evals/test_judge.py` — feed a known-good snippet through a mocked SDK; confirm checklist
fields are filled with the expected types. A malformed judge response raises a typed error that the runner can
record as `outcome="error"` on the *judge*, not the candidate.

### 9. Author v1 tasks + prompts + rubric

**What:** Create three task directories. Each contains:

- `task.yaml` (id, version, language, entrypoint, test_command, timeout_s)
- `description.md` (human-readable summary, not sent to the model)
- `tests/test_*.py` (hidden pytest suite, 4–8 tests each)
- `reference.py` (known-good solution that passes the tests)
- `prompts/yaml_spec.md`, `prompts/prose_spec.md` (hand-authored pair, reviewed for requirement equivalence)
- `README.md` (what this task discriminates, what tricky cases the tests cover)

Also create `rubric/v1.yaml` with the five checklist items.

**Why:** Without real content the harness has nothing to measure.

**Verify:** For each task, `pytest -q tasks/<id>/tests/ --rootdir=tasks/<id>` against `reference.py` placed at
the entrypoint position passes. Manual diff of `prompts/yaml_spec.md` vs `prompts/prose_spec.md` confirms the
two encode the same requirements (different form, same content).

### 10. Runner

**What:** `runner.py` with `async def run_cell(cell: Cell, prompt_text: str, task: Task) -> RunRecord`. Wires
together: `sdk.invoke` → `classify.outcome` → if `code`: extract → `sandbox.run_tests` → `metrics.compute` →
`judge.score` → assemble `RunRecord` → `store.append`. Transient SDK errors (rate-limit, network) retry with
backoff; persistent errors record `outcome="error"`.

**Why:** This is the integration point everything else feeds into.

**Verify:** `tests/evals/test_runner.py` — integration test against a stub task and a mocked SDK that returns
deterministic responses for each outcome bucket. Each run produces exactly one record in the store with all
expected fields.

### 11. `run` CLI command

**What:** `cli.py` `run` subcommand. Resolves `--task` and `--prompt` flags to tasks and prompt files, computes
the cell for each pair, queries the store for existing count, dispatches `max(0, N - existing)` cells under an
`asyncio.Semaphore(--concurrency)`. Prints per-cell progress and running cost. `--dry-run` skips the SDK calls
and prints estimated cost.

**Why:** First end-to-end CLI behaviour. Everything from here is reporting.

**Verify:** `uv run python -m jig.evals.prompt_style_eval run --task todo_cli --prompt yaml_spec --seeds 2
--dry-run` prints a plan and an estimate. Without `--dry-run`, it produces 2 records in `results/runs.jsonl` and
the records have the expected cell identity.

### 12. Reporter

**What:** `report.py` with `aggregate(records, group_by=("task_id", "prompt_id")) -> Report`. Computes per-cell
pass rate, outcome-bucket breakdown, checklist averages, static-metric summaries, and bootstrap CIs (1000
resamples). Renders as a text table (and JSON when `--format json`).

**Why:** Without aggregation, run records are just a pile of JSONL.

**Verify:** `tests/evals/test_report.py` — feed a constructed set of records, confirm pass rates and CIs match
manual calculations. `uv run python -m jig.evals.prompt_style_eval report --task todo_cli` against the records from
step 11 prints a sensible table.

### 13. `rescore` CLI command

**What:** `cli.py` `rescore` subcommand. Reads existing records with `outcome=="code"`, re-runs the judge with
the requested rubric version, appends derived records (`derived_from: <original run_id>`, new
`rubric_version`).

**Why:** Lets us iterate on the rubric without re-burning candidate tokens.

**Verify:** Create a `rubric/v1b.yaml` with one item flipped. `uv run python -m jig.evals.prompt_style_eval rescore
--rubric v1b` produces N new records (where N = existing `code` records for the filter), each with
`derived_from` set. The original records are unchanged.

### 14. End-to-end shakedown (small scale)

**What:** Run the full eval at a small scale to confirm the pipeline works on real model calls.

```
uv run python -m jig.evals.prompt_style_eval run \
  --task todo_cli --task word_stats --task url_router \
  --prompt yaml_spec --prompt prose_spec \
  --seeds 3 \
  --model claude-opus-4-7 --temperature 0.0 \
  --judge-model claude-sonnet-4-6 --rubric v1 \
  --concurrency 3
```

3 tasks × 2 prompts × 3 seeds = 18 candidate runs + 18 judge runs.

**Why:** Find integration bugs before paying for the real eval.

**Verify:** All 18 records present. Pass rates non-zero (sanity: the tasks aren't impossible) and non-100% on at
least one task (sanity: the tasks aren't trivial). Manual spot-check of one record per outcome bucket to confirm
classification is sane. Cost is under $5.

### 15. First real eval (the YAML-vs-prose answer)

**What:** Same command as step 14 but `--seeds 15`. 3 × 2 × 15 = 90 candidate + 90 judge runs.

**Why:** This is the deliverable — first evidence for or against the YAML-vs-prose hypothesis.

**Verify:** `uv run python -m jig.evals.prompt_style_eval report --rubric v1` produces a table showing per-prompt
pass rate, variance, checklist scores, and bootstrap CIs across all three tasks. Persist the report to
`feature-work/prompt-style-eval/results/first-eval.md` with a brief interpretation.

## Rollback

Nothing here touches shared infrastructure. The harness is a self-contained directory under `jig/evals/`. Worst-case
rollback: delete `jig/evals/prompt_style_eval/` and any `tests/evals/` files. Run records under `results/runs.jsonl`
are local-only.

## Out of scope for this plan

- Folding into the jig orchestrator (deferred per design non-goals).
- Web UI / dashboard. Tables to stdout only.
- Per-task rubrics or rubric extras. v1 = one global rubric.
- Cost dashboards beyond the running per-cell print and the `--budget-usd` cutoff.
- Statistical machinery beyond bootstrap CIs.
- Additional prompt styles beyond `yaml_spec` and `prose_spec`. Adding more is a follow-up once the harness is
  validated.

## Change log

- 2026-05-12: Initial draft (brent)
