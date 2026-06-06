---
title: Quality Feedback Loop — Design
type: design
status: draft
owner: Brent Hoover
created: 2026-06-02
updated: 2026-06-02
problem: ./problem.md
---

# Quality Feedback Loop — Design

## Summary

Four coordinated changes close both feedback gaps: (1) a fallback in `_code_metrics_section`
routes all taxonomy hits to the generalist when no specialist owns them; (2) `build_fix_loop_bundle`
is widened to include notables alongside importants, bypassing per-finding routing so notables
always reach the dev; (3) `_run_review_phase_federation` gains an unacked-notable check — when any
notable from the current federation pass has no ack from the dev, the phase returns `"blocked"`,
triggering a dev re-run with the notables in its bundle; (4) a `reject` ack kind gives the dev a
machine-verifiable path to disagree with a finding rather than ignore it, with the reviewer as
adjudicator.

## Approach

### 1. Taxonomy hit fallback — `prompt_builder.py`

`_code_metrics_section` currently calls `hits_for_reviewer(metrics.taxonomy_hits, role)` and
renders only the matched subset. When the result is empty — either because no role was passed or
because the spawned reviewer owns no taxonomy entries — fall back to the full `metrics.taxonomy_hits`
list. Each hit in the fallback block is annotated with its `TaxonomyHit.reviewer` so the generalist
knows which specialist would normally receive it.

Specialist-federation runs are unaffected: a spawned `reviewer-error-handling` gets a non-empty
result from `hits_for_reviewer` and the fallback never fires.

### 2. Notable inclusion in fix-loop bundle — `fix_loop_bundle.py`

Two changes to `build_fix_loop_bundle`:

**Severity filter**: Remove the `severity in ("critical", "important")` guard on line 73. All
findings from the latest cycle are candidates. The early-return guard becomes: return empty only
when there are no findings at all.

**Routing bypass for notables**: Importants and criticals continue to flow through `_route_one`
routing unchanged. Notables always target the dev phase, regardless of what `_route_one` would
return for the file they reference. This is an explicit invariant: `build_fix_loop_bundle` appends
all unacked notables to the dev bundle unconditionally, bypassing the `target_phase_idx` filter
that governs importants. When `target_phase_idx` is already dev, notables land there directly.
When `target_phase_idx` is a non-dev phase (e.g. test owns a blocking important), the notables
do not appear in that bundle — but §3's unacked-notable check re-blocks on the subsequent review
pass, and `_route_blocked_phase`'s `check-failure-fallback` branch routes notable-only blocks to
the dev phase, where the full unacked-notable bundle is then built. This costs an extra round-trip
in the test-owned-important case; see Risks.

In the rendered bundle, findings are grouped by tier: importants under `## Blocking findings
(must fix)`, notables under `## Non-blocking findings (fix or explain why not)`. The distinction is
explicit so the dev understands that notables require `mark_finding_addressed` but do not require
a code change.

The `latest_cycle` filter in `build_fix_loop_bundle` applies only to importants and criticals.
Notables are selected from the full comment history for the ticket (all cycles), filtered to those
with no satisfying ack in `FindingAcksStore`. This ensures a notable flagged at cycle N is still
present in the dev's bundle at cycle N+1 even if the reviewer did not re-emit it — the dev always
has context for every finding it is asked to acknowledge.

### 3. Unacked-notable check in `_run_review_phase_federation` — `orchestrator.py`

This is the key mechanism that closes the notable-only escape. Currently, `_run_review_phase_federation`
returns `"success"` whenever `blocking` (critical + important) is empty — even when notables are
present. The ticket advances without the dev ever seeing the notables.

After the federation runs and `blocking` is computed, add a second check:

1. Load `FindingAcksStore` and collect all acks via `acks_store.for_ticket(ticket_id)`.
   Load `ReviewCommentsStore` and collect all comments via
   `comments_store.for_ticket_chronological(ticket_id)` (these are two distinct store classes;
   `for_ticket_chronological` lives on `ReviewCommentsStore`, not on `FindingAcksStore`).
2. Compute finding IDs for all comments using `compute_finding_ids(all_comments)`.
3. Build the set of satisfied finding IDs. "Latest ack" for a finding is determined by sorting
   `for_ticket(ticket_id)` results by `cycle` ascending, then by append order within a cycle
   (JSONL row order). This matches the existing `ws_server.py` convention (`ack_history[-1]`).
   Matching is by `finding_id` alone — cycle-equality is not used (notable stamped at cycle N,
   dev ack written at cycle N+1 after the back-route increment).

   Satisfied rules:
   - Latest ack kind is `addressed` → satisfied for the gate. The reviewer-verify-bundle
     (§4, `build_verify_bundle`) surfaces every `addressed` notable to the reviewer each cycle
     for re-examination. If the reviewer finds the claim dishonest or the issue still present,
     they re-flag → `compute_reraised_acks` writes a `reraised` ack → latest becomes `reraised`
     → gate blocks again. Dev self-attestation is accepted at face value; reviewer re-flag is
     the enforcement mechanism, same as for importants.
   - Latest ack kind is `resolved` → satisfied regardless of what preceded it.
   - Latest ack kind is `reject` → **not satisfied**. A rejection requires affirmative reviewer
     sign-off (`mark_finding_resolved`); reviewer silence does not close it.
   - Latest ack kind is `reraised` → not satisfied. The reviewer re-flagged after a prior dev
     claim (`addressed` or `reject`) — the finding is open again.
4. For each notable across **all cycles** for this ticket (from the full
   `for_ticket_chronological` comment history, not just the current pass): check whether its
   finding ID is in the satisfied set. A notable flagged at cycle N that the reviewer did not
   re-emit at cycle N+1 still requires an ack — reviewer non-repetition is not acceptance.
   If any notable finding ID is absent from the satisfied set → this is an unacked-notable block.
5. If unacked notables exist → return `"blocked"` listing the unacked finding IDs. Wrap the
   entire check in a try/except and fail-**closed** on error (return `"blocked"`) — the
   "must not silently escape" requirement takes precedence over fail-open convenience.
6. Only when all notables are acked (or there are no notables) → return `"success"` as before.

This handles both cases:

- **Notable-only first pass**: dev resolves → federation finds notables → no acks exist →
  `"blocked"` → dev re-run → dev calls `mark_finding_addressed` for each → dev resolves again →
  federation runs again → all notables acked → `"success"`.
- **Co-present important + notable, dev-owned important**: dev re-run is triggered by the
  important. The widened bundle (§2) delivers the notables alongside the important. The dev acks
  both, resolves, and the federation's unacked-notable check passes. One extra round-trip total.
- **Co-present important + notable, test-owned important**: `fix_idx` = test phase; the dev does
  not re-run on the important block. The test agent fixes the issue. On the next review pass,
  the unacked-notable check fires and routes back to dev via the `check-failure-fallback`. Two
  extra round-trips total (one for the test fix, one for the notable ack).

The check is cheap: a JSONL read + set intersection per review pass.

### 4. `reject` ack kind — `store/finding_acks.py`, `finding_ack_mcp.py`, `mcp_server.py`

**`FindingAck` model** (`store/finding_acks.py`): add `"reject"` to the `kind` Literal:

```python
kind: Literal["addressed", "resolved", "reraised", "reject"]
```

**`AckKind` + `_write_ack`** (`finding_ack_mcp.py`): `AckKind = Literal["addressed", "resolved"]`
(line 34) becomes `Literal["addressed", "resolved", "reject"]`. The `_write_ack` function's
`kind: AckKind` parameter widens accordingly.

**`handle_mark_finding_addressed`** (`finding_ack_mcp.py`): add an optional `kind` parameter
defaulting to `"addressed"`, accepting `"addressed"` or `"reject"`. Prose is required in both
cases. The handler passes the kind through to `_write_ack`. The current call site at line 141
(`kind="addressed"` hardcoded) must be replaced with the threaded param — without this, `reject`
acks will silently be written as `addressed`.

**MCP registration** (`mcp_server.py`): thread the new `kind` parameter through the tool schema
so the dev agent can call `mark_finding_addressed(finding_id=..., how_resolved=..., kind="reject")`.

**Reviewer verify bundle** (`fix_loop_bundle.py`, `build_verify_bundle`): a finding whose latest
ack is `reject` gets `status="reject"` in the verify dict. Reviewer behavior by severity:

- `important` + `status="reject"`: handled by the **pre-existing** `blocking` branch
  (`orchestrator.py:923-953`), not by the new unacked-notable gate. The federation re-runs; if
  the reviewer re-flags the important, it appears in `blocking` and returns `"blocked"` as today.
  If the reviewer calls `mark_finding_resolved`, `compute_reraised_acks` treats it as terminal
  (`resolved_fids`, line 259) and no `reraised` ack fires on future passes. The new gate in §3
  is notable-only; important-reject is the old path.
- `notable` + `status="reject"`: the reviewer must explicitly call `mark_finding_resolved` to
  accept the rejection, or re-flag to dispute it. Reviewer silence leaves the finding unsatisfied
  and the gate re-blocks. There is no automatic closure on a rejection.

**Unacked-notable check** (§3): accepts `"addressed"` and `"resolved"` as satisfying acks.
`"reject"` does NOT satisfy the gate — it requires affirmative reviewer sign-off
(`mark_finding_resolved`) or re-flag before the finding is closed.

### 5. `dev.yaml` instruction update

Add a paragraph after the ruff-check instruction:

> Every finding in the fix-loop bundle must be acknowledged before resolving — use
> `mark_finding_addressed` for each one. For **blocking findings** (must fix): fix the issue
> and call `mark_finding_addressed(finding_id=..., how_resolved="...")`. For **non-blocking
> findings** (fix or explain why not): call `mark_finding_addressed` with `kind="addressed"` if you
> fix it, or `kind="reject"` with a prose explanation if you genuinely disagree. Do not skip
> any finding — unacknowledged notables will block the review phase from advancing.

## Interfaces

| Surface | Change |
|---|---|
| `FindingAck.kind` (`store/finding_acks.py`) | Add `"reject"` to the `Literal` |
| `AckKind` (`finding_ack_mcp.py` line 34) | Add `"reject"` to the `Literal` |
| `_write_ack` (`finding_ack_mcp.py`) | `kind: AckKind` widens to accept `"reject"` |
| `handle_mark_finding_addressed` (`finding_ack_mcp.py`) | New optional `kind` param, default `"addressed"` |
| MCP tool schema (`mcp_server.py`) | Thread `kind` through `mark_finding_addressed` registration |
| `build_fix_loop_bundle` return shape | Notables included; severity field distinguishes tiers |
| `build_verify_bundle` return shape | `status` field gains `"reject"` as a valid value |
| `_run_review_phase_federation` (`orchestrator.py`) | Unacked-notable check + rejection-escalation check before returning `"success"` |
| `_code_metrics_section` (`prompt_builder.py`) | Fallback block added when `hits_for_reviewer` returns empty |
| `dev.yaml` | New paragraph on finding acknowledgement requirement |
| `prompt_builder.py` verify-bundle section | Inject a `status="reject"` handling instruction into every reviewer prompt when the verify bundle contains rejected findings — no role YAML edits needed |

No new MCP tools. No new store files. The `mark_finding_addressed` MCP tool schema gains one
optional `kind` parameter (default `"addressed"`); callers that omit it are unaffected.

## Data model

`FindingAck.kind` gains `"reject"` as a fourth valid value. All existing store query methods work
unchanged. Existing JSONL rows with `kind` in `{"addressed", "resolved", "reraised"}` continue to
deserialise without error — the new Literal widens acceptance, it does not invalidate existing
values.

## Alternatives considered

### Simplest — Surface and instruct, no gate

Change `build_fix_loop_bundle` to include notables. Change `_code_metrics_section` fallback. Add
a sentence to `dev.yaml` instructing the dev to call `mark_finding_addressed` for all findings. No
machine enforcement.

**Drawback:** The problem is already that the dev ignores what it isn't forced to address. Adding
an instruction without a gate reproduces the current failure mode — the hn-cli dev agent had
`mark_finding_addressed` available and still didn't acknowledge the notable. Instruction without
enforcement is known-broken for this case.

### Complete — Surface + gate, no `reject` kind

All of Optimal minus `reject`. The dev must call `mark_finding_addressed(kind="addressed")` for
every finding. If the dev genuinely disagrees with a finding, the only exit is to call
`mark_finding_addressed` with dishonest prose just to escape the gate.

**Drawback:** Dishonest `addressed` acks are structurally identical to genuine ones — the audit
trail becomes noise and the reviewer can't distinguish real claims from gate-escape claims.
`reject` is the honest exit, and it routes disagreements through the reviewer rather than burying
them.

### Optimal — Complete + `reject` ack kind *(chosen)*

All of Complete plus `reject`. `reject` is a dev dissent that requires reviewer sign-off
(`mark_finding_resolved`) — reviewer silence does not close it. A rejected important routes
through the pre-existing `blocking` branch; a rejected notable requires the reviewer to explicitly
accept or re-flag each cycle.

**Why above Simplest:** instruction-only is known-broken.

**Why not beyond Optimal:** cross-ticket carry-forward of deferred notables was considered and
deferred — it requires tracking notable state across ticket boundaries, which is a new subsystem.
The per-ticket gate is sufficient to prevent escapes within a ticket.

## Risks

- **`reject` requires reviewer to actively respond**: a `reject` ack does not satisfy the gate
  on its own — the reviewer must call `mark_finding_resolved` or re-flag. If the reviewer agent
  fails to handle the rejection (neither accepts nor re-flags), the ticket loops. The reject-handling
  instruction is injected by `prompt_builder.py` into the reviewer prompt when the verify bundle
  contains `status="reject"` findings — no reviewer role YAML edits are needed.
- **`reject` loops bounded only by `max_fix_cycles`**: without SA escalation, a persistent
  dev/reviewer disagreement on a `reject` will cycle until `max_fix_cycles` is hit and the
  ticket fails. This is visible to the operator; SA escalation is deferred to a separate feature.
- **Extra dev round-trip on notable-only tickets**: every ticket that receives any unacked notable
  requires at least one additional dev re-run (the acknowledgement pass). Acceptable for
  correctness; monitor in evals.
- **Extra round-trip when important is test-owned**: when a blocking important routes to the test
  agent and notables are co-present, the notables require a second block cycle via the
  `check-failure-fallback` (dev acknowledgement pass). This costs two extra round-trips instead
  of one. Acceptable; the alternative (complicating `build_fix_loop_bundle` to know the dev phase
  index) is not worth the added coupling.
- **`reject` loop on importants**: if the dev rejects an important and the reviewer re-flags it,
  and the dev rejects again, the ticket can cycle. This is intentional — the loop should surface
  to the operator. Max-fix-cycles cap already bounds infinite loops.
- **Latency of unacked-notable check**: the check reads `FindingAcksStore` + `ReviewCommentsStore`
  before returning from each review pass. Both are JSONL; the cost is negligible at current eval
  scale. Monitor if it regresses on large projects with many findings.

## Out of scope

- `_run_review_federation` / `apply_severity_disposition` (`orchestrator.py` lines 673–807):
  this is the post-RESOLVE gate from an earlier design era. It has no live callers — the
  phase-loop `_run_review_phase_federation` is the live gate targeted by §3. The
  `has_higher_severity → coord_arg=None` path mentioned in problem.md §3 (Context) is in this
  dead function; it does not need to be patched.
- Cross-ticket carry-forward of unresolved notables.
- Auto-applying notables with a `suggested_diff`.
- Changing the `QualitySnapshot` store schema or adding cross-run trend analysis.
- SA escalation for `reject`→`reraised` loops: the live phase loop has no sa-consult dispatch
  path (the only existing sa-consult emitter is in `_run_review_federation`, which is dead code).
  Reject loops are bounded by `max_fix_cycles` for now; SA escalation is a separate feature.
- The `status="reject"` handling instruction for reviewers is injected by `prompt_builder.py`
  (in the verify-bundle section) when rejected findings are present, not via role YAML edits.
  This satisfies the problem constraint ("no edits to existing reviewer role YAML files").
- Reviewer confirmation of notable rejections (residual risk, future improvement).

## Change log

- 2026-06-02: Initial draft (Brent Hoover)
