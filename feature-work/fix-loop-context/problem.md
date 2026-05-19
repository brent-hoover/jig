---
title: Fix-loop context — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-19
updated: 2026-05-19
---

# Fix-loop context — Problem Statement

## Context

The review-routing feature (merged 2026-05-18, plan steps 1–8) gave the
orchestrator the ability to route a blocking reviewer finding back to the
specific phase that authored the offending file — e.g. a finding against
`tests/test_filter_flags.py` routes to the test phase, not dev. The router
chooses *which* phase to re-spawn; that part works.

What the router does NOT do is tell the re-spawned phase agent *what* to
fix. The blocked phase publishes a thread Note
(`fix_loop_route: "Routing blocked phase 'review' back to 'implement'
(reason: writes-glob implement)"`) and then re-runs the implement phase
with `spawn_reason=PHASE_PRIMARY` — the same context bundle a first-time
spawn gets. The reviewer findings themselves live in `ReviewCommentsStore`
(a JSONL file separate from the thread store), are not enumerated in any
context URI, and no MCP tool surfaces them to the agent. The dev sees the
ticket description, the design doc, the plan, and the thread (including
the routing Note that says "you're back" but not why). It does not see
the file:line:prose of the findings it was sent back to fix.

## Problem

The bounded fix loop relies on each routed-back phase converging on the
specific findings that blocked the previous federation pass. Today the
routed-back agent has no prompt-injected signal about those findings, no
way to acknowledge a specific finding ("I addressed RC-3 by removing the
unused constant"), and no way for the next federation pass to compare
"what the dev claimed they fixed" against "what is still in the diff."

In practice this looks like:

1. Federation blocks with N important findings.
2. Router sends dev back. Dev sees the routing Note and the diff, picks
   *something* to fix based on the diff (often the wrong thing), commits,
   declares done.
3. Federation re-spawns. Same findings (slightly re-worded) re-flag.
4. Repeat until the fix-loop budget is exhausted; ticket fails.

The hn-cli eval ticket `be2459b8` failed this way on 2026-05-19 after 5
review cycles, with 1–2 important findings re-raised every round — most
notably the `BASE` / `_HN_BASE` duplication finding, which the dev never
addressed because nothing pointed at it.

## Simplest possible solution

For every back-routed spawn:

1. Resolve the latest cycle's blocking findings from
   `ReviewCommentsStore`, filtered to the comments whose routing target
   matches the phase being re-spawned.
2. Render each finding inline in the agent's prompt with a stable
   per-ticket ID (`RC-1`, `RC-2`, …), file:line, severity, prose, and
   any prior `mark_finding_addressed` claims for that ID.
3. Add an MCP tool `mark_finding_addressed(id, how_resolved)` the agent
   calls once per finding it believes it has fixed. The call records a
   structured "addressed" entry — it does NOT clear the finding from the
   next federation pass.

For every reviewer running when prior addressed claims exist (cycle 2+
on this ticket, regardless of whether this reviewer has run before):

4. Render the previous cycle's findings + dev's `addressed` claims in
   the reviewer's prompt.
5. Frame the reviewer's job as two tasks: (a) **verify** each addressed
   claim — for each one, call `mark_finding_resolved(id, confirmation)`
   if the dev's fix actually resolved the issue, or re-flag the finding
   if it did not; (b) **find new issues** in the updated diff.
6. Add an MCP tool `mark_finding_resolved(id, confirmation)` for
   reviewers. Together with `mark_finding_addressed`, this produces a
   two-sided audit trail per finding: raised → addressed (dev claim) →
   resolved (reviewer confirmation) or re-flagged (reviewer rejection).

## Complications considered

- **Scale**: bounded — typical ticket has <20 findings across all cycles;
  no risk of unbounded prompt growth. We only inject the *latest* cycle's
  blocking findings, not the full history.
- **Concurrency**: N/A — federation already runs in parallel inside a
  single phase; the routed-back spawn is sequential after federation
  completes.
- **Failure modes**:
  - Agent ignores the injected findings and fixes something else: the
    next federation pass will re-flag and the dev's addressed claims (or
    lack thereof) will be visible. This is observability, not a hard
    gate; the bounded fix-loop is the hard gate.
  - Agent calls `mark_finding_addressed` on a finding it did not in fact
    address: same observability story — the next federation pass
    re-flags and the false claim is on record.
  - Reviewer re-phrases the same logical finding across cycles: the
    stable per-ticket ID must survive re-phrasing. This forces a
    decision about ID assignment that is not "hash the prose" — see
    constraints below.
- **Cross-cutting policies**: N/A — touches no PII, secrets, auth.
  Findings are already persisted to a JSONL store; this work surfaces
  what is already recorded.

## Constraints

- The injection mechanism must work for any back-routed phase, not just
  dev. Test phase, document phase, validate phase — anything the router
  can target.
- The per-ticket finding ID must be stable across cycles for the same
  logical finding. A reviewer that re-phrases a finding in cycle 3 must
  not get a new ID for it; the dev needs to be able to say "I addressed
  RC-3" once and have it stick. Cycle-1 RC-3 and cycle-3 RC-3 must
  resolve to the same logical issue.
- `mark_finding_addressed` claims must be persisted (so subsequent
  federation passes and the operator can read them) and must include
  *which agent* made the claim (for attribution) and *what cycle*
  (so re-evaluation is bounded to the relevant period).
- The work must not require touching ReviewerComment authoring — the
  judgment reviewer agents stay as they are. Stable IDs are assigned at
  the *consumer* side (orchestrator or store reader), not by reviewers.
- The work must not bypass the bounded fix-loop. `mark_finding_addressed`
  records a claim; it does not gate the next federation pass.

## Requirements

- Every back-routed spawn renders the latest cycle's blocking findings
  targeted at that phase in its prompt, with stable per-ticket IDs.
- A new MCP tool `mark_finding_addressed(id, how_resolved)` records the
  claim to a persistent store and is in the allowed_tools of any role
  the router can re-spawn (dev, test, document, validate at minimum).
- Every reviewer agent running on cycle 2+ of a ticket with prior
  addressed claims sees those findings + claims and is prompted with
  a two-task structure: verify each claim (resolved or re-flag), then
  look for new issues in the updated diff. (Cycle 1 reviewers run
  unchanged — no claims exist yet to verify.)
- A new MCP tool `mark_finding_resolved(id, confirmation)` is available
  to judgment-reviewer roles and records the reviewer's confirmation
  that a previously-raised finding is no longer present.
- A canonical audit view (operator inspection or eval-harness join)
  combines `.jig/store/review_comments.jsonl` (which already records
  raised findings) with `.jig/store/finding_acks.jsonl` (which records
  the new ack events: addressed, resolved, reraised). The join keyed
  on `(ticket_id, finding_id)` yields a complete per-finding lifecycle:
  raised → addressed (dev) → resolved (reviewer) OR raised → addressed
  (dev) → re-flagged (reviewer-issued reraised ack) OR raised →
  never-addressed → re-flagged. The ack store alone does not contain
  the raised event — review_comments.jsonl is the source of truth for
  that.
- Re-phrased findings across cycles resolve to the same stable ID via
  some heuristic (file + line + reviewer + type) that is robust to prose
  drift. The heuristic's misses (false-equates and false-distincts) are
  ranked by frequency in the failure mode they cause; the worst-case
  miss is "two genuinely distinct findings collapse to the same ID,"
  which we accept as a notable bug, not a critical one.
- An operator inspecting `.jig/store/` after a failed ticket can read
  the addressed-claims log to see what the dev claimed and reconcile
  against the diff.

## Non-goals

- No automatic suppression of findings the dev claims to have addressed.
  Suppression would re-create the exact failure mode we just observed
  with judgment reviewers (dev declares done, gets through the gate, bug
  ships). The reviewer is the gate; this work makes the reviewer's job
  easier and the dev's job more focused.
- No changes to `ReviewerComment` schema or to reviewer-emitted output
  format. Judgment reviewers keep emitting the same shape; we layer
  consumer-side stable IDs and ack tracking on top of that existing
  surface. (Cycle-2+ reviewer *prompts* DO change — see the
  Requirements section — but the comment schema does not.)
- No per-finding diff-application tooling. `mark_finding_addressed` is
  documentation, not a code-action verb.
- No retroactive replay over historical tickets. The feature applies to
  new ticket runs only.
- No changes to the fix-loop budget itself. Budget tuning is a separate
  problem; we are fixing the convergence rate first.
- Not applicable to mechanical (deterministic) reviewers — they pass or
  fail their own checks; their findings already carry concrete pointers
  the dev can act on without prompt injection. Scope here is the
  judgment reviewer federation.
- No new termination policy for the federation. Judgment review is
  inherently non-exhaustive per pass — LLM reviewers (like human
  reviewers) notice different things on each look, so multi-round
  review is fundamental to the tool, not a failure mode. Today's
  termination is the bounded fix-loop budget (N cycles, then fail).
  This work makes each round's dev response more focused, which makes
  the rounds *more productive*, but does not change the termination
  condition. Plausible future levers (diminishing severity bar,
  no-new-findings termination, explicit "good enough" reviewer signal)
  are out of scope; the addressed-claims log is exactly the evidence
  surface those levers would need, so we are not precluding them.

## Success criteria

- The hn-cli eval ticket `be2459b8` (or equivalent) can be re-run from
  the same starting point and converge to RESOLVED — i.e. the dev
  actually addresses the `_HN_BASE` finding because it is named with a
  stable ID in the dev's prompt.
- Inspecting `.jig/store/finding_acks.jsonl` (or wherever addressed
  claims land) on a failed ticket shows a clear trail of what the dev
  claimed vs. what reviewers re-flagged, sufficient for an operator to
  diagnose without reading agent transcripts.
- The injected prompt section for a back-routed spawn is bounded in
  size — typical case <2KB, hard cap on the rendered findings
  (everything past the cap renders as "N more findings — read
  $RC_STORE for full list").

## Open questions

- [ ] Stable-ID heuristic specifics: confirm `(reviewer, type, file, line)`
  is sufficient, or whether we need a small-string-distance fallback on
  prose. To be resolved in the design doc.
- [ ] Where the addressed-claims store lives. Likely
  `.jig/store/finding_acks.jsonl` as a sibling of `review_comments.jsonl`,
  but should the comments store own it? To be resolved in the design doc.

## Change log

- 2026-05-19: Initial draft (brent)
- 2026-05-19: Add Non-goals entry on judgment-review non-exhaustiveness
  and the deferred termination-policy levers (brent)
- 2026-05-19: Add reviewer-side `mark_finding_resolved` tool and
  two-task framing for cycle-2+ reviewers (brent)
- 2026-05-19: Reword the "no reviewer agent changes" non-goal to
  cover only `ReviewerComment` schema (cycle-2+ prompts DO change);
  clarify the audit-view requirement joins review_comments.jsonl
  with finding_acks.jsonl rather than living in the ack store alone
  (roborev job 8)
