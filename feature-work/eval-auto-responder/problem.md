---
title: Eval Auto-Responder — Problem Statement
type: problem
status: draft
owner: Brent Hoover
created: 2026-06-10
updated: 2026-06-10
---

# Eval Auto-Responder — Problem Statement

## Context

`jig eval run <project-id>` (shipped in PR #152, fixed in #156) runs a zero-touch integration test: unattended init,
`jig start --no-docker` as a subprocess, then a race between `project_complete`, the StallDetector, and a wall-clock
timeout. The intent is that an operator can run one command and get a pass/fail signal for the full orchestration
pipeline with no human in the loop.

The first real run (`hn-cli`, 2026-06-10) got through init, brief, and architecture, then stalled. The PM agent
finished drafting its plan and called `ask_question` twice with `target="any_human"` and `blocking=true`:

1. "What should the default value for `--limit N` be... 10 or 30?"
2. "Does the plan above look correct? If so, please approve and I'll create the 4 tickets."

The planning ticket flipped to `needs_info`, the bus went silent, and the StallDetector correctly fired `bus_silence`
after 305s. Outcome: `stall`, exit code 1.

## Problem

The orchestration workflow has human gates by design. The PM role prompt mandates one
(`jig/defaults/roles/pm.yaml:120-122`): present the plan, "call `ask_question` asking the user to approve," and create
tickets "only after explicit user approval." Any agent may also ask blocking clarifying questions at any phase.

A zero-touch eval has no human, so every eval run is guaranteed to stall at the PM plan-approval gate — and may stall
earlier or later on any other blocking `any_human` question. `jig eval run` as it exists today can never produce a
SUCCESS outcome. The stall outcome is indistinguishable from a genuine pipeline failure (an agent crashing or wedging),
so the eval currently has zero signal value.

Nothing in the codebase answers questions unattended. The only answer paths are the TUI (which sends the
`answer_questions` WS command) and operator MCP/CLI surfaces — all human-driven.

How question events surface on the wire (verified against `ws_server.py` / `ticket_mcp.py`):

- A posted question is relayed to typed WS subscribers on topic `"threads"`, kind `"posted"`, with payload fields
  `comment_kind: "question"`, `ticket_id`, `content`, `author` — but **not** `target` or `blocking`
  (`jig/ticket_mcp.py:442-457`, `jig/ws_server.py:724-725`).
- The eval runner subscribes to `ALL_TOPICS = ("tickets", "spec", "agents", "events", "prompts")`
  (`jig/eval/runner.py:28`) — `"threads"` is not in the list, so today's runner never receives question frames at
  all. It does receive the `ticket_updated` frame (status → `needs_info`) on the `tickets` topic.
- Full question detail including `target` and `blocking` is available on demand via the WS `list_comments` command
  (`jig/ws_server.py:309-317`, `_thread_entry_to_wire` at `:752`).

## Simplest possible solution

The eval runner already holds an open WebSocket connection to the orchestrator and already consumes every bus frame
(it feeds them to the StallDetector). The WS server already exposes an `answer_questions` command
(`jig/ws_server.py:414`) that binds answers oldest-to-newest to a ticket's open questions and, with `resume=True`
(the default), flips `needs_info` back to in-progress.

So: when the runner sees a ticket flip to `needs_info` (or a question frame, if it also subscribes to `"threads"`),
send `answer_questions` back over the same socket with a canned answer — approve approval-style questions, and tell
the agent to use its best judgment for everything else. `answer_questions` binds answers to all open questions on the
ticket, so the trigger frame doesn't need to carry the question details. No new processes, no new config surface, no
changes to agent roles or the orchestrator.

## Complications considered

- **Scale**: N/A — bounded by the number of blocking questions per eval run, which is small (single digits).
- **Concurrency**: The runner is the only "human" in an eval, so there is a single answerer. One fact to handle:
  multiple blocking questions can be open on a ticket simultaneously (observed: 2 in the hn-cli run, posted in the
  same batch). `answer_questions` binds answers in order, so the responder must answer all open questions on the
  ticket, not just one.
- **Failure modes**:
  - The answer command fails or the orchestrator misses it → ticket stays `needs_info`, bus stays silent, the
    StallDetector fires exactly as today. The existing stall outcome is the backstop; no new handling needed.
  - A canned answer sends the agent down a bad path (e.g. approving a broken plan) → the run proceeds and either
    completes with a failing tracer or stalls later. Both are visible outcomes; acceptable for an integration test
    whose job is to exercise the pipeline, not to make optimal product decisions.
  - An answer loop (agent re-asks the same question after an unhelpful canned answer) → bounded by the wall-clock
    timeout, but worth a cap so the outcome is attributable (see Open questions).
- **Cross-cutting policies**: N/A — eval-only code path, local-only WS connection, no secrets or PII. Answers should
  be recorded in the store like any operator answer (they already would be, via the normal `answer_questions` path),
  so the run remains auditable after the fact.
- **Fidelity**: the eval should exercise the production orchestrator and agent prompts. Auto-answering keeps the PM's
  ask-then-wait flow intact; alternatives that strip the approval gate (a config flag, prompt surgery for evals) test
  a different system than the one that runs in production. This is the reason the responder approach is preferred.

## Constraints

- Must not change agent role prompts or orchestrator behavior — the eval has to test what production runs.
- Must work over the runner's existing single WS connection, which has a single reader (`_dispatch`).
- Answers must go through the existing `answer_questions` path so they are persisted and the resume semantics stay in
  one place.
- Python 3.12 async, in `jig/eval/`, consistent with the existing runner.

## Requirements

- A zero-touch eval run reaches `project_complete` on a well-behaved project without human input, passing every
  blocking `any_human` question gate along the way.
- Approval-style questions (plan approval, confirm-to-proceed) are answered affirmatively.
- Clarifying questions are answered with a deterministic response that lets the agent proceed on its own judgment.
- Every auto-answer is persisted in the project store the same way an operator answer is.
- Auto-answer activity is visible in the runner's logs (question text + answer given), so a post-mortem of a failed
  run shows what the responder did.
- If auto-answering cannot unblock a ticket, the run still terminates with the existing stall/timeout outcomes.

## Non-goals

- LLM-generated answers. Canned/deterministic responses only — the eval must be reproducible and cheap. With LLM
  answers, a red run has three possible causes (pipeline regression, agent regression, answer variance) and loses
  attribution. This mirrors the synthetic-operator simulator's decision (`jig/sim/policy.py`): deterministic
  templates, "reproducibility is load-bearing," LLM generation deferred. If a fixture needs a real decision the brief
  didn't make, the design may offer per-fixture scripted answers (e.g. `evals/projects/<id>/answers.yaml`) — still
  deterministic, controlled by the fixture author. The answer policy should be a small interface so an LLM responder
  could drop in later without rework.
- Answering non-blocking questions or participating in design discussions.
- Auto-answering outside eval runs. The TUI and operator surfaces remain the only answer paths in normal operation.
- Changing the PM approval gate or any role prompt.
- Evaluating plan quality. The responder approves whatever plan the PM presents; plan quality is judged downstream by
  the tracer and any analysis tooling.

## Success criteria

- `jig eval run hn-cli` gets past the planning phase without manual intervention (the gate where every run dies
  today). Depends on the `evals/projects/hn-cli/` fixture (brief.md + tracer.sh), which exists.
- A full run can reach `outcome: success` with a passing tracer, exit code 0.
- The store for an eval run shows the questions asked and the auto-answers given.
- Existing runner tests still pass; new tests cover the answer-on-question path and the answer-failed-still-stalls
  backstop.

## Open questions

All resolved 2026-06-10 (operator decisions):

- [x] **Answer policy**: no classification — a single canned answer (e.g. "Approved — proceed; use your best judgment
      on any open details.") covers both approval-style and clarifying questions. The agent asking is capable of
      deciding once given license.
- [x] **Per-fixture scripted answers** (`evals/projects/<id>/answers.yaml`): deferred. No current fixture needs it.
      The answer policy stays behind a small interface so scripted answers (or an LLM responder) can drop in later.
- [x] **Loop guard**: cap auto-answer rounds per ticket (small, e.g. 3). On hit, stop answering, log loudly, and let
      the existing `bus_silence` stall fire. No new outcome enum or exit code; store + logs carry the post-mortem.
- [x] **Trigger**: ~~the `ticket_updated` → `needs_info` frame + `answer_questions`~~ **Superseded during design**
      (2026-06-10): verification showed `answer_questions` never resumes the orchestrator — it parks on a
      `PromptRegistry` future in `_prompt_for_needs_info` (`jig/orchestrator.py:2638`) that only the typed
      `prompt_reply` command resolves. The design instead triggers on `prompt_request` frames (topic `"prompts"`,
      already in the runner's subscriptions) and replies via `prompt_reply` — the same mechanism a human operator
      uses in the TUI. See design.md "How resume actually works".

## Change log

- 2026-06-10: Initial draft (Brent Hoover)
- 2026-06-10: Expanded LLM-answers non-goal with rationale and the per-fixture scripted-answers design option (Brent
  Hoover)
- 2026-06-10: Resolved all open questions — single canned answer, answers.yaml deferred, loop cap falls through to
  stall, needs_info trigger (Brent Hoover)
- 2026-06-10: Trigger decision superseded during design — `answer_questions` cannot resume the parked orchestrator;
  design uses `prompt_request`/`prompt_reply` instead (Brent Hoover)
