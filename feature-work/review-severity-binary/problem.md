---
title: Binary review severity & failed-ticket dead-ends — Problem Statement
type: problem
status: draft
owner: brent-hoover
created: 2026-06-12
updated: 2026-06-12
---

# Binary review severity & failed-ticket dead-ends — Problem Statement

## Context

Review federation gates each ticket's review phase on reviewer findings, which carry one of three severities:
`critical`, `important`, or `notable` (`jig/reviewers/comment.py`). Critical and important findings block the phase
directly. Notable findings are advisory in intent, but they carry an acknowledgement obligation: an unacked notable
also blocks the phase (the unacked-notable gate in `Orchestrator._run_review_phase_federation`,
`jig/orchestrator.py:1036-1064`), and PR #162 routes each unacked notable to the phase whose `writes:` glob owns the
file so the owning agent can call `mark_finding_addressed`. Every blocked review increments a per-ticket counter; at
the cap the orchestrator gives up and marks the ticket `failed`.

The 2026-06-12 `jig eval run hn-cli` run surfaced how these mechanics interact in practice. Ticket `bb0a5437` was
blocked in review four consecutive rounds — two rounds on important findings, then two rounds on *freshly posted*
notables (RC-3, then RC-4, a `# noqa: B008` consistency nit at confidence 0.72). Each round the dev agent fixed the
finding; each re-review found a new one on the revised diff. The cumulative counter hit the cap and the ticket
failed. Its dependent ticket then sat `open` forever: `_on_ticket_failed` (`jig/orchestrator.py:2313`) has no
failed-path analogue of `_unblock_dependents`, and `_maybe_run_analyzer` (`jig/orchestrator.py:2389`) requires every
ticket to be terminal before emitting `project_complete`. The orchestrator idled logging "no ready tickets in queue"
until the eval runner's catch-all `bus_silence` heuristic killed the run 303 seconds later.

**Operator decisions (settled, 2026-06-12) — inputs to the design, not open design choices:**

1. A finding either blocks or it doesn't — the notable tier's "non-blocking but ack-required" middle ground is
   eliminated. Notable findings are retained as non-blocking data: surfaced as `proposed` issues in the issue
   tracker (operator-gated, never auto-dispatched) so reviewers have an outlet for observations that shouldn't
   block, in addition to the raw review-comments record.
2. The give-up counter counts *persistent* findings, not rounds: only a finding that survives a fix attempt (the
   same finding re-raised after the dev addressed it) accumulates. Fresh findings on a revised diff each get a
   fix round but do not by themselves fail the ticket. A total-round cap remains as a runaway backstop.
3. When a blocking finding persists past the threshold, the orchestrator escalates to the SA for adjudication
   instead of failing the ticket. The SA resolves the reviewer/dev deadlock — dismiss the finding (review passes)
   or uphold it (SA guidance routes to the dev, or the ticket fails if the SA deems it unresolvable). A reviewer
   cannot unilaterally fail a ticket.
4. Reviewer role templates must state the *consequences* of each severity, not just classification rubrics — the
   generalist template currently claims notables are non-blocking ("ticket-passes-with-deferred") while the gate
   blocks on them, and the other six reviewer templates (architectural, error-handling, pattern-conformance,
   performance, security, test-adequacy) never say what each tier does to the ticket at all.
5. In the `small` profile, where the generalist is the sole reviewer, the *first* review round must comprise at
   least two reviewer passes over the diff before the gate evaluates — enforced by the orchestrator, not by
   prompt text — so discovery is front-loaded into one comprehensive round. The second pass is *informed*: it
   receives the first pass's findings and adds coverage rather than re-deriving or duplicating them.
6. Review rounds after the first are *convergence* rounds, not discovery rounds: the reviewer verifies the prior
   round's findings and reviews only the delta diff since the last-reviewed commit. Unchanged code is not
   re-reviewed — a new finding on untouched code is structurally impossible because that code is not in the
   served diff. Regressions introduced by the fix itself remain reviewable (they are in the delta); cross-file
   regressions in unchanged code are the validate phase's job.

## Problem

Two distinct defects, one incident:

1. **Advisory findings can fail tickets.** A notable finding — by the role templates' own guidance, "advisory,
   stylistic, documentation gap" — participates in the same blocking gate and the same give-up counter as critical
   findings. Because reviewers see a fresh diff each round, they can post a *new* notable every round, and the
   counter does not distinguish a persistent unresolved finding from a stream of new nits on converging code. A
   single 0.72-confidence style observation on round four was sufficient to fail a ticket whose blocking findings
   were all resolved. The severity taxonomy says three tiers, but the gate semantics are effectively binary already
   — notable-with-ack-obligation behaves as "blocker with extra steps", and the ack machinery (finding-ack store,
   `mark_finding_addressed` MCP tool, per-finding writes-glob routing, reraise tracking) exists largely to service
   that contradiction.

2. **A failed ticket dead-ends the project silently.** When a ticket fails, tickets that depend on it remain `open`
   indefinitely: nothing fails, closes, or skips them, and `_handle_schedule` only schedules a ticket when all its
   dependencies are `resolved`. Because `open` is a non-terminal status, `_maybe_run_analyzer` never sees an
   all-terminal project, so `project_complete` and the post-run analyzer never fire. The run neither finishes nor
   fails — it idles forever. Downstream, the eval pipeline records the outcome as `stall/bus_silence` (an
   infrastructure signal) instead of "ticket failed, project unfinishable", and no analysis report is captured.

## Simplest possible solution

1. Stop consulting notables in the review gate: delete the unacked-notable gate from `_run_review_federation` and
   let the existing `severity in (critical, important)` filter be the whole blocking decision. Notables keep
   flowing into the review-comments store, which already persists them for later reading.
2. In `_on_ticket_failed`, walk the failed ticket's dependents (the same `blocks` list `_unblock_dependents`
   walks on resolve) and mark any that can no longer become ready as terminal, recursively; then the existing
   `_maybe_run_analyzer` all-terminal check fires `project_complete` unchanged.

The complications below have to justify anything beyond this.

## Complications considered

- **Scale**: N/A — bounded by tickets per project (tens) and findings per review (single digits).
- **Concurrency**: dependency chains fan out — a failed ticket may have transitive dependents several hops removed,
  and a dependent may have *other* dependencies still in flight when the failure happens. The unreachability
  decision must be correct in the presence of running agents and must not race ticket-status writes from those
  agents' MCP servers.
- **Failure modes**: a silent dead-end wastes the remainder of a multi-hour, multi-dollar run, produces no analyzer
  report, and mislabels eval outcomes — corrupting the eval signal the orchestrator's quality work depends on.
  Conversely, over-eager cascade (terminating dependents that could still run) throws away completable work.
- **Cross-cutting policies**: severity values are embedded in analytics events (`ReviewFindingPosted/Acked/Rejected`
  carry `Literal["critical", "important", "notable"]`), reviewer role templates, the finding-ack store schema, and
  existing eval/quality tooling. Historical JSONL data is regenerable (no production deployments), but every
  consumer of the severity vocabulary moves together.
- **Dead code surface** (specific to this problem): notables stop blocking ⇒ the machinery built to service the
  ack obligation (finding-ack store, ack MCP tools, reraise tracking, per-finding notable routing from PR #162)
  loses its primary caller. Leaving it half-wired invites divergence; removing it touches many files. The design
  must decide deliberately, not by omission.

## Constraints

- Stores are append-only JSONL; no migrations needed (existing data is regenerable), but record shapes are shared
  across orchestrator, MCP tools, analytics, and eval tooling.
- Reviewer behavior is steered by role YAML templates (`jig/defaults/roles/reviewer_*.yaml`); any severity-semantics
  change must keep template guidance and gate behavior consistent.
- The eval runner consumes `project_complete` / `analysis_complete` events to classify outcomes; whatever ends a
  dead-ended project must surface through that same event path.
- Quality gates stay meaningful: critical/important findings must continue to block exactly as they do today.

## Requirements

- No finding classified as advisory (`notable`) may block a review phase, route work back to a prior phase, or
  count toward the review-block give-up counter — under any acknowledgement state.
- Notable findings are still recorded and retrievable after the run for post-failure triage, and each distinct
  notable (deduplicated across review cycles) is surfaced as a `proposed` issue linked to its source ticket —
  visible for operator triage, never dispatched without approval.
- The give-up decision is driven by finding persistence: only a specific blocking finding that survives fix
  attempts trips the threshold — not N rounds of different findings. A total-round cap remains as a backstop
  against unbounded loops.
- A persistent finding past the threshold escalates to the SA for adjudication; the ticket fails review only as
  an SA-mediated outcome (or via the backstop cap), never as the direct result of one reviewer's repeated
  finding.
- Every reviewer role template (all seven `reviewer_*.yaml`, including test-adequacy) states, for each severity it
  may emit, what posting it does to the ticket (blocks the merge and forces a dev round vs. recorded for triage
  only) — and that statement is true.
- When the generalist is the sole reviewer, the first review round runs at least two reviewer passes — separate
  agent invocations with fresh contexts; a single invocation cannot meaningfully re-review its own output — and
  gates on the merged finding set. The second invocation receives the first pass's findings as input.
  Runtime-enforced and runtime-observable (agent runs in the event log), not prompt steering.
- Review rounds after the first serve the reviewer a diff based at the last-reviewed commit (the fix delta) plus
  the list of findings to verify; the gate semantics are unchanged. New findings are only possible on changed
  code.
- When a ticket fails, every ticket that transitively depends on it reaches a terminal state without operator
  intervention, while tickets with a still-viable path to ready continue untouched.
- A project in which every ticket is terminal emits `project_complete` (with accurate resolved/failed counts) and
  runs the analyzer, exactly as a fully successful run does.
- The eval runner classifies such a run as a ticket-failure outcome, not `stall/bus_silence`.

## Non-goals

- Rewriting the per-role classification rubrics (what kind of issue belongs in which tier) — the template changes
  in scope are about consequences and review discipline, not re-tuning what reviewers look for. The gate must
  remain safe regardless of reviewer calibration.
- Changing how critical/important findings block, route, or escalate within a round.
- Relaxing severity thresholds or removing the runaway backstop cap. (The counting semantics behind the give-up
  decision are in scope per operator decision 2; the existence of a hard cap is not.)
- Migrating historical JSONL store data.
- TUI work beyond whatever minimal display change the severity semantics force.
- Retrying or rescuing the failed ticket itself (failure handling for the *failed* ticket is unchanged; this work
  is about its dependents and the project outcome).

## Success criteria

- Replaying the `hn-cli` eval scenario (notable-only findings on consecutive review rounds) ends with the review
  phase passing; the run proceeds instead of failing the ticket.
- A unit/integration test demonstrates: ticket fails → transitive dependents reach a terminal state →
  `project_complete` fires with accurate resolved/failed counts → analyzer runs.
- `jig eval run` on a project with an induced ticket failure reports a ticket-failure outcome rather than
  `stall/bus_silence`, and an analysis report exists for the run.
- Notable findings posted during a run are visible in the review-comments store after the run for triage.
- A test demonstrates the persistence counter: a sequence of review rounds each blocked by a *different* important
  finding does not fail the ticket, while the same finding re-raised after repeated fix attempts triggers SA
  escalation. (The exact survival threshold and the cross-round matching scheme are design outputs; the test pins
  whatever the design chooses.)
- A test demonstrates SA adjudication outcomes: dismiss → review passes; uphold-with-guidance → routed to dev;
  uphold-unresolvable → ticket fails (and the dead-end cascade then applies).
- All seven reviewer role templates contain consequence statements consistent with the implemented gate, and the
  generalist template's notable description matches actual behavior.
- A test demonstrates that a small-profile first review round executes at least two reviewer passes and the gate
  decision uses findings from both.
- A test demonstrates that a re-review round's diff is based at the last-reviewed commit and its prompt carries
  the prior round's blocking findings for verification.
- Full test suite passes; the design's decision on the ack machinery (keep/remove) is fully reflected — no consumer
  of notable-blocking semantics remains wired in either case.

## Open questions

- [ ] What terminal status do unreachable dependents get — `failed`, `closed`, or a new status (e.g. `skipped`)?
      Affects analytics, eval outcome counts, and TUI display. (Design decision.)
- [ ] When is unreachability evaluated — eagerly at failure time (transitive walk), or lazily whenever a ticket's
      last in-flight dependency reaches a terminal state? Must handle dependents with multiple dependencies where
      the failure arrives while siblings are still running.
- [ ] Does the finding-ack machinery (`finding_acks` store, `mark_finding_addressed`/`mark_finding_resolved` MCP
      tools, reraise tracking, PR #162's per-finding notable routing) retain any purpose once notables stop
      blocking, or is it removed wholesale? Note: the persistence counter needs cross-round finding identity —
      the existing reraise signature matching may be the right substrate even if the ack tools go.
- [ ] How are findings matched across rounds for the persistence counter — reuse the reraise signature scheme, or
      something more robust to line-number/phrasing drift between diffs? How many survived fix attempts fail the
      ticket?
- [x] ~~Is the second reviewer pass informed or blind?~~ Resolved (operator, 2026-06-12): informed — pass 2
      receives pass 1's findings and adds coverage.
- [ ] What shape does SA adjudication take — reuse the existing SA-consult handoff path, or a dedicated
      escalation? What context does the SA get (finding history, fix attempts, diffs), and is its dismissal
      binding on subsequent rounds (the dismissed finding can't re-block if re-raised)?
- [ ] Does the `notable` severity *name* survive as the label for non-blocking triage findings, or does the
      vocabulary collapse to two values? (Semantics are settled — non-blocking either way; this is naming and
      schema only.) Analytics literals and role templates follow whichever way this goes.

## Change log

- 2026-06-12: Initial draft (brent-hoover)
- 2026-06-12: Conformed to project template (added Simplest possible solution); recorded operator decision on
  binary semantics; narrowed open questions per problem-review (brent-hoover)
- 2026-06-12: Scope expanded per operator: persistence-based give-up counting, consequence-explicit severity
  guidance in all reviewer templates, generalist multi-pass review discipline (brent-hoover)
- 2026-06-12: Corrected reviewer template count to seven (test-adequacy included); marked persistence
  threshold/matching as design outputs per re-review (brent-hoover)
- 2026-06-12: Multi-pass review changed from prompt steering to orchestrator-enforced double pass per operator
  (brent-hoover)
- 2026-06-12: Persistent findings escalate to SA adjudication instead of failing the ticket directly; reviewer
  can no longer unilaterally fail a ticket (brent-hoover)
- 2026-06-12: Second reviewer pass resolved as informed (receives pass 1's findings) per operator (brent-hoover)
- 2026-06-12: Notables surfaced as proposed issues (operator-gated) per operator; supersedes the
  coordinator-deferred-queue notion of notable triage (brent-hoover)
- 2026-06-12: Decision 6 added per operator: discovery front-loaded into multi-pass first round; re-review rounds
  are delta-scoped convergence rounds (verify findings + fix delta only) (brent-hoover)
