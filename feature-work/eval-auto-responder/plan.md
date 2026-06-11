---
title: Eval Auto-Responder — Implementation Plan
type: plan
status: archived
owner: Brent Hoover
created: 2026-06-10
updated: 2026-06-11
design: ./design.md
---

# Eval Auto-Responder — Implementation Plan

## Overview

Implement `jig/eval/responder.py` (policy protocol + responder loop) with unit tests first, then wire it into
`run_eval`'s task fan-out, then validate end-to-end with `jig eval run hn-cli`. Module-first order so the responder
logic is fully tested against fake frames before it touches the runner's task choreography. One PR for the whole
feature; steps 1–3 are commits within it.

## Preconditions

- [x] problem.md approved (open questions resolved 2026-06-10; trigger decision superseded — see its change log)
- [x] design.md approved (two review rounds, mechanism verified against orchestrator/ws_server/prompt_reply)
- [x] Worktree `.worktrees/feat-eval-auto-responder` on branch `feat/eval-auto-responder` off origin/develop

## Steps

### 1. `jig/eval/responder.py` — policy + responder loop, with unit tests

**What:** New module:

- `AnswerPolicy` protocol: `answer(ticket_id: str, question: str) -> str`.
- `CannedAnswerPolicy`: returns the constant license text from design.md.
- `DEFAULT_MAX_REPLIES_PER_TICKET = 3` module constant.
- `async def auto_responder(queue, ws, *, policy, max_replies_per_ticket=DEFAULT_MAX_REPLIES_PER_TICKET)`
  (design's `log=` seam dropped — v1 uses the module logger, matching `runner.py`):
  consumes frames from its queue; on `topic == "prompts" and kind == "request" and
  data.prompt_type == "question_answer"`: dedupe by `prompt_id`, enforce per-ticket cap (ERROR log on cap hit),
  log question + answer at INFO, send
  `{"type": "command", "name": "prompt_reply", "args": {"args": [prompt_id, answer]}}` via `ws.send`.
  On `type == "result" and ok is false`: WARNING log with the error string (uncorrelated, per design). All other
  frames ignored. Loop runs until cancelled.

Tests in `tests/test_eval_responder.py` (fake queue + fake ws capturing sends):

- question_answer prompt → exactly one `prompt_reply` send with correct prompt_id and canned text
- duplicate prompt_id → one send
- cap: 4th prompt for the same ticket (distinct prompt_ids) → 3 sends, ERROR record asserted via `caplog`
- distinct tickets have independent caps
- non-question prompt_type → no send, WARNING record asserted via `caplog` (distinct branch — this log is the
  operator's only signal a run stalled on an unhandled prompt type, per design Risks)
- result frame with `ok: false` → no send, WARNING record asserted via `caplog` (separate branch from the above)
- snapshot/other frames → ignored, no crash

**Why:** Responder behavior fully verified against fake frames before runner integration.

**Verify:** `uv run pytest tests/test_eval_responder.py -v` green; `uv run ruff check jig/ tests/` and
`uv run ruff format --check` clean.

### 2. Wire into `run_eval`

**What:** In `jig/eval/runner.py`:

- Add `responder_q: asyncio.Queue[dict]` alongside `completion_q` / `stall_q`; `_dispatch` puts each frame on all
  three.
- Create `responder_task = asyncio.create_task(auto_responder(responder_q, ws, policy=CannedAnswerPolicy()))` next
  to the watcher tasks; it is NOT in the `asyncio.wait` race set. Insertion point for teardown: cancel it alongside
  `dispatch_task` and include it in the existing await list (`for task in [dispatch_task, *pending]` at
  `runner.py:273` becomes `[dispatch_task, responder_task, *pending]`) so it can't leak a "Task was destroyed but
  it is pending" warning.

No `run_eval` signature change (cap and policy are module constants in v1, per design).

Extend `tests/test_eval_runner.py`:

- New test: `_FakeWS._frames` gets a question_answer `prompt_request` frame inserted BEFORE the
  `project_complete` frame — ordering is load-bearing: the frame must flow through `_dispatch` into `responder_q`
  while the responder task is still alive (i.e. before the race settles). `_FakeWS.send` (currently a no-op at
  `test_eval_runner.py:187,270`) grows a `self.sent: list[str]` capture; assert one sent frame is a
  `{"type": "command", "name": "prompt_reply", ...}` with the injected prompt_id.
- Existing 11 tests untouched and green (responder is inert when no prompt frames arrive).

**Why:** Activates the responder in real runs without touching the race semantics.

**Verify:** `uv run pytest tests/test_eval_runner.py tests/test_eval_responder.py -v` green; full
`uv run pytest tests/ -q` no regressions; ruff check + format clean.

### 3. End-to-end validation

**What:** `jig eval run hn-cli` from the repo root. Watch runner INFO logs for the auto-answer (question text +
canned answer). Expected: the run gets past the planning gate where the 2026-06-10 run stalled. Full SUCCESS
depends on downstream phases and the tracer, which this feature doesn't control — the acceptance bar for THIS
feature is no stall at a `question_answer` prompt, and the store showing the operator Answer + `resolved_by` +
status flip. Check `<temp_dir>/.jig/store/comments.jsonl` / `tickets.jsonl` directly (ticket ids are
run-dependent); `jig story <ticket-id> --path <temp_dir>` works once the id is known.

**Why:** The problem's success criterion: the planning gate no longer kills every run.

**Verify:** Run output + store artifacts as above. If the run stalls elsewhere (new gate type, agent wedge), that's
a new finding for a new problem doc, not scope creep into this PR.

### 4. PR

**What:** Push `feat/eval-auto-responder`, open PR per repo conventions (problem/fix, summary, attention areas,
checklist, manual test steps = step 3). Docs (problem/design/plan) ride in the same PR; flip problem.md and
design.md `status: draft → active` in this commit.

**Verify:** CI green (`ruff check`, `ruff format --check`, pytest). Roborev/Claude review findings addressed.

## Rollback

Single additive module + a contained runner diff; revert the PR (or drop the responder task creation) restores
pre-feature behavior exactly. No data migrations, no config changes, nothing depends on the responder.

## Out of scope for this plan

- `answers.yaml` scripted answers, LLM policies (deferred; `AnswerPolicy` is the seam)
- Any change to orchestrator, ws_server, prompt_reply handler, role prompts, TUI
- New outcome/exit codes (cap-hit falls through to stall)
- Fixing whatever the eval surfaces downstream of the planning gate

## Change log

- 2026-06-10: Initial draft (Brent Hoover)
- 2026-06-10: Review fixes — log seam waved off explicitly, caplog assertions named, frame-injection ordering and
  teardown insertion point pinned (Brent Hoover)
- 2026-06-11: Archived — implementation complete, shipped in PR #158 (Brent Hoover)
