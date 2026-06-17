# Prompt Style Eval

This harness compares prompt variants against the same hidden-test task. Each
run invokes a candidate model, executes returned Python code in a temporary
sandbox, scores the code with a rubric-backed judge, and appends a record to a
local JSONL store.

## Setup

From a fresh clone:

```bash
uv sync --dev
claude setup-token
```

`run` requires `CLAUDE_CODE_OAUTH_TOKEN`. `claude setup-token` configures it for
Claude Code; alternatively export the variable before invoking the eval.

## Available Python Quality Tasks

The Python agent-quality corpus includes these tasks:

| Task | Prompts | Rubric | Use it for |
| --- | --- | --- | --- |
| `review_fix_ladder` | `patch_first`, `review_then_fix` | `python_quality_v1` | Review-and-fix work across four flawed modules. |
| `order_fulfillment_engine` | `architectural_spec`, `prose_spec` | `python_quality_v1` | Greenfield domain-model work with pricing, inventory, shipments, and cancellation behavior. |

`review_fix_ladder` uses multi-file scoring. Its `task.yaml` lists every file
that should be scored, so the judge evaluates `cart_totals.py`, `scheduler.py`,
`permissions.py`, and `ledger.py`, not just the entrypoint.

## Run

Run all prompt variants for both Python quality tasks:

```bash
uv run python -m jig.evals.prompt_style_eval run \
  --task review_fix_ladder \
  --task order_fulfillment_engine \
  --rubric python_quality_v1 \
  --seeds 3 \
  --model claude-sonnet-4-6 \
  --judge-model claude-opus-4-7 \
  --concurrency 3
```

Run a single prompt variant:

```bash
uv run python -m jig.evals.prompt_style_eval run \
  --task review_fix_ladder \
  --prompt review_then_fix \
  --rubric python_quality_v1 \
  --seeds 5
```

The run command treats `--seeds` as the target sample count per cell. If a cell
already has enough records in the store, it is skipped. Use `--force` to append
fresh records anyway.

Results are written to `jig/evals/prompt_style_eval/results/runs.jsonl` by
default. To keep an experiment separate:

```bash
uv run python -m jig.evals.prompt_style_eval run \
  --task order_fulfillment_engine \
  --rubric python_quality_v1 \
  --seeds 3 \
  --store-path /tmp/jig-python-quality-runs.jsonl
```

## Report

Report on the default store:

```bash
uv run python -m jig.evals.prompt_style_eval report \
  --task review_fix_ladder \
  --task order_fulfillment_engine \
  --rubric python_quality_v1
```

Report on a separate experiment store:

```bash
uv run python -m jig.evals.prompt_style_eval report \
  --store-path /tmp/jig-python-quality-runs.jsonl \
  --rubric python_quality_v1
```

The text report groups results by full task identity, including task version.
Each prompt cell shows outcome counts, pass rate with a bootstrap confidence
interval, static metrics, judge coverage, judge scores, and total observed cost.

For machine-readable output:

```bash
uv run python -m jig.evals.prompt_style_eval report \
  --rubric python_quality_v1 \
  --format json
```

## Add Another Prompt

Add a Markdown file under the task's `prompts/` directory. The filename stem is
the prompt id:

```text
jig/evals/prompt_style_eval/tasks/review_fix_ladder/prompts/my_prompt.md
```

Then run it by id:

```bash
uv run python -m jig.evals.prompt_style_eval run \
  --task review_fix_ladder \
  --prompt my_prompt \
  --rubric python_quality_v1 \
  --seeds 3
```
