# Eval Watcher + Analyzer — Design Sketch

Status: design only, nothing implemented. Iterate on this doc before
building.

## Problem

Eval runs need automated supervision because:

- **Stalled runs eat compute** when an agent loops, a `needs_info`
  prompt sits unanswered, or the phase pipeline oscillates.
- **Successful runs hide quality issues** that we'd see if a reviewer
  looked at the trajectory: phases retried, dead-end paths, redundant
  agent thinking, agents asking the operator for verifiable facts.

We want a process that lives above the daemon and can observe both the
shape of the run and its outcome.

## Scope

Two responsibilities, intentionally separated:

1. **Stall detector** — runs concurrently with the eval. Watches the
   bus + ticket store + daemon log for activity heuristics. When
   thresholds trip, it kills the run (or marks it stalled) and hands
   off to the analyzer.

2. **Analyzer** — runs after the eval terminates, regardless of how it
   ended. Reads the persisted artifacts (`.jig/store/*.jsonl`,
   `.jig/logs/*.jsonl`, ticket worktrees, git history) and produces a
   markdown report covering trajectory, decisions, time-spent, and
   improvement notes.

Reasoning for separation: stall detection is a real-time concern with
process-control needs; analysis is a batch concern that benefits from
the full artifact set being final. Keeping them apart means the
analyzer can be re-run on any historical eval, including ones that
weren't originally watched.

## Stall heuristics

All thresholds configurable; defaults below are starting guesses.

| Signal | Default threshold | Notes |
|---|---|---|
| No bus event published | 5 minutes | Strongest signal — daemon is alive but nothing's progressing. |
| No `agent_thinking` heartbeat | 90 seconds | Catches agents that hung mid-tool-call. |
| Same ticket in `needs_info` | 30 minutes | Operator never answered — eval is unattended, treat as stalled. |
| Phase oscillation (same ticket bouncing review→dev→review) | 4 cycles | Already partially capped at 3 in orchestrator; watcher catches the rare case it slips. |
| Single agent run wall time | 30 minutes | Some legitimate runs are long, so this is a safety net not a precision instrument. |
| Daemon process dead | immediate | Watcher self-promotes the run to FAILED, runs analyzer. |

A run can also end "cleanly" by reaching `resolved` on every top-level
ticket. The watcher emits a single `eval_outcome` event (`stalled` /
`failed` / `succeeded`) into a watcher log so the analyzer has
ground truth.

## Analyzer scope

The analyzer is itself a Claude agent (probably `claude-agent-sdk` like
the rest of jig's agents) given:

- The full ticket store as JSONL.
- All thread comments and system events.
- The run's daemon log.
- Final git history of the project repo (commits per ticket branch).
- Watcher's `eval_outcome` verdict.

It produces `evals/runs/<run-id>/analysis.md` with:

- **Outcome** — succeeded / stalled / failed, time taken, ticket count.
- **Trajectory** — bullet timeline of major events (phases entered,
  questions asked, retries, merges, key decisions).
- **Operator-question quality** — for each `ask_question`, judge
  whether the question was verifiable with `WebFetch` / `Read` /
  `Bash` (PM should have looked it up itself per `pm.yaml` step 3).
  Sets `metrics.operator_questions.verifiable_in_hindsight`.
- **Friction (efficiency)** — retried phases, tickets that ran
  disproportionately long, agents re-reading the same file, expensive
  individual agent runs, phase oscillations.
- **Brief fidelity** — were any items from `brief.md` dropped without
  notice? List behaviors / acceptance criteria / non-goals from the
  brief that don't appear in any resulting ticket, commit, or test.
  Distinguish "deferred with rationale" from "silently dropped."
- **Right-sizing the build** — does code complexity match project
  breadth? Flag both over-engineering (premature abstractions,
  unused config layers, factories around one-call sites, scope creep
  beyond the ticket) and under-engineering (missing error handling
  at boundaries, edge cases from spec acceptance criteria not covered,
  one-file dumps for systems that warrant separation).
- **Tool-use anti-patterns** — repeated identical reads, missed bulk
  operations (Grep/Glob instead of N reads), bash for things with
  dedicated tools, large ToolSearch result sets that weren't used.
- **Quality observations** — even on successful runs: was the
  dependency graph too linear when work could have parallelized? Did
  `review` catch real issues or rubber-stamp? Are tests proportional
  to risk?
- **Recommendations** — concrete, actionable. Tagged `blocking` /
  `improvement` / `nit`. Each one names the specific file, prompt,
  workflow phase, or brief section to change.

The analyzer prompt should be explicit that successful runs still get
the full review treatment — "what could have been approved more
efficiently" is the framing, not "what went wrong."

## Output format (v1, schema-versioned)

The analyzer produces TWO files per run, side-by-side:

- `analysis.md` — human-readable narrative (the same trajectory /
  friction / quality / recommendations sections described above).
- `metrics.json` — machine-readable summary that aggregates cleanly
  across runs. This is what the regression dashboard reads.

`metrics.json` schema (v1):

```json
{
  "schema_version": 1,
  "run_id": "hn-cli-20260507-103000",
  "project": "hn-cli",
  "outcome": "succeeded",
  "outcome_reason": "all top-level tickets resolved",
  "started_at": "2026-05-07T17:30:00Z",
  "ended_at":   "2026-05-07T18:14:22Z",
  "duration_s": 2662,

  "tickets": {
    "total": 9,
    "resolved": 8,
    "failed": 1,
    "needs_info_max_minutes": 12.4
  },

  "phases": {
    "spec":      {"runs": 8, "retries": 0, "avg_turns": 11.2},
    "test":      {"runs": 8, "retries": 1, "avg_turns": 18.7},
    "implement": {"runs": 8, "retries": 0, "avg_turns": 22.1},
    "review":    {"runs": 8, "retries": 2, "avg_turns":  9.5},
    "validate":  {"runs": 8, "retries": 0, "avg_turns":  6.2},
    "document":  {"runs": 8, "retries": 0, "avg_turns":  7.8}
  },

  "agents": {
    "total_turns":      612,
    "total_cost_usd":   4.27,
    "total_tokens_in":  812345,
    "total_tokens_out": 198432
  },

  "operator_questions": {
    "asked": 4,
    "verifiable_in_hindsight": 1,
    "product_scope": 3
  },

  "stalls": {
    "detected": 0,
    "signal": null
  },

  "merge_failures": 0,
  "review_blocks": 2,
  "watcher_version": "1.0.0",
  "jig_commit": "<git sha>",
  "tags": ["nightly", "ci"]
}
```

Field notes:

- `outcome` ∈ `succeeded` | `stalled` | `failed`.
- `stalls.signal` (when present) ∈ `bus_silence` | `heartbeat_gap` |
  `unanswered_needs_info` | `phase_oscillation` | `wall_time` |
  `daemon_dead`.
- `operator_questions.verifiable_in_hindsight` is the analyzer's
  judgment of how many operator questions the PM could have answered
  itself with a tool call (per role-prompt guidance in `pm.yaml`).
- `tags` is free-form labels for filtering in the dashboard
  (`nightly`, `ci`, `manual`, `regression-pin`, etc.).

Fields evolve via `schema_version`; the dashboard ingests the highest
version it knows and ignores newer fields it doesn't understand.

## Regression dashboard (v1 minimum)

The dashboard is the natural consumer of `metrics.json`. v1 doesn't
need a UI — it's a script that walks `evals/runs/*/metrics.json` and
emits a summary table:

```
$ scripts/eval_dashboard.py --project hn-cli --last 10

run_id                       outcome   dur   tickets   $cost   turns   stalls
hn-cli-20260507-103000       success   44m   8/9       4.27    612     0
hn-cli-20260506-203000       fail      18m   3/9       1.84    241     1 (heartbeat_gap)
hn-cli-20260506-180000       success   51m   9/9       5.11    701     0
...
```

A second mode (`--regress`) flags metrics that have moved
unfavorably vs the rolling median: cost up >25%, duration up >25%,
operator questions up, review_blocks up. This is what we'd surface
as a CI check status / PR comment.

## CI integration (target state)

GitHub Actions workflow per project:

1. Checkout jig + the eval project.
2. Run `python -m evals.watcher.run --project <name>` which:
   - Starts the daemon
   - Attaches the watcher
   - Runs the eval to completion (or stall-kill)
   - Runs the analyzer
   - Writes `analysis.md` + `metrics.json` to
     `evals/runs/<run-id>/`
3. Upload `evals/runs/<run-id>/` as a workflow artifact.
4. Run `scripts/eval_dashboard.py --regress` to check the new run
   against historical baselines; emit a check failure (or PR comment)
   on regression.

Cost guard: CI runs default to nightly, not per-PR. Per-PR runs are
opt-in via a label (e.g. `eval:full`) so we don't spend $5+ on
trivial doc changes.

Determinism guard: the watcher and analyzer are themselves agents
calling out to LLMs, so their reports are nondeterministic.
`metrics.json` is the comparison surface, not `analysis.md` — the
narrative is for humans, the metrics are for diff'ing.

## Layout (proposed)

```
evals/
  watcher/
    DESIGN.md             ← this file
    __init__.py
    stall_detector.py     ← long-running watcher process
    analyzer.py           ← post-run agent invocation
    run.py                ← CLI entry: starts daemon, attaches watcher,
                            runs eval, invokes analyzer on termination
    heuristics.py         ← thresholds + signal definitions
    metrics.py            ← metrics.json schema + aggregation helpers
    prompts/
      analyzer.md         ← system prompt for the analyzer agent
  runs/
    <run-id>/
      eval.log            ← watcher's verdict + signal traces
      analysis.md         ← analyzer's narrative report
      metrics.json        ← machine-readable summary (dashboard input)
      ticket-snapshot.json
      thread-snapshot.json
scripts/
  eval_dashboard.py       ← reads metrics.json across runs; --regress mode
.github/workflows/
  evals.yml               ← nightly + opt-in PR eval runs
```

## Open questions

- Should the watcher kill the daemon outright, or signal it via a new
  `/abort` command that records the stall in the ticket store first?
  (Probably the latter — keeps the artifact trail consistent.)
- Should successful-run analysis be opt-in or always-on? Always-on
  generates more compute cost but also more learning data.
- Should the analyzer have web access? Useful for verifying API claims
  in retrospect, but adds nondeterminism to the eval.
- Regression thresholds are starting guesses (cost +25%, duration +25%);
  tune from real data once we have a few runs.
