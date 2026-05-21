---
title: Prompt Style Eval — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-12
updated: 2026-05-12
---

# Prompt Style Eval — Problem Statement

## Context

A large part of building agents and LLM applications is choosing a prompting style: terse vs verbose, plain
instructions vs chain-of-thought, zero-shot vs few-shot, role-primed vs neutral, plan-then-code vs straight-to-code,
and so on. Across the jig project (agent roles, PM/PO prompts, spec regeneration, reviewer prompts) we have a lot of
prompts and very little empirical basis for the stylistic choices in them. Today we pick a style by intuition, run
the system a few times, eyeball the result, and move on.

The existing `evals/watcher/` directory covers stall detection and analyzer behavior — it is not a general harness
for comparing prompts. There is no place to ask "does style A produce more correct / more consistent / higher
quality code than style B for the kinds of tasks we care about?" and get a defensible answer.

## Problem

We have no repeatable way to compare prompt styles on the dimensions that actually matter for code-generating
agents:

1. **Correctness** — does the generated app pass a hidden test suite?
2. **Consistency** — across N runs of the same prompt at the same task, how much does the output vary in
   pass-rate and structure? A style that passes 9/10 times beats one that passes 9/10 on average but with high
   variance.
3. **Quality** — is the code readable, idiomatic, appropriately scoped? This is fuzzier but matters: a passing
   solution that's 400 lines of spaghetti is not equivalent to a 60-line clean one. An LLM-as-judge can produce a
   useful signal here, but the score is a diagnostic — not gospel. It is read alongside static metrics (ruff
   findings, LoC, cyclomatic complexity) and the raw artifact, not in place of them.

Without a harness, every prompt-style choice we make is anecdote. We cannot answer "is the role primer in
`roles/pm.yaml` actually helping?" or "would a plan-then-code style beat the current direct style for the builder
agent?" — and we cannot tell whether changes to prompts are improvements or regressions.

## Motivating questions

The harness exists to answer prompt-design questions in this codebase. We will use it to investigate many
hypotheses over time; the harness is not built for any one of them. A non-exhaustive list of questions we want it
to be capable of answering:

- Does a spec encoded as YAML produce better results than the same spec written as prose? *(the first test we
  intend to run — see below)*
- Does adding a role primer (e.g. "You are an experienced Python engineer…") improve correctness, or just style?
- Does explicit plan-then-code outperform direct code generation for tasks with >3 requirements?
- Do few-shot examples help on tasks that are unlike the examples, or only on tasks similar to them?
- Does an EARS-style spec produce different output than a free-form requirements list?

The first hypothesis we'll actually run through the harness — useful both as a real result and as a shakedown for
the harness itself — is YAML-vs-prose. It surfaces one design constraint worth naming up front: **a task's
logical requirements must be format-independent**. The task owns the requirements and the hidden tests; the
prompt style owns how those requirements are *rendered* into the prompt sent to the model. That separation is
what allows spec encoding (YAML, prose, EARS, bullet list, …) to be expressed as a prompt-style variant rather
than as a separate task per format. It is also what keeps the harness from being co-designed around any single
hypothesis.

## Simplest possible solution

Pick one small task (e.g. "build a CLI todo app with add/list/done"), write two prompt-style templates, run each
through the Anthropic SDK 10 times against a pinned model+temperature, run a hidden pytest suite against the
generated code, and print a pass-rate table.

That gets you a defensible answer for *that one task and those two styles*, in a few hundred lines of code, with
no framework. Everything beyond it is added complexity that has to earn its place.

## Complications considered

- **Scale**: A full matrix grows as `styles × tasks × seeds`. 5 styles × 8 tasks × 15 seeds = 600 model runs per
  change. That's not "scale" in the distributed-systems sense but it dominates cost and wall-clock time. The
  harness must (a) make it cheap to skip combinations already run, (b) persist raw results so re-scoring doesn't
  require re-running, and (c) support running a slice (one style × one task) for iteration.

- **Concurrency**: Multiple runs can happen in parallel (different SDK calls are independent). Race conditions
  exist only around the results store. N/A for correctness — a JSONL append per run with a unique id is enough.
  Rate limits on the Anthropic API are the real concurrency cap.

- **Failure modes**: Model returns nothing, returns malformed code, hits a tool/length limit, network errors, or
  — when running via an agent SDK — comes back with a clarifying question instead of code. All transient cases
  are recoverable by retry; persistent failures must be recorded as a run outcome (not silently dropped) so they
  count against the style's consistency score. The "agent asked a question" outcome is *not* automatically
  re-prompted into an answer: a style that consistently produces questions instead of code is itself a result and
  must be visible in the report rather than papered over. Hidden test execution can also fail (timeout, crash)
  and must be sandboxed — code from the model should not be trusted not to `rm -rf`.

- **Cross-cutting policies**:
  - *Cost*: every run costs tokens. The harness must report `$` per eval pass so we know the price of an answer.
  - *Reproducibility*: model version, temperature, prompt template, task spec, judge model, and judge prompt all
    have to be captured per-run or results from different days aren't comparable.
  - *Judge bias*: an LLM-as-judge for "quality" injects the judge's own preferences and is known to favour its own
    style, longer answers, and surface polish. Judge scores are only comparable when the judge model and judge
    prompt are frozen across the comparison, and even then they are a *signal* alongside static metrics — not the
    quality score on their own. Pair every reported judge score with at least one objective metric.
  - PII/secrets/audit: N/A — synthetic tasks, no real data.

- **Task validity**: tasks must actually discriminate between styles. A trivial task ("print hello world") gives
  every style 100% and tells us nothing. A task that's too hard gives every style 0%. Task selection is part of
  the harness's correctness, not a side concern — tasks need to be calibrated.

- **Determinism**: even at `temperature=0`, model outputs can drift across days and provider-side updates. The
  harness should pin model snapshot ids where available and record them per-run, and re-runs of "the same eval"
  should be treated as a new sample, not an authoritative replay.

## Constraints

- Runs locally on the developer's machine first; cloud / batch execution is a later concern.
- Uses the Claude Code SDK (per CLAUDE.md global defaults). Prompt caching enabled where it helps.
- Python 3.12+, async, `uv` for deps, `ruff` + `pytest` (project conventions).
- Generated code is executed via `subprocess` in a `tempfile.TemporaryDirectory()` with a hard timeout and SIGKILL
  on overrun. Bwrap/Docker reuse is deferred to the eventual jig fold-in; not needed for v1.
- Judge is the same family as the candidate (Claude), a different snapshot where possible. Self-preference bias
  is mitigated by pairing every judge score with static metrics in the report, not by switching family.
- v1 corpus: 3 tasks. Grow toward 6–8 once the harness is proven and the existing tasks stop discriminating
  between styles. No fixed up-front target.
- Lives as a sibling under `evals/` (i.e. `evals/prompt_style_eval/`), not bolted into `evals/watcher/`.

## Requirements

- Run a single `(style, task, seed)` cell, a slice (e.g. one style × all tasks), or the full matrix from one
  command.
- Every run persists: prompt sent, raw model output, extracted code, test results, judge output, model id,
  temperature, timestamp, token usage, and cost.
- Re-scoring (e.g. swapping the judge prompt) can be done against persisted runs without calling the model again.
- Output an aggregate report comparing styles on the three axes: correctness (pass@1 across N seeds), consistency
  (variance / failure-bucket breakdown across seeds), quality (judge checklist + static metrics, reported
  separately so the judge isn't laundered into a single number), with bootstrap confidence intervals.
- Quality rubric is a fixed checklist of binary or short-scale items (e.g. type hints present, no bare except,
  function names descriptive, scope appropriate, no obvious dead code), not free-form judge prose. The checklist
  is versioned and frozen for the duration of a comparison.
- Adding a new task is a single-directory change. Adding a new prompt variant for an existing task is a
  single-file change inside that task.
- A task is a *hidden test suite plus its prompt variants*, not a structured spec. The variants are
  hand-authored. Two valid framings exist and each task picks one (documented in its `README.md`):
  - *Detail-matched*: every variant encodes the same concrete requirements (format strings, exit codes,
    error behaviors), so the only thing varying is the wire form. Isolates "form" as the eval variable.
  - *Natural-voice*: each variant is written in the voice/density natural to its form — jig's structured
    spec carries explicit acceptance criteria; user-story prose stays high-level and lets the model infer
    specifics. Measures the comparison that actually matters in practice (jig's detailed spec vs the
    user-story-level prose people would naturally write), at the cost of conflating form and detail.
- Failed runs (timeout, malformed output, judge error, agent-asked-a-question) are recorded as outcomes, not
  dropped, and surfaced in the per-style report as their own bucket.

## Non-goals

- Not benchmarking models. The model is pinned per eval; we are comparing prompt styles, not
  Claude vs others or Opus vs Sonnet.
- Not a general-purpose eval framework. Scope is code-generation tasks from a natural-language spec, scored
  against hidden tests + a quality rubric. Not RAG, not classification, not chat-quality.
- Not novel research. Borrow established patterns (HumanEval-style hidden tests, LLM-as-judge with frozen
  rubric) — don't invent.
- Not measuring `pass@k` for k>1 in v1. We measure pass@1 over many seeds; `pass@k` is more useful for
  comparing sampling strategies than prompt styles. Add later if needed.
- Not integrated with the jig orchestrator *yet*. This is an offline harness; it does not spawn agents through
  jig, and conflating the two now adds dependencies without value. However, the harness is likely to be folded
  into jig later (e.g. so role/reviewer prompts can be A/B-tested in-tree), so it should not adopt patterns or
  dependencies that would block that — task definitions, prompt templates, and the results store should be plain
  data on disk, not tied to a runner that only this harness can drive.
- Not a UI. CLI + JSONL + a printed table is enough.

## Success criteria

- Running `uv run python -m jig.evals.prompt_style_eval --style terse --style verbose-cot --task todo-cli --seeds 10`
  produces a comparison table with pass-rate, consistency, and quality scores per style, plus a path to the
  persisted JSONL.
- Re-running the same command without changes produces the same persisted results (cached) unless `--force` is
  passed.
- Adding a new prompt variant for an existing task requires only writing a new file under that task's
  `prompts/` subdirectory.
- For any pair of prompt styles and a fixed set of tasks+seeds, the harness produces per-style pass-rate,
  variance across seeds, judge score, static-metric summary, and bootstrap confidence intervals — without
  harness-code changes. (YAML-vs-prose is the first comparison we'll run through it; the harness should not be
  any easier or harder for that one than for the next.)

## Open questions

All v1 blockers resolved — decisions captured in Constraints / Requirements / Non-goals above. Recorded here as
audit trail.

- [x] **Task corpus size.** Start with 3 tasks; grow toward 6–8 as the existing corpus stops discriminating. No
      fixed up-front target.
- [x] **Sandbox for generated code.** Subprocess + `tempfile.TemporaryDirectory()` + hard timeout + SIGKILL.
      Bwrap/Docker reuse is deferred to the eventual jig fold-in.
- [x] **Judge model.** Same family (Claude), different snapshot from the candidate where possible. Self-preference
      bias mitigated by pairing every judge score with static metrics, not by switching family.
- [x] **Quality rubric.** Fixed, versioned checklist of binary or short-scale items. Free-form judge prose is not
      aggregable.
- [x] **`pass@k` for k>1.** Not in v1. Pass@1 across N seeds gives the variance signal we want for prompt-style
      comparison.

Deferred to the design doc (not blockers for the problem statement):

- [ ] Exact set of v1 tasks (the 3 we start with).
- [ ] Exact checklist items and scoring scale.
- [ ] Caching / re-run semantics (content-hash on prompt+task+model, or explicit run-id?).

## Change log

- 2026-05-12: Initial draft (brent)
- 2026-05-12: Add motivating-questions section (YAML-vs-prose called out as the first test, not as the harness's
  reason for being); reframe LLM-as-judge as a signal paired with static metrics, not the quality score; loosen
  the jig-integration non-goal to "not yet, but design with eventual fold-in in mind"; require format-independent
  task requirements so any spec encoding is a prompt-style variant (brent)
- 2026-05-12: Add "agent comes back with a question" as a recorded failure outcome (not auto-retried, surfaced
  as its own bucket per style); resolve all v1 open questions — 3 starter tasks growing to 6–8, subprocess +
  tmpdir sandbox, same-family judge with frozen checklist, pass@1 over many seeds, no pass@k in v1 (brent)
- 2026-05-12: Drop "format-independent structured requirements" requirement. Prompts are now hand-authored plain
  text files per `(task, style)` pair; equivalence between styles is a review concern, not a mechanical
  property. Adding a new style is no longer a single-file change in the global sense — it's one file per task.
  Updated to match the simplified design (brent)
