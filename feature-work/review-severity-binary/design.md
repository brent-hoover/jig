---
title: Binary review severity & failed-ticket dead-ends — Design
type: design
status: draft
owner: brent-hoover
created: 2026-06-12
updated: 2026-06-12
problem: ./problem.md
---

# Binary review severity & failed-ticket dead-ends — Design

## Summary

Make review blocking binary: only `critical`/`important` findings block; the unacked-notable gate and its ack
obligation are deleted, and notables instead become operator-gated `proposed` issues. Replace round-counting with
persistence counting — a blocking finding accumulates toward escalation only when it survives a fix attempt — and
route persistent findings (and backstop-cap trips) to an SA adjudication step with binding dismissals, so a
reviewer can never unilaterally fail a ticket. Run the generalist reviewer twice per review round (informed second
pass) where it is the sole reviewer. On ticket failure, transitively fail dependents so `project_complete` fires;
add a stuck-project watchdog for dead-ends with other causes; teach the eval runner to classify these outcomes.

## Approach

Seven components, in dependency order:

### 1. Binary gate (removal)

Delete the unacked-notable gate in `Orchestrator._run_review_phase_federation` (the in-workflow phase gate,
`jig/orchestrator.py:1034-1064`; distinct from `_run_review_federation`, the post-resolve gate at :752): the
existing `severity in (critical, important)` filter becomes the entire blocking decision. Remove with it:

- `_unacked_notables` / `_unacked_notable_finding_ids` (orchestrator.py), the notable-only-block routing branch
  in `_route_blocked_phase` (orchestrator.py:3320-3341, PR #162 — dead once notables never block), and
  `_notable_is_satisfied` (`jig/fix_loop_bundle.py:150`).
- The notable-routing branch in `build_fix_loop_bundle` (`jig/fix_loop_bundle.py:113-140`, PR #162) — back-routed
  agents no longer receive notables at all.
- The notable→coordinator-deferred branch in `apply_severity_disposition` (`jig/reviewers/disposition.py`, applied
  from the post-resolve gate at `jig/orchestrator.py:825-886`) — superseded by issue conversion, component 2.
  Critical and important disposition in that path is untouched.

**Two SA touchpoints, deliberately distinct.** The existing post-resolve disposition path (important → BLOCKED +
`Handoff(phase="sa-consult")`) fires once, at ticket resolution, and keeps its current semantics. The new SA
*adjudication* (component 5) fires mid-phase, only on persistent-finding or cap-trip escalation inside the
in-workflow review loop — the place churn actually happens. They cannot fire on the same finding at the same
lifecycle point; unifying them is possible later but is not attempted here.

Kept (shared with blocking-finding machinery): `FindingAcksStore`, `signature_of`, `compute_finding_ids`,
`compute_reraised_acks`, and the `mark_finding_addressed`/`mark_finding_resolved` MCP tools, which dev agents use
to claim fixes for blocking findings.

### 2. Notables → proposed issues

At federation end (gate already evaluated), the orchestrator converts each notable comment in the cycle into an
issue: a ticket created in `proposed` status via the same path the issue front door uses, with
`labels=["review-notable"]`, `created_by="review-federation"`, a link to the source ticket, and the finding's
prose + file/line in the description. Operator-gated: never dispatched without `jig issue approve`.

Dedup: per-ticket, by `signature_of(comment)`. At federation end the orchestrator scans existing
`review-notable`-labeled tickets linked to the source ticket (the store is already loaded) and skips signatures
already filed — restart-robust at the cost of one in-memory scan; no separate dedup state to maintain.

### 3. Reviewer template consequences

All seven `reviewer_*.yaml` role templates get an identical consequence block alongside their existing
classification rubrics:

- `critical` / `important` — *blocks the ticket and forces a fix round. Post it only if you would hold the merge
  for it. Repeatedly re-raising the same finding escalates to the solutions architect.*
- `notable` — *never blocks and is never routed to the dev. Filed as a proposed issue for the operator. Use it
  freely for anything worth recording that you would not hold the merge for.*

The generalist's false "ticket-passes-with-deferred" text is replaced by the above (it becomes true). The
classification rubrics themselves (what kind of issue belongs in which tier) are not rewritten.

### 4. Review invocation shaping: multi-pass discovery, delta-scoped convergence

Discovery is front-loaded into the *first* review round; every later round converges on what round one found.

**First round — multi-pass.** `PhaseConfig` gains `review_passes: int = 1`. `_run_review_phase_federation` owns
the pass loop (not `dispatch_with_llm_spawn`), running the federation dispatch `review_passes` times sequentially
within the cycle; per-cycle post-processing such as `compute_reraised_acks` still runs exactly once, after the
final pass, against a prior-comments snapshot taken before pass 1. Passes after the first are *informed*: the
spawn prompt includes the findings already posted this cycle (rendered from the cycle-scoped
`ReviewCommentsStore.for_cycle` query) with the instruction to add coverage, not re-derive or duplicate. All
passes post into the same cycle, so the gate's existing cycle-scoped query evaluates the union with no merge
step. `feature-s`'s review phase (the small profile's sole-reviewer round) sets `review_passes: 2`.

**Re-review rounds — delta-scoped, single-pass.** When the review phase re-runs after a blocked round's fix, the
orchestrator passes the *last-reviewed commit* as `base_ref` instead of the project default branch — the existing
plumbing (`_run_review_phase_federation` → `dispatch_with_llm_spawn(base_ref=...)` → reviewers' `git diff
base_ref..HEAD`, orchestrator.py:939, dispatch.py:836) carries it unchanged. The spawn prompt lists the prior
round's blocking findings with the instruction: verify each is resolved, review the new changes, do not re-review
unchanged code. The last-reviewed commit (worktree HEAD at the moment the cycle's final pass completes) is
tracked in the phase loop next to `fix_counts`; on daemon restart it is absent and re-review falls back to the
full-diff base — degraded to today's behavior, never wrong. Re-review rounds run a single pass (`review_passes`
applies only to the first round; a second pass over a small delta buys nothing). Gate semantics are unchanged.

Consequences: a new finding on untouched code is structurally impossible on re-review (the code is not in the
served diff) — the RC-4 class dies at the source; a regression introduced by the fix itself is still reviewable
(it is in the delta); a fix in one file breaking unchanged code elsewhere is the validate phase's job (full test
suite) plus the post-resolve gate, both of which stay full-scope. Persistence matching (component 5) also gets
more reliable, since re-review findings are predominantly re-raises of known findings rather than novel ones.

### 5. Persistence counting + SA adjudication

**Persistence key**: `(reviewer, type, file)` — deliberately coarser than `signature_of` (drops the line/contract
discriminator) because a fix changes the very lines a finding points at, making line-anchored identity miss on
exactly the rounds that matter. Over-matching collapses same-type findings in one file; that errs toward earlier
SA escalation, and the SA is the disambiguator.

**Counting**: the phase loop tracks `survival_counts: dict[PersistenceKey, int]` next to `fix_counts`. When the
review phase blocks, each blocking finding whose key was also in the *previous* blocked round's set increments its
count; fresh keys enter at zero; a key *absent* from a blocked round resets to zero (it was presumably fixed — if
something reappears later it restarts as an inherited slot, and a flapping finding that never accumulates is
caught by the round cap instead). `fix_counts`/`max_fix_cycles` (orchestrator.py:1649) remain as the backstop, but
the backstop now also escalates (below) instead of failing directly.

**Escalation triggers** (constants, tunable):
- `SURVIVAL_THRESHOLD = 2`: a finding survived two fix attempts → SA adjudication for that finding.
- `fix_counts[phase] > max_fix_cycles`: cap trip → SA adjudication of *all* outstanding blocking findings.

**Adjudication**: the orchestrator spawns the profile's `sa_role` agent (same spawn machinery as reviewers) with
an adjudication bundle: each escalated finding's per-cycle prose history, the dev's `addressed` acks, and the fix
commits between cycles. Escalation is by persistence key; each escalated key is presented to the SA as its
*latest* RC-N occurrence (with the history of prior occurrences in the bundle), and the SA's verdict for that
RC-N binds the whole key. The SA returns one verdict per finding via a new MCP tool (see Interfaces):

- `dismissed` — finding is wrong or not worth blocking. Recorded as
  `FindingAck(kind="dismissed", persistence_key=...)` — the ack carries the coarse key (new optional field, see
  Data model) so the binding outlives both line drift and daemon restarts (acks are JSONL-persisted, unlike the
  in-memory counters). **Binding**: the federation gate maps each blocking comment to its persistence key and
  filters out keys with a dismissal ack before evaluating, in all later cycles. The reviewer can re-post it; it no
  longer blocks.
- `uphold_guidance` — finding is real; SA guidance is injected into the fix-loop bundle for one more routed fix
  round, and that finding's survival count resets.
- `uphold_fail` — finding is real and the SA judges it unresolvable in this ticket → ticket FAILED.

**Bound**: at most `MAX_SA_ESCALATIONS = 2` adjudications per ticket review phase; afterwards, remaining
persistent blockers fail the ticket directly (the SA has had its say — this is the new, SA-mediated meaning of
giving up). `uphold_guidance` resets the finding's survival count but does *not* reset `fix_counts`, so the
combined worst case is bounded: `max_fix_cycles` (3) routed rounds, plus at most `MAX_SA_ESCALATIONS` (2)
SA-granted extension rounds — ≤ 5-6 review rounds per ticket phase, then a terminal outcome. SA agent errors fail
closed to the old behavior (ticket fails) with a loud log, never silently pass.

### 6. Failure cascade + stuck watchdog

**Cascade**: `_on_ticket_failed` walks the failed ticket's `blocks` list transitively. Every dependent in a
non-terminal, not-running status is marked `FAILED` with `block_reason="dependency-failed"` (reusing the existing
free-text field; no schema change), emitting `ticket_failed` per ticket. Dependents cannot be in flight — a ticket
is only scheduled once all dependencies are `resolved` — so there is no race with running agents; the walk reads
and writes through the single orchestrator-owned ticket store. `_handle_schedule` additionally learns the inverse
check: if any dependency is `FAILED`, cascade-fail the ticket immediately (covers tickets created after the
failure). `_maybe_run_analyzer` keeps its trigger logic — with dependents terminal, it fires `project_complete`
and runs the analyzer — but its payload computation adds `tickets_failed` (it currently counts only
RESOLVED+CLOSED into `tickets_resolved`, orchestrator.py:2412-2425). One plan-level check: no existing code may
interpret a ticket by `block_reason` alone without consulting status — the BLOCKED path also writes that field,
and cascade-failed tickets are `FAILED + block_reason="dependency-failed"`.

**Watchdog**: lives in the periodic reconcile loop (`_run_reconcile_loop`, orchestrator.py:1393-1411) — *not* the
"no ready tickets" branch of `_start_ready_tickets`, which is event-driven and is precisely what stops firing when
a project dead-ends. Each reconcile tick evaluates: no running tickets, no ready tickets, no `needs_info` tickets
(operator-pending counts as alive), and at least one non-terminal ticket. The first tick where this holds stamps
`_stuck_since`; any tick where it doesn't clears it. Once the condition has held for `STUCK_GRACE_SECONDS`
(default 120), emit a `project_stuck` event once per distinct stuck ticket-set (with the stuck ticket ids and
their blockers) and log at WARNING. It does not mutate tickets — it is a tripwire for dead-end causes the cascade
does not know about (e.g. dependency cycles).

### 7. Eval outcome classification

The eval runner's completion watcher already keys on `project_complete` + `analysis_complete`. Outcome mapping
becomes: `tickets_failed == 0` → `SUCCESS`; `tickets_failed > 0` → new `EvalOutcome.COMPLETED_WITH_FAILURES`
(manifest still collected, tracer still run and recorded — an incomplete project failing its tracer is signal,
not noise). A `project_stuck` event → new `EvalOutcome.STUCK` (replaces the generic `stall/bus_silence` label for
this case; the stall detector remains the catch-all for everything else).

### Analytics

New events alongside the existing `ReviewFinding*` family: `ReviewFindingPersisted` (key, survival count),
`SAAdjudication` (finding ids, verdicts, rationale), `NotableIssueFiled` (issue ref, source ticket),
`TicketCascadeFailed` (root failure id), `ProjectStuck`. Without these the eval loop cannot tell whether
persistence counting and adjudication actually improved outcomes.

## Interfaces

- **MCP tool (new, SA-only)** `sa_adjudicate_finding(finding_id: str, verdict: "dismissed" | "uphold_guidance" |
  "uphold_fail", rationale: str, guidance: str | None)` — registered only for adjudication spawns; one call per
  escalated persistence key, identified by that key's latest RC-N occurrence (the orchestrator maps RC-N → key
  when recording the verdict). Validation: finding_id must be one of the escalated RC-Ns; verdict enum enforced
  at the tool layer.
- **PhaseConfig** gains `review_passes: int = 1` (workflow YAML surface; applies to the first review round of a
  phase — re-review rounds are single-pass and delta-scoped by construction, not configurable).
- **Workflow YAML** `feature-s` review phase sets `review_passes: 2`.
- **Events**: `project_complete` payload gains `tickets_failed: int`; new `project_stuck` event; new analytics
  event types listed above. WS relay passes them through unchanged.
- **EvalOutcome** gains `COMPLETED_WITH_FAILURES` and `STUCK`.
- **Role templates**: consequence block added to all seven `reviewer_*.yaml`; SA role template gains an
  adjudication section describing the verdict tool and its obligations.
- **Removed surface**: the unacked-notable block message ("Call mark_finding_addressed for each…") disappears;
  `reviewer_post_comment` schema is unchanged (severity vocabulary stays `critical|important|notable`).

## Data model

- `FindingAck.kind` gains `"dismissed"`, and `FindingAck` gains an optional `persistence_key: str | None` field
  (serialized `reviewer|type|file`, set only on dismissals) so the binding-dismissal filter is keyed coarsely and
  persists across restarts. Append-only JSONL; no migration — historical data regenerable.
- Notable-derived issues are ordinary tickets: `status=proposed`, `labels=["review-notable"]`,
  `created_by="review-federation"`, linked to the source ticket. No new store.
- `survival_counts` and the last-reviewed commit are in-memory phase-loop state, like `fix_counts`. Daemon
  restart resets them; behavior degrades to extra fix rounds / a full-diff re-review, never to a stuck or
  wrongly-failed ticket.
- Persistence key: `(comment.reviewer, comment.type, comment.file)`. Dismissal acks store this key so the gate's
  filter survives line drift the same way the counter does.

## Alternatives considered

### Simplest: delete the gate, cascade on failure

Binary gate + template fixes + failure cascade only. Kills the notable death-spiral and silent dead-ends in ~3
focused PRs. Rejected because it leaves the round-counting defect intact: the hn-cli ticket also burned two rounds
on *fresh important* findings — under Simplest, a chatty reviewer still unilaterally fails tickets at
`max_fix_cycles`. Operator decisions 2, 3, and 5 (persistence counting, SA adjudication, two-pass) are
requirements, not options.

### Optimal: drift-robust matching, persisted counters, full observability

Everything in Chosen plus LLM-judged finding identity ("is this the same finding as last round?"), block/survival
counters persisted across daemon restarts, SA escalation surfaced in the TUI, and per-size pass-count tuning.
Rejected for now: LLM matching is speculative until eval runs show the coarse key misbehaving; counter persistence
protects against a rare event (mid-ticket restart) whose failure mode is already benign; TUI surfacing is
independent work. Each can be added later without unwinding this design.

### Chosen: Complete plus targeted robustness

All seven components, built on existing machinery (signatures, acks, federation dispatch, IssueService-shaped
issue creation, agent spawn), with the four Optimal items that are load-bearing or nearly free: coarse persistence
key (drift-robust by construction, SA as disambiguator), cap-trips routed through SA, the stuck watchdog, and
analytics events. This is the smallest design in which a reviewer cannot fail a ticket, a failed ticket cannot
silently dead-end a project, and the eval loop can measure both claims.

## Risks

- **Coarse key over-collapse**: two genuinely different same-type findings in one file share a key, so a fixed
  finding's "slot" can be inherited by a new one, inflating survival counts → premature SA escalation. Accepted:
  the failure mode is an early adjudication, not a wrong terminal outcome.
- **SA verdict quality**: a rubber-stamping SA (`uphold_guidance` forever) is bounded by `MAX_SA_ESCALATIONS`; a
  dismissive SA waves real defects through — mitigated by the adjudication bundle including full finding history,
  and measurable via `SAAdjudication` analytics against eval regressions.
- **Cost**: +1 generalist pass per review round (~$0.35/round observed) and an SA spawn per escalation. Bounded by
  pass/escalation constants; quality of the most problematic flow is the stated priority.
- **Two federation paths**: the in-workflow gate and the post-resolve disposition path must both reflect binary
  semantics. Component 1 touches both; a test asserts no notable influences either path's blocking decision. The
  two SA touchpoints (post-resolve sa-consult Handoff vs mid-phase adjudication) stay semantically separate — the
  risk of operator confusion is accepted and documented in the SA role template.
- **Dismissal scope**: binding dismissals are keyed coarsely, so an SA dismissal also mutes a *future, different*
  same-type finding in that file for that ticket. Accepted for ticket scope (short-lived); dismissals do not carry
  across tickets.
- **Watchdog false positives**: a legitimately quiet-but-alive state (e.g. operator pondering a needs_info prompt)
  must not trip it — `needs_info` tickets count as "running" for the watchdog's purposes.
- **Delta-scope blind spot**: a fix in file A that breaks an invariant in unchanged file B is invisible to a
  delta-scoped re-review. Accepted: the validate phase runs the full test suite and the post-resolve gate reviews
  full-scope; the alternative (full re-reviews) is the proven churn generator this design exists to kill.

## Out of scope

- Reviewer rubric re-tuning, LLM-based finding matching, persisted counters, TUI surfacing (see Alternatives).
- Retrying/rescuing the root failed ticket; cross-ticket dismissal memory; severity vocabulary rename (the
  `notable` name stays).
- The eval stall detector's thresholds and signals (unchanged; it remains the catch-all).

## Open questions

- [ ] None blocking. Tunables (`SURVIVAL_THRESHOLD=2`, `MAX_SA_ESCALATIONS=2`, `STUCK_GRACE_SECONDS=120`) are
      initial values to be revisited against eval data, not design commitments.

## Change log

- 2026-06-12: Initial draft (brent-hoover)
- 2026-06-12: Design-review fixes: watchdog moved to reconcile loop with explicit debounce state; SA touchpoints
  reconciled (post-resolve sa-consult kept, mid-phase adjudication new); persistence-key absence/reset rule and
  worst-case round bound stated; dismissal acks persist the coarse key; RC-N ↔ persistence-key granularity pinned;
  restart-robust notable dedup via store scan; tickets_failed computation and block_reason check noted
  (brent-hoover)
- 2026-06-12: Plan-review backports: gate method corrected to `_run_review_phase_federation`; added
  `_route_blocked_phase` notable-branch (PR #162) to the removal list (brent-hoover)
- 2026-06-12: §4 expanded per operator decision 6: multi-pass discovery confined to the first round; re-review
  rounds delta-scoped (base_ref = last-reviewed commit) and single-pass, verifying prior findings (brent-hoover)
