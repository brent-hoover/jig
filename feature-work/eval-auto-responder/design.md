---
title: Eval Auto-Responder — Design
type: design
status: draft
owner: Brent Hoover
created: 2026-06-10
updated: 2026-06-10
problem: ./problem.md
---

# Eval Auto-Responder — Design

## Summary

Add a responder task to the eval runner that watches the WebSocket frame stream it already receives and answers the
orchestrator's `prompt_request` events — the prompts that, in an attended run, the TUI shows to the operator. The
responder replies with a single canned answer via the typed `prompt_reply` command, which resolves the orchestrator's
parked future; the orchestrator then posts the Answer, marks the question resolved, and resumes the ticket through
its own production code path. A per-ticket cap (3 replies) guards against re-ask loops; on cap, the responder stops
and the existing `bus_silence` stall fires as the backstop. The answer policy is a one-method protocol so scripted or
LLM answers can drop in later without touching the responder.

## How resume actually works (mechanism this design targets)

When an agent posts a blocking question, the ticket flips to `needs_info` and the orchestrator's phase loop parks in
`_prompt_for_needs_info` (`jig/orchestrator.py:2638`): it finds the most recent unresolved blocking question,
registers a future in the `PromptRegistry`, emits a `prompt_request` event (topic `"prompts"`, kind `"request"`)
whose payload carries `prompt_id`, `prompt_type: "question_answer"`, `ticket_id`, `asker`, and the question text
(under both `question_text` and `question` keys), and then `await`s the future. The typed WS command `prompt_reply` (`jig/tui/commands/prompt_reply.py`) delivers a reply
into the registry, resolving the future. The orchestrator then posts an `Answer` authored `"operator"`, sets
`resolved_by` on the question, and flips the ticket back to `in_progress` (`jig/orchestrator.py:2687-2696`).

Two facts that shaped this design (both verified):

- The legacy `answer_questions` WS command does **not** resume the orchestrator: it posts an Answer and flips ticket
  status, but never resolves the question (`Answer` does not auto-resolve — `jig/thread.py:143-144`) and never
  completes the parked future. The coroutine stays parked and the run stalls anyway.
- The runner already subscribes to the `"prompts"` topic (`ALL_TOPICS`, `jig/eval/runner.py:28`), so `prompt_request`
  frames are already arriving on the socket today — unanswered.

## Approach

### Components

**`jig/eval/responder.py`** (new) — two pieces:

1. `AnswerPolicy` protocol: `def answer(self, ticket_id: str, question: str) -> str`. One implementation in v1,
   `CannedAnswerPolicy`, which ignores its inputs and returns a constant:
   > "Approved — proceed. Use your best judgment on any open details; pick reasonable defaults and note them in the
   > ticket."
2. `auto_responder(queue, ws, *, policy, max_replies_per_ticket=3, log=...)` — an async task that consumes frames
   from its own `asyncio.Queue` (fed by the runner's existing `_dispatch` fan-out) and replies to question prompts.

**`jig/eval/runner.py`** (modified) — `_dispatch` gains a third queue (`responder_q`); `run_eval` creates the
responder task alongside `_watch_completion` / `_watch_stall` and cancels it with the others when the race settles.

### Responder loop

For each frame from the queue:

1. **Match the trigger.** React only to `msg["topic"] == "prompts"`, `msg["kind"] == "request"`, and
   `msg["data"]["prompt_type"] == "question_answer"` — the needs-info prompt shape emitted at
   `jig/orchestrator.py:2663-2674`. Extract `prompt_id`, `ticket_id`, `question_text`. Other prompt types are logged
   at WARNING and ignored (see Risks).
2. **Dedupe.** Skip `prompt_id`s already replied to (a `set`). This is defensive, not load-bearing: in the
   steady-state run each prompt arrives exactly once via the live relay (`jig/ws_server.py:736-737`) — the
   pending-prompt re-emission (`jig/ws_server.py:172-183`) fires only on a fresh `subscribe`, which the runner sends
   once at startup. The set matters only if a future runner change reconnects/re-subscribes mid-run. It never blocks
   a legitimate re-ask: each `needs_info` episode calls `PromptRegistry.register()` afresh, so a new question always
   carries a new `prompt_id`.
3. **Check the cap.** A `dict[str, int]` of replies per `ticket_id`. At `max_replies_per_ticket`, log at ERROR
   ("auto-responder cap hit for ticket X — leaving it blocked") and skip. The ticket stays `needs_info` and the
   StallDetector's existing thresholds end the run.
4. **Reply.** Log the question text and answer at INFO, then send the typed command
   `{"type": "command", "name": "prompt_reply", "args": {"args": [prompt_id, policy.answer(ticket_id,
   question_text)]}}`. The handler resolves the registry future; the orchestrator does the rest (Answer post,
   `resolved_by`, status flip) through its production path.
5. **Record the reply** (dedupe set + cap counter) and go back to waiting.

The responder does not await the command result synchronously — the `{"type": "result", ...}` frame arrives on the
shared stream and is fanned out to all queues. Failure results are **uncorrelatable**: `prompt_reply` returns
`prompt_id` only on success; the failure shape is `{"type": "result", "ok": false, "error": ...}` with no
prompt_id, topic, or kind (`jig/tui/commands/prompt_reply.py:29-33`, `jig/ws_server.py:534`). The responder
therefore logs a WARNING with the error string on any `type == "result"` frame with `ok: false` — it cannot
attribute the failure to a ticket, and does not retry. Every failure mode here is bounded the same way: an
undelivered reply leaves the future parked and the stall backstop ends the run.

### Multiple questions / re-asks

The orchestrator prompts for one question per `needs_info` episode (the most recent unresolved blocking question) and
resumes the ticket after one answer. If other questions remain unresolved or the agent re-asks, the ticket flips to
`needs_info` again and a *new* `prompt_request` (new `prompt_id`) arrives — the responder answers each episode as it
comes. Convergence is driven by the orchestrator's own episode loop, bounded by the per-ticket cap. In the observed
hn-cli run (2 questions in one batch) this means: prompt for the approval question, canned answer covers both ("use
your best judgment"), PM proceeds; if it instead re-asks the `--limit` question, that's episode 2 of 3.

### Concurrency

The runner keeps its single WS reader (`_dispatch`); the responder only sends, and `websockets` permits concurrent
senders. `prompt_reply` is a typed command, so it returns from `_handle_incoming` before the legacy history-replay
branch — no replay side effect, no legacy-path coupling. Success result frames carry `prompt_id` in `data`; failure
frames are matched only by `type == "result", ok == false` (see Responder loop) — no shape-based matching beyond
that.

## Interfaces

- `AnswerPolicy` protocol (new, `jig/eval/responder.py`): `answer(ticket_id, question) -> str`. The seam for future
  `answers.yaml` or LLM policies; v1 ships only `CannedAnswerPolicy`.
- `run_eval` gains no new parameters. The cap and policy are module constants in v1 — no operator knob until a
  second policy exists.
- WS wire usage (existing, unchanged): consumes `prompt_request` envelopes on the `prompts` topic; sends the typed
  `prompt_reply` command (`{"type": "command", "name": "prompt_reply", "args": {"args": [prompt_id, reply]}}`,
  handled by `jig/tui/commands/prompt_reply.py`).

## Data model

No new persistent state. Auto-answers persist through the production path the orchestrator already implements for
operator replies: an `Answer` thread entry authored `"operator"` bound to the question id, `resolved_by` set on the
question, and a status flip back to `in_progress` — byte-identical to a human answering in the TUI. Post-mortem
reads them via `jig story <ticket-id>` or the store JSONL directly.

## Alternatives considered

### Trigger on the `needs_info` ticket-status frame + legacy `answer_questions` command

The original draft of this design (and the trigger decision recorded in problem.md). Rejected on verification:
`answer_questions` flips ticket status but never resolves the question and never completes the future the
orchestrator is parked on (`jig/ws_server.py:414-499` vs `jig/orchestrator.py:2657,2682`), so the agent is never
re-dispatched and the run stalls anyway. The `needs_info` frame also carries no `prompt_id`, which the real resume
path requires. The legacy command path additionally drags in a one-time history replay of up to 1000 raw frames and
uncorrelated responses — all of which the typed `prompt_reply` path avoids. Problem.md's trigger decision is
superseded by this section (noted in its change log).

### Subscribe to `threads` and react to question frames

React to `comment_posted` frames with `comment_kind == "question"`. Rejected: the relayed payload lacks `target`,
`blocking`, and any prompt linkage; it triggers on non-blocking questions too; and it still leaves the parked future
unresolved without a `prompt_id`.

### LLM-generated answers

Rejected in the problem statement (see non-goals there): answer variance destroys failure attribution, and the
synthetic-operator simulator set the precedent (`jig/sim/policy.py` — deterministic templates, LLM deferred). The
`AnswerPolicy` seam keeps the door open.

### A separate "operator bot" process

A standalone process connecting as a second WS client. Rejected: needs its own lifecycle management in the runner
for no benefit — the runner already owns a socket that receives every `prompt_request`.

### Chosen: responder task inside the runner, `prompt_request` trigger, typed `prompt_reply`

It is the exact mechanism a human operator uses, one hop earlier: same prompt, same command, same orchestrator-side
resume code. No new subscriptions (`prompts` is already in `ALL_TOPICS`), no legacy-path coupling, correlated
request/response by `prompt_id`, and the question text rides in the trigger frame so logging needs no extra round
trip.

## Risks

- **Prompt types other than `question_answer`.** If a future orchestration phase emits a different blocking
  `prompt_request` type during an eval, the responder ignores it and the run stalls. That stall is correct-by-policy
  (we only auto-answer agent questions), but the WARNING log must make the cause obvious. Revisit if a new prompt
  type becomes a standard part of unattended runs.
- **No-question pause paths.** `_prompt_for_needs_info` falls back to silent polling when a ticket is `needs_info`
  with no unresolved blocking question (`jig/orchestrator.py:2652-2655`). No prompt is emitted, so the responder
  never fires and the run stalls. N/A for the observed flow (blocking `ask_question` always creates the question
  first); stated so the assumption is explicit.
- **Reply delivered but orchestrator fails before resuming.** The future resolves, then the Answer post or status
  update raises. The reply is consumed (`ok: true`) but the ticket stays blocked, and no recovery path exists: the
  prompt is already popped from the registry, and the ws_server's `_pending_prompts` replay-to-new-subscribers
  would filter it as stale anyway (`jig/ws_server.py:174-176`). Backstop: stall thresholds. Acceptable — this is an
  orchestrator bug surfacing, which is exactly what an integration eval should expose loudly rather than paper
  over.
- **Cap interplay with the StallDetector.** Each reply resets the episode; a cap-hit ticket then sits in
  `needs_info` until `bus_silence_seconds` or `unanswered_needs_info_seconds`
  (`jig/evals/watcher/stall_detector.py:124-137`) fires. Today's thresholds (minutes) dwarf responder latency
  (milliseconds); no synchronization needed.

## Out of scope

- `answers.yaml` per-fixture scripted answers (deferred in problem.md; `AnswerPolicy` is the hook).
- Any change to role prompts, the orchestrator, `ws_server.py`, the `prompt_reply` handler, or the TUI.
- Answering during normal (non-eval) operation.
- A distinct outcome/exit code for cap-hit (falls through to stall by decision in problem.md).
- Handling prompt types other than `question_answer` (logged and ignored).

## Open questions

- None. The trigger decision recorded in problem.md (needs_info frame + `answer_questions`) was made before the
  resume mechanism was verified and is superseded by the `prompt_request`/`prompt_reply` design above; problem.md's
  change log records the supersession.

## Change log

- 2026-06-10: Initial draft — needs_info trigger + legacy `answer_questions` (Brent Hoover)
- 2026-06-10: Rewritten after design review: `answer_questions` cannot resume the parked orchestrator coroutine;
  re-targeted at the production `prompt_request`/`prompt_reply` path (Brent Hoover)
- 2026-06-10: Review round 2 fixes — failure result frames carry no prompt_id (uncorrelated WARNING), dedupe set is
  defensive not load-bearing, named both stall thresholds (Brent Hoover)
