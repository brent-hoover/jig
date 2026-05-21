---
title: Prompt Style Eval — Design
type: design
status: draft
owner: brent
created: 2026-05-12
updated: 2026-05-21
problem: ./problem.md
---

# Prompt Style Eval — Design

## Summary

A standalone Python harness under `jig/evals/prompt_style_eval/` that compares hand-authored prompts against each
other on the same underlying task. A **task** is a hidden pytest suite plus a short reference description; a
**prompt** is a plain text/markdown file that asks the agent to build that task in some particular style (YAML
spec, prose spec, EARS, etc.). For each `(task, prompt, seed)` cell the harness invokes the Claude Code SDK
single-turn, classifies the outcome (code / question / refusal / malformed / error / timeout), executes any
returned code against the task's hidden tests in a `subprocess + tmpdir + timeout` sandbox, scores it with a
frozen rubric checklist via an LLM-as-judge, and computes static metrics (ruff, LoC, cyclomatic). All run records
are appended to a JSONL store. A `report` command aggregates the store into a per-prompt comparison table; a
`rescore` command re-runs the judge against persisted code without re-calling the candidate.

There is no templating layer in v1. "Same requirements, different style" is enforced by careful authoring of the
prompt pair, not by rendering both from a shared structured spec — that abstraction can be added later if the set
of styles grows past what's comfortable to write by hand.

## Approach

### Components

```
jig/evals/prompt_style_eval/
├── __main__.py          CLI entrypoint (click)
├── cli.py               run / report / rescore commands
├── runner.py            one cell end-to-end: load prompt → invoke → classify → score → store
├── sdk.py               Claude Code SDK wrapper (single-turn, write-only tool surface)
├── classify.py          outcome classifier (heuristic, transcript → enum)
├── sandbox.py           subprocess + TemporaryDirectory + timeout test runner
├── judge.py             frozen-checklist LLM judge
├── metrics.py           ruff, LoC, cyclomatic complexity
├── store.py             JSONL append + filtered read
├── report.py            aggregation, bootstrap CIs, table printing
├── models.py            pydantic v2 models (Task, Prompt, RunRecord, …)
├── tasks/               one directory per task; contains hidden tests AND its prompt variants
├── rubric/              versioned checklist files (v1.yaml, v2.yaml, …)
└── results/runs.jsonl   append-only run store
```

There is no `prompts/` directory at the top level. Prompt files live *inside* the task they belong to, because
a prompt only makes sense paired with its hidden test suite — moving them apart invites drift.

### Execution flow for one cell

```
load_task(task_id)             ──► hidden tests, description, timeout
load_prompt(task_id, prompt_id) ──► prompt text (read verbatim from file)
                                                   │
                                                   ▼
                                  sdk.invoke(prompt_text, model, temperature)
                                                   │
                                              transcript
                                                   │
                                       classify.outcome(transcript)
                                                   │
                          ┌────────────────────────┴────────────────────────┐
                          ▼                                                 ▼
                      "code"                                  "question" / "refusal" / …
                          │                                                 │
                  extract_code(transcript)                         RunRecord(outcome=…)
                          │                                                 │
              sandbox.run_tests(code, task.tests)                           ▼
                          │                                              store.append
                  metrics.compute(code)
                          │
              judge.score(code, rubric)
                          │
                          ▼
                     RunRecord(…)
                          │
                          ▼
                     store.append
```

A "cell" is the pair `(task_id, prompt_id)`. With three v1 tasks and two v1 prompts per task (`yaml_spec`,
`prose_spec`), there are 6 cells per eval. `--seeds N` says "ensure each selected cell has N records."

### Concurrency

- Async throughout. Cell runs are independent; the outer loop dispatches with `asyncio.gather` under a
  `Semaphore(--concurrency)`, default 5.
- Anthropic rate limits are the real cap. Surface 429s to the loop, back off, and retry the same cell (without
  storing the failed attempt as a "result" — it's a transport-layer retry, not an outcome).
- Sandbox subprocess execution is run via `asyncio.to_thread` so test runs don't block the event loop.

### Sample-count semantics (no per-seed cache)

A cell is identified by `(task_id, task_version, prompt_id, prompt_version, model, model_snapshot, temperature,
rubric_version)`. Each `--seeds N` invocation means "ensure this cell has N records in the store". The runner
counts matching records and dispatches `max(0, N - existing)` new runs. `--force` ignores existing and runs N
fresh.

`prompt_version` is derived from the content hash of the prompt file. Editing the prompt produces a new version
and therefore a new cell — old records remain comparable to each other but are not mixed with results from the
edited prompt.

Rationale: the Anthropic API doesn't expose a deterministic seed knob, so per-seed caching would cache "the first
draw we ever got" forever. Sample-count semantics make re-running a no-op once you have enough samples, and a
top-up when you don't, without ever pretending to replay a stochastic call.

### Rescoring path

`rescore --rubric-version v2 --prompt yaml_spec` reads existing run records where `outcome=="code"`, calls
`judge.score(record.extracted_code, rubric_v2)`, and appends *new* run records with the original cell identity
but a new `rubric_version` and a back-reference (`derived_from: <original run_id>`). Original records are not
mutated. The store remains append-only.

### Sandbox

For v1: `subprocess.run([python_exe, "-m", "pytest", "-q", "tests/"], cwd=tmpdir, timeout=…)` inside a
`tempfile.TemporaryDirectory()`. The generated code is written into the tmpdir alongside a copy of the task's
hidden `tests/` directory. SIGKILL on timeout. Stdout/stderr captured.

This is not adversarial isolation. The threat model is "model produces stupid code that hangs or writes too many
files," not "model is an attacker". Bwrap fold-in is deferred until the harness is integrated with jig and the
infrastructure is already in scope.

### Outcome classifier

Heuristic, runs locally — no second model call. The transcript is the SDK's message list. Rules in priority
order:

1. SDK raised → `error`
2. Wall-clock exceeded → `timeout`
3. No final assistant message → `malformed`
4. Final message contains a refusal marker (e.g. starts with "I can't" / "I won't" / "I'm not able") → `refusal`
5. Final message ends in `?` and contains no extractable code block / file write → `question`
6. Extractable code present (fenced block in agreed language, or written file via tool) → `code`
7. Otherwise → `malformed`

The classifier is wrong sometimes. That's acceptable because (a) the raw transcript is persisted, so misclassified
runs can be re-classified later by changing this code and replaying over the store, and (b) misclassification is
roughly evenly distributed across styles for v1 purposes.

### Judge

Single call per `code`-outcome run. Inputs: the extracted code + the rubric version's checklist. The judge is
asked to fill out each checklist item independently with a structured response (JSON, schema enforced by the SDK
or by a Pydantic parse step). No free-form prose score. Judge temperature `0.0`. Judge model is recorded per-run.

### Static metrics

Run alongside the judge, no model call:

- `ruff check` count and category breakdown
- LoC (non-blank, non-comment)
- max cyclomatic complexity (via `radon` or equivalent)

These are reported alongside the judge score in the per-style breakdown, never collapsed into the judge number.

### Implementation-choice observation (post-hoc, not via tests)

Hidden tests check behavior, not implementation choices — they do not assert that the model picked any particular
subcommand name, output format, completion marker, or persistence file format. Those decisions are *interesting*
(they tell us what each prompt style biases the model toward) but should not influence pass/fail.

The reporter (plan step 12) surfaces these as descriptive statistics over the persisted runs:

- Sampled excerpts of `extracted_code` per prompt style (first/last N chars, or a hand-picked random sample),
  so a human reviewer can eyeball the distribution of solutions.
- Optional heuristic histograms over `extracted_code` (e.g. detected subcommand names, output-format
  fingerprints) — added on demand when a specific question is worth answering, not built in v1.

Because the raw transcript and extracted code are persisted, retroactively asking "what subcommand names did
prose-spec produce vs yaml-spec?" is a one-shot query, not a re-run.

## Interfaces

### CLI

```
uv run python -m jig.evals.prompt_style_eval run \
  --task todo_cli \
  --prompt yaml_spec --prompt prose_spec \
  --seeds 10 \
  --model claude-opus-4-7 \
  --temperature 0.0 \
  --judge-model claude-sonnet-4-6 \
  --rubric v1 \
  [--force] [--concurrency 5] [--dry-run]

uv run python -m jig.evals.prompt_style_eval report \
  [--task …] [--prompt …] [--rubric v1] \
  [--format table|json]

uv run python -m jig.evals.prompt_style_eval rescore \
  --rubric v2 \
  [--task …] [--prompt …]
```

`--prompt <id>` resolves to `tasks/<task_id>/prompts/<id>.md`. If no `--prompt` is given, all prompts under the
selected task(s) are used.

`run` exits non-zero only on configuration error or unhandled exception — not on test failures, which are
*data*, not a process failure.

`--dry-run` renders prompts and prints a cost estimate without calling models.

### Task directory layout

```
tasks/<task_id>/
├── task.yaml            # task metadata: id, version, language, entrypoint, test_command, timeout_s
├── description.md       # short human reference description (not sent to the model; aids prompt authors)
├── tests/               # hidden pytest suite
│   └── test_*.py
├── prompts/             # one file per prompt variant for this task
│   ├── yaml_spec.md
│   ├── prose_spec.md
│   └── …
├── reference.py         # known-good reference solution (sanity-checks the tests; NOT sent to the model)
└── README.md            # what this task is for and what it discriminates between styles
```

Prompts are read **verbatim** and passed to the SDK as the user message. No interpolation, no templating, no
system-prompt wrapper. What's in the file is what the model sees. This is deliberate: it makes the comparison
auditable — anyone reading `prompts/yaml_spec.md` and `prompts/prose_spec.md` side-by-side can verify that the
two encode the same requirements.

When authoring a new prompt variant for an existing task, the workflow is:

1. Copy an existing prompt to `prompts/<new_id>.md`.
2. Rewrite the body in the target style.
3. Diff against siblings to confirm the same requirements are present in different form.
4. Commit. The content-hash gives the prompt its `prompt_version`.

### Rubric layout

```
rubric/<rubric_version>.yaml
```

Frozen once published. Each item:

```yaml
- id: type_hints_present
  prompt: "Do all function and method signatures include type hints?"
  scale: bool                       # bool | likert_5
- id: function_names_descriptive
  prompt: "Are function names descriptive (not single-letter, not generic)?"
  scale: likert_5
```

Adding a sixth item means publishing `v2.yaml`. Existing runs keep `v1` results; rescoring against `v2` produces
derived records.

## Data model

### Task (loaded from `task.yaml`)

```yaml
id: todo_cli
version: v1
language: python
entrypoint: todo.py
test_command: ["pytest", "-q", "tests/"]
timeout_s: 30
```

Just the metadata the runner needs to execute and score. The *content* of what the model is asked to build lives
in the prompt files; the runner does not synthesize prompts from structured fields.

`description.md` sits alongside as a human-readable summary of what the task is — for prompt authors and reviewers
— but is never sent to the model.

### Prompt (plain text file)

```
tasks/<task_id>/prompts/<prompt_id>.md
```

Read verbatim. Its identity is `(task_id, prompt_id, content_hash)`. Two example prompts for the same task,
abbreviated:

`prompts/yaml_spec.md`:

```
You are to build a CLI program `todo.py` matching the following spec.

```yaml
commands:
  - name: add
    args: [text]
    behaviour: Add a new todo item with the given text.
  - name: list
    args: []
    behaviour: Print all items, one per line, numbered from 1.
  - name: done
    args: [n]
    behaviour: Mark item n as complete.
constraints:
  - Persistence is local to the working directory; format is up to you.
  - No external dependencies beyond the Python standard library.
```
```

`prompts/prose_spec.md`:

```
You are to build a CLI program `todo.py`. It should accept three commands.

`add <text>` adds a new todo item with the given text. `list` prints every item
on its own line, numbered from 1. `done <n>` marks item n as complete.

The program may store data however it likes, in any file format, but it must
keep data local to the working directory and must not depend on any third-party
Python packages — standard library only.
```

The two convey the same requirements; the encoding is what varies. Verifying that equivalence is the prompt
author's responsibility and a code-review concern.

### RunRecord (one JSONL line)

```json
{
  "run_id": "uuid",
  "timestamp": "2026-05-12T18:04:01Z",
  "cell": {
    "task_id": "todo_cli",
    "task_version": "v1",
    "prompt_id": "yaml_spec",
    "prompt_version": "sha256:abc123…",
    "model": "claude-opus-4-7",
    "model_snapshot": "2026-05-01",
    "temperature": 0.0,
    "rubric_version": "v1"
  },
  "prompt": "…full rendered prompt…",
  "transcript": [ … SDK message list … ],
  "outcome": "code",
  "extracted_code": "…",
  "test_result": {
    "passed": true,
    "n_passed": 5,
    "n_failed": 0,
    "duration_s": 0.42,
    "stdout": "…",
    "stderr": ""
  },
  "static_metrics": {
    "loc": 87,
    "ruff_findings": 0,
    "ruff_breakdown": {},
    "cyclomatic_max": 4
  },
  "judge": {
    "model": "claude-sonnet-4-6",
    "snapshot": "2026-04-15",
    "rubric_version": "v1",
    "checklist": {
      "type_hints_present": true,
      "no_bare_except": true,
      "function_names_descriptive": 4
    },
    "tokens": {"input": 432, "output": 89},
    "cost_usd": 0.0021
  },
  "candidate_tokens": {"input": 1234, "output": 567},
  "candidate_cost_usd": 0.0234,
  "derived_from": null
}
```

Fields not applicable to the outcome are `null` (e.g. `test_result` is null when `outcome != "code"`).

## Alternatives considered

### Use the raw Anthropic SDK instead of Claude Code SDK

Simpler — one HTTP call per run, easy classification. Rejected because the harness exists in service of jig
prompts, and jig runs agents via the Claude Code SDK with tools available. Measuring raw `messages.create` would
test prompts in a context different from where they actually live; the YAML-vs-prose result on a raw-SDK harness
would not necessarily transfer to the agent-SDK context.

### Drive runs through the jig orchestrator

Most representative of the production environment. Rejected for v1 because it couples the eval harness to a large
moving piece of code; orchestrator changes would invalidate eval results in ways that are hard to debug, and the
eval harness's job is to be a stable measuring stick. The fold-in is the *outcome* we eventually want, but the
measurement tool needs to exist first.

### Bwrap or Docker sandbox for generated code

Strongest isolation. Rejected for v1 because the threat is "stupid code hangs or writes too many files," not "the
model is an attacker"; subprocess + tmpdir + SIGKILL handles the threat that actually exists. Reuse-when-folded-in
is the plan.

### Free-form LLM-as-judge prose score

Most flexible — the judge can comment on whatever it notices. Rejected because (a) free-form scores can't be
aggregated without another extraction layer, (b) judge writing variance dominates signal on small samples, and
(c) the rubric existing in a file lets us version it, diff it, and reason about it.

### Jinja2 templates rendering a structured task spec

An earlier draft stored each task as a structured YAML (`requirements: [{id, statement}, …]`) and rendered it
through Jinja2 templates per style (`yaml_spec.j2`, `prose_spec.j2`). The appeal: a single style template
auto-generates the prompt for every task, and "same requirements" is enforced mechanically because both prompts
render from the same source. Rejected for v1 because:

- v1 has 3 tasks × 2 prompts = 6 files. Hand-authoring is cheap.
- The template layer is itself a thing that can be wrong, and bugs in `to_spec_yaml` or paragraph rendering would
  silently bias one style — exactly what the eval is trying to detect.
- Plain prompt files are auditable by anyone, including non-programmers. A Jinja template plus a YAML spec is two
  artefacts to reason about; a `.md` is one.
- The "same requirements" property is what we actually care about. Enforcing it through code review on
  hand-written prompts is fine for v1 sizes. If the number of styles or tasks grows enough that hand-authoring
  becomes burdensome, the templated approach can be revisited — the storage shape (`tasks/<id>/prompts/*.md`)
  doesn't preclude generating those files from a template later.

### Per-seed cache keys

Treat `(cell, seed)` as the unit and cache forever. Rejected because the Anthropic API doesn't expose a seed knob
on `messages.create`, so the "same seed → same output" property doesn't hold; caching by seed would freeze
whichever draw we got first. Sample-count semantics give the same skip-the-work behaviour without pretending to
be deterministic.

### One global rubric vs per-task rubrics

Per-task is more sensitive to what each task is testing. Rejected for v1 because per-task rubrics make
cross-task aggregation either incoherent or laborious. v1 has one rubric; task-specific *additions* (not
replacements) can be layered in later via `tasks/<id>/rubric_extras.yaml` if signal warrants it.

### Chosen: hand-authored prompt files per (task, style), Claude Code SDK runs, frozen-checklist judge, subprocess sandbox, JSONL store

This combination keeps each axis varied independently (task ↔ prompt ↔ rubric ↔ model), matches how prompts
are actually used in jig (verbatim strings, not synthesized from structured specs), is implementable in a few
hundred lines without new dependencies the project doesn't already use, and keeps the comparison auditable — what
you see in the prompt file is what the model sees.

## Risks

- **Prompt-pair drift.** Two prompts that are *meant* to convey the same requirements may quietly encode
  different ones (an item omitted in the prose version, a constraint added only in the YAML). The result then
  measures requirement-coverage, not style. Mitigation: when authoring a new prompt variant, diff against
  siblings and confirm the same requirement set is present in each. This is a code-review concern, not an
  automated one in v1. A later improvement could parse prompts for a canonical requirement-id list and warn on
  mismatch.
- **Tasks don't discriminate prompts.** All prompts pass on every seed → no signal. Mitigation: pick at least one
  task that's hard enough to fail sometimes; if v1 corpus is too easy, the design supports adding harder ones
  without code change.
- **Judge variance dominates the static metrics.** Checklist items disagree wildly across judge runs at the same
  temperature. Mitigation: scale → if judge variance > static-metric variance for the same axis, drop the judge
  item.
- **SDK behavior drift.** A Claude Code SDK upgrade changes message shape or default tools and old run records
  become incomparable to new ones. Mitigation: record SDK version per-run; bump cell version on SDK upgrade so
  comparisons within a cell stay coherent.
- **Cost blowout.** 5 prompts × 8 tasks × 15 seeds + judge ≈ a lot of tokens. Mitigation: `--dry-run` cost
  estimate, running cost printed per cell, hard `--budget-usd` cutoff that aborts the matrix when reached.
- **Outcome misclassification skews results.** A `code` that gets called `question` (or vice versa) pollutes the
  bucket. Mitigation: full transcript persisted, classifier replayable; spot-check the bucket distribution by
  hand during v1 calibration.
- **Tests in `tasks/<id>/tests/` are wrong** and pass code that's broken (or fail code that's correct).
  Mitigation: every task lands with at least one known-good reference solution committed in a sibling
  `tasks/<id>/reference.py` (not used by the runner, just by the test author to confirm tests are right).

## Out of scope

- Multi-language tasks (Python only for v1).
- Multi-turn agent loops (single-turn; an "agent asked a question" outcome ends the run).
- Web UI / dashboard. CLI + JSONL + table is enough.
- Per-task rubric replacements (only additions, deferred).
- Cloud / batch / distributed execution.
- Direct fold-in with the jig orchestrator (deferred; the design keeps the door open by making task definitions,
  prompt templates, and results plain data on disk).
- Statistical significance machinery beyond bootstrap CIs (no Bayesian priors, no power analysis).

## Open questions

- [ ] Exact v1 task set. Three tasks of varying difficulty. Candidates: CLI todo, line-by-line text-stats tool,
      simple URL-route matcher. Need to pick during the plan.
- [ ] Exact v1 prompt pair per task. Two prompt variants per task — `yaml_spec` and `prose_spec` — authored such
      that the two encode the same requirements. Need to draft and review during the plan.
- [ ] Exact v1 checklist items. Proposed five: type hints present (bool), no bare except (bool), function names
      descriptive (likert_5), scope appropriate (likert_5), no obvious dead code (bool). Confirm during the plan.
- [ ] Judge model snapshot. Use Sonnet 4.6 (`claude-sonnet-4-6`) as a different snapshot from the candidate
      (Opus 4.7) — confirm cost and rate-limit headroom before committing.
- [ ] Whether `transcript` in run records is the SDK's full message list (richer, replayable, larger) or a
      compacted final-response-only form (smaller, lossy). Default: full transcript, gzip the JSONL if size
      becomes a problem.
- [ ] Reference solutions: in-tree under `tasks/<id>/reference.py` (transparent, sanity-checks tests) or out of
      tree (avoids any risk the candidate model has seen them in training)? v1 lean is in-tree with the note that
      they are never provided to the candidate or the judge.

## Change log

- 2026-05-12: Initial draft (brent)
- 2026-05-12: Drop the Jinja-templates-rendering-a-structured-spec layer. Prompts are now plain hand-authored
  files living under each task (`tasks/<id>/prompts/<style>.md`), read verbatim by the runner. CLI flag
  renamed `--style` → `--prompt`. Cell identity uses `prompt_id + content-hash` instead of `style_id +
  style_version`. Added prompt-pair-drift risk. Recorded templated approach under Alternatives so we can revisit
  if the matrix grows (brent)
