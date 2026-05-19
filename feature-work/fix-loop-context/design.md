---
title: Fix-loop context — Design
type: design
status: draft
owner: brent
created: 2026-05-19
updated: 2026-05-19
problem: ./problem.md
---

# Fix-loop context — Design

## Summary

Plumb the existing-but-hidden reviewer findings into the prompts of
both back-routed phase agents and re-spawned reviewers, with a stable
per-ticket finding ID and a two-sided audit trail (`mark_finding_addressed`
from the dev side, `mark_finding_resolved` from the reviewer side). No
suppression; no termination-policy change; bounded fix-loop budget
stays the gate.

## Approach

Four pieces, all additive:

### 1. Stable finding IDs

Computed on-the-fly per prompt render — no separate registry to keep
in sync. For a given ticket, call
`ReviewCommentsStore.for_ticket_chronological(ticket_id)` (defined
below) to get rows in **insertion order**, compute each row's
signature `(reviewer, type, file, line)`, and assign `RC-1` to the
first signature, `RC-2` to the second, and so on. Rows whose
signature was already seen earlier in the walk reuse the existing
RC-N rather than getting a new one.

**Required reads.** `ReviewCommentsStore.for_ticket` goes through
`Collection.find_where` → `Store.find_by`, which iterates the
indexed set of doc IDs. Set iteration in Python is implementation-
defined; the order can vary across processes. So the legacy
`for_ticket` is **not** safe for stable-ID computation.

We add a new read, `for_ticket_chronological`, that walks the
in-memory `_docs` dict directly (Python dicts are insertion-ordered
since 3.7) and filters by `ticket_id`. This gives us a deterministic
append order — the same order in which the JSONL was written —
without depending on a separate timestamp field as the tiebreaker.

The legacy `for_ticket` is kept as-is for any caller that doesn't
care about order; new callers (the stable-ID resolver, the `jig
story` extension, and the TUI snapshot builder) use the chronological
variant.

**Companion schema addition.** `ReviewerComment` does not carry a
`created_at` field. We add `created_at: datetime` with a static
default `datetime.min` (NOT a `default_factory`, which would stamp
the load time on legacy rows). The MCP write path
(`reviewer_mcp.handle_reviewer_post_comment`) is updated to inject
`created_at = datetime.now(UTC).isoformat()` into the payload
*before* `ReviewerComment.model_validate`, so newly-written rows
carry the real write-time UTC. Legacy rows in the JSONL with no
`created_at` field load with the `datetime.min` sentinel and render
as "(no timestamp)" in story output.

The split — static default for missing-field-on-load, write-path
stamping for new rows — sidesteps the Pydantic-default-factory
subtlety that would stamp legacy-on-load with the current time
(effectively rewriting history on every load).

`created_at` is purely a display/timestamp concern. The stable-ID
ordering key is insertion order, not `created_at`.

The insertion-order walk is load-bearing for ID stability: it
guarantees that adding a new finding in a later cycle only appends a
higher RC-N and never renumbers existing IDs.

The signature is stable across cycles, so a finding that gets
re-phrased in cycle 3 collapses to the same group as its cycle-1
sibling and keeps the same RC number.

When a reviewer doesn't supply `file`/`line` (a project-wide finding),
the signature degenerates to `(reviewer, type, None, None)` — there's
exactly one such bucket per (reviewer, type) pair per ticket, which
matches the natural interpretation ("this reviewer flagged a single
category-level concern").

### 2. `FindingAcksStore`

A new append-only JSONL store at `.jig/store/finding_acks.jsonl`,
sibling to `ReviewCommentsStore`. Uses the same `Collection` pattern.
One row per ack event. Indexed on `(ticket_id, finding_id)`.

Row schema (`FindingAck` pydantic model):

```python
class FindingAck(BaseModel):
    ticket_id: str
    finding_id: str          # "RC-3"
    kind: Literal["addressed", "resolved", "reraised"]
    author: str              # role name; "dev", "reviewer-pattern-conformance", ...
    cycle: int               # the cycle in which the ack was posted
    prose: str               # "removed _HN_BASE from main.py" or "confirmed; single BASE definition"
    created_at: datetime
```

`reraised` is what the orchestrator records when a finding posted in
cycle N+1 has the same signature as one from cycle N — i.e., the
reviewer either explicitly chose not to call `mark_finding_resolved`
on a previously addressed finding, or never addressed it in the first
place. Recorded automatically, not via tool call.

### 3. Two new MCP tools

`mark_finding_addressed(ticket_id, finding_id, how_resolved)` — for
any role the router can re-spawn (dev, test, document, validate at
minimum). Writes a `FindingAck(kind="addressed")` row.

`mark_finding_resolved(finding_id, confirmation)` — for all
judgment-reviewer roles. In the default workflow that is six
reviewers: `reviewer-pattern-conformance`, `reviewer-error-handling`,
`reviewer-test-adequacy`, `reviewer-architectural`,
`reviewer-performance`, `reviewer-security`. Writes a
`FindingAck(kind="resolved")` row.

Both tools validate that `finding_id` resolves against the current
ticket's findings; an unknown ID raises a clear error so the agent
sees its mistake.

### 4. Prompt injection

Two new sections in `jig/prompt_builder.py`, conditionally rendered:

**For back-routed phase agents** (any role with
`spawn_reason=FIX_LOOP_RETRY`):

```
## Blocking Findings

The previous review cycle blocked this ticket with findings routed to
your phase. Address each finding, then call
`mark_finding_addressed(finding_id="RC-N", how_resolved="...")` per
finding before calling `commit_progress` and `update_ticket`.

[RC-3] tests/test_api.py:17 — important — pattern-conformance
test_api.py re-defines FIXTURE_PATH and _raw_fixture instead of using
conftest.py's session-scoped fixture_data...
(prior: addressed cycle 1 by dev: "renamed _raw_fixture → fixture_data";
re-flagged cycle 2 by reviewer-pattern-conformance: "test_api.py still
loads FIXTURE_PATH locally")

[RC-7] src/hn_cli/main.py:23 — important — pattern-conformance
_HN_BASE duplicates api.py's BASE constant...
```

Each finding includes `file:line — severity — reviewer`, the full
prose, and the full ack history for that finding. The dev sees the
full evolution and can target the actual unresolved issue.

**For reviewer agents on cycle 2+ with prior acks**:

```
## Previous Cycle Findings

The previous review cycle raised these findings; the dev claims they
have been addressed. Your job has two tasks in this order:

1. **Verify** each addressed claim. For each finding, either call
   `mark_finding_resolved(finding_id="RC-N", confirmation="...")` if
   the dev's fix actually resolved the issue, or re-flag it by posting
   a fresh reviewer_post_comment with the same file/line as the
   original.
2. **Find new issues** in the updated diff. Report via
   `reviewer_post_comment` as you would on a first-cycle review.

[RC-3] tests/test_api.py:17 — important
ORIGINAL FINDING: test_api.py re-defines FIXTURE_PATH...
DEV CLAIMED: cycle 1, dev: "renamed _raw_fixture → fixture_data"

[RC-7] src/hn_cli/main.py:23 — important
ORIGINAL FINDING: _HN_BASE duplicates api.py's BASE constant...
DEV CLAIMED: (no claim posted; not addressed)
```

The two-task framing is rendered explicitly so the LLM doesn't drop
either task; "verify" runs first because the verification work is
bounded and concrete, whereas "find new issues" is open-ended.

### 5. Orchestrator wiring

`_route_blocked_phase` is the choke point for back-routed dev/test/etc.
spawns. It already computes the routing target; it gains the additional
step of:

1. Compute `RC-N` IDs for the ticket (walk `ReviewCommentsStore`).
2. Filter to findings whose routing target matches the chosen phase.
3. Load prior acks for those findings from `FindingAcksStore`.
4. Stash on the spawn context as `fix_loop_bundle`.
5. `agent.py` plumbs the bundle into `build_initial_prompt` so
   `_instructions_section` (or a new dedicated section) can render it.

`_run_review_phase_federation`, when running on cycle 2+ (i.e., when
the ticket has any prior acks), computes the same RC-N IDs, loads all
prior acks for the ticket, and passes them to each reviewer's spawn
context as a `verify_bundle`. The reviewer's prompt renders the
"Previous Cycle Findings" section from it.

`SpawnReason` gains a new variant `FIX_LOOP_RETRY` (back-routed phase)
to distinguish from a fresh `PHASE_PRIMARY` and keep the prompt
section conditional on the right signal.

After each federation pass completes, the orchestrator scans
new-cycle comments and:

- For any comment whose signature matches a prior cycle's signature
  AND has no `kind=resolved` ack between the prior cycle and this one,
  writes a `FindingAck(kind="reraised")` row. This is the
  automatic "you didn't resolve this" trail.

## Interfaces

### MCP tools

```python
# Available to dev, test, document, validate roles.
def mark_finding_addressed(
    finding_id: str,        # "RC-3"
    how_resolved: str,      # short prose; max 500 chars
) -> str:                   # returns the FindingAck row id
    ...

# Available to all six judgment-reviewer roles
# (reviewer-pattern-conformance, reviewer-error-handling,
# reviewer-test-adequacy, reviewer-architectural,
# reviewer-performance, reviewer-security).
def mark_finding_resolved(
    finding_id: str,
    confirmation: str,      # short prose; max 500 chars
) -> str:
    ...
```

Both tools take only `finding_id` — `ticket_id` is implicit from the
agent's per-spawn MCP context (each agent's MCP server is scoped to
one ticket). This matches the shape of `commit_progress` and other
ticket-implicit tools already in the surface.

Both tools fail loudly on unknown `finding_id`. Both are idempotent:
re-calling with the same `(finding_id, kind, author)` within the same
cycle silently de-dupes against the most recent ack of that shape (so
retries don't pollute the log). A different cycle or different author
*does* create a new row — that history matters.

### TUI

The tickets detail pane (`jig/tui/screens/tickets.py`,
`_render_detail`) gains a "Findings" section, rendered after the
existing ticket metadata when the ticket has any reviewer findings.
Each row is one finding:

```
Findings
  [RC-1] tests/test_api.py:17 — important — addressed (cycle 1, dev)
  [RC-3] src/hn_cli/main.py:23 — important — open
  [RC-7] src/hn_cli/api.py:28 — important — resolved (cycle 2, reviewer-error-handling)
```

Status values: `open` (no ack), `addressed` (dev ack, no reviewer
confirmation yet), `resolved` (reviewer confirmed), `reraised`
(reviewer rejected the dev's claim). Color-coded via the existing
TUI palette (notable=dim, important=yellow, critical=red, resolved=green).

A finding row is selectable; selecting one swaps the detail pane to
a finding-detail view showing the original prose, file/line, and full
ack history with timestamps. Back-arrow returns to the ticket detail.

Data source: `ReviewCommentsStore` + `FindingAcksStore` snapshots, both
already pushed to the TUI via the existing daemon snapshot mechanism.
A new snapshot type `findings_snapshot` carries the joined view to
avoid the TUI having to compute the join itself.

### Role config changes

Six judgment-reviewer YAML files gain `mark_finding_resolved` in
`allowed_tools`: `reviewer-pattern-conformance`,
`reviewer-error-handling`, `reviewer-test-adequacy`,
`reviewer-architectural`, `reviewer-performance`, `reviewer-security`.

Four phase-agent YAML files (`dev`, `test`, `document`, `validate`)
gain `mark_finding_addressed` in `allowed_tools`.

No reviewer prompt text changes for cycle 1 — the new section only
renders when there are prior acks. Reviewer prompts keep their
existing instructions for first-pass behavior.

### CLI

The `jig story <ticket-id>` command is extended to interleave findings
and their ack history into the per-ticket narrative. The story builder
gains a new `StorySource.finding` and emits one `StoryEvent` per
finding-raised (timestamp = `ReviewerComment.created_at`, kind =
`"finding_raised"`) and one per ack (timestamp = `FindingAck.created_at`,
kind = `"finding_{addressed|resolved|reraised}"`). The events interleave
with existing thread + log events by timestamp, so an operator running
`jig story <tid>` sees the full chronology in one pass.

No new top-level CLI command. The `.jig/store/finding_acks.jsonl` file
is the canonical eval surface — its schema (the `FindingAck` pydantic
model) is documented and stable. Eval harnesses parse the JSONL
directly; humans use `jig story`.

## Data model

```
.jig/store/finding_acks.jsonl     # one FindingAck per line, append-only
```

Indexed on `ticket_id` and `finding_id`. The store is owned by the
orchestrator (loaded at `startup`, reset on `_emergency_reset`, same
lifecycle as `review_comments`).

ID assignment is computed on-the-fly per render; no persisted RC-N
registry. The store does not store RC-N IDs — it stores the canonical
`finding_id` value (which is the RC-N at compute time, but the
recomputation produces the same string given the same comment set,
so consistency is preserved across reads).

A subtle invariant: if a reviewer posts a finding in cycle 1 and a
*different* reviewer posts the same (file, line, type) in cycle 3,
they get *different* RC numbers because `(reviewer, type, file, line)`
is the signature key. This is intentional — two reviewers flagging
"the same line" probably mean different things and should be
addressed (and acked) separately.

## Alternatives considered

### Tool-only, no prompt injection

Add `get_blocking_findings()` and `mark_finding_*` tools, no prompt
injection. The agent must discover the findings via tool call.

Rejected: too much discovery burden. The dev does not currently know
review comments exist as a queryable surface. Without a prompt
nudge, the agent's likely path is the same as today: read the diff,
guess what to fix, commit. The whole point of this work is to take
"guess" out of the loop.

### Suppress findings claimed as addressed

Once dev calls `mark_finding_addressed`, the next federation pass
does not see that finding (it gets pre-filtered).

Rejected: lets a confused dev bypass the gate. If the dev claims to
have addressed something they didn't, the reviewer never gets a chance
to catch it. The reviewer is the gate; this work supports the gate,
it does not weaken it.

### Sequential numbering vs. hash-based IDs

Use a short hash of the signature (e.g., 4-char hex) instead of
`RC-N`. Globally unique without the cross-ticket collision concern.

Rejected: humans read `RC-3` and immediately know where to look in
the list. They read `RC-A4F2` and have to scan. The cross-ticket
collision concern is moot because IDs are always prefixed with the
ticket ID in the audit trail (`RC-3` on ticket `be2459b8` is
unambiguous). Operator readability wins.

### Chosen: prompt injection + dual tools + on-the-fly stable IDs

Reads the way humans audit changes: see the list, address items, call
"done" per item, the next reviewer verifies the items. Operator
inspecting the JSONL gets a clean linear trail. Reviewer
non-exhaustiveness is acknowledged as a feature of the tool, not a
bug to engineer around.

## Risks

- **Stable-ID heuristic mis-equates two genuinely distinct findings.**
  Signature is `(reviewer, type, file, line)`. Two findings from the
  same reviewer with the same type at the same line will collapse to
  one ID. Mitigation: this is rare in practice (a reviewer typically
  doesn't post two findings of the same type at the same line), and
  when it does happen the prose makes the collapse visible. We accept
  this as a notable but not critical bug.

- **Line drift across cycles.** If the dev edits a file and a
  finding's logical target moves from line 23 to line 27, the
  signature changes and the RC number changes. The reviewer's
  "verify" task in cycle 2 sees the old `RC-3` as still-pending and
  a new `RC-9` appears for the same logical concern. Mitigation:
  acceptable for v1. A future refinement could fuzzy-match on
  signature with line-drift tolerance.

- **Prompt size growth.** Worst case is a ticket with N cycles each
  raising K findings; the cycle-N prompt would render N×K findings.
  Mitigation: render only the latest cycle's findings (with ack
  history per finding), not the full cycle-by-cycle history. Hard
  cap at 30 findings per prompt; anything past renders as "N more —
  see `.jig/store/review_comments.jsonl`".

- **Reviewer ignores the verify-task and only does new-issues.**
  Mitigation: prompt structure puts "verify" first, lists explicit
  RC IDs, makes the tool call concrete. The reviewer's job is also
  graded post-hoc: the orchestrator's automatic `reraised` writes
  detect findings that should have been resolved but weren't.

- **Dev marks something addressed it didn't fix.** Mitigation: next
  federation pass re-flags the finding, the false claim is on
  record, the bounded fix-loop budget burns one cycle for nothing.
  This is the same failure mode we have today, with better
  observability.

## Out of scope

- No automatic suppression of claimed-addressed findings.
- No reviewer-prompt rewrite for first-cycle behavior.
- No new top-level CLI subcommand — the existing `jig story` is
  extended to surface findings.
- No replay over historical tickets.
- No fix-loop budget tuning.
- No mechanical-reviewer integration — judgment reviewers only.
- No cross-ticket finding IDs.

## Open questions

None — both resolved during review.

### Resolved

- **Batch `mark_findings_addressed([ids], one_how_resolved)`?** No.
  One call per finding. Forces the dev to be specific about how each
  finding was resolved, which is the point of the audit trail.
- **Auto-`reraised` ack: prose or link?** Include the new comment's
  prose in the `FindingAck` row. Audit trail stays self-contained;
  the JSONL inflation is bounded by the bounded fix-loop budget
  (worst case N cycles × K findings of prose duplication).

## Change log

- 2026-05-19: Initial draft (brent)
- 2026-05-19: Fix the stable-ID sort order — must be insertion order,
  not file/line, so adding new findings doesn't renumber existing acks
  (brent)
- 2026-05-19: Add autopsy surface (extend `jig story`) and TUI surface
  (Findings section in tickets detail pane); JSONL remains the
  canonical eval surface (brent)
- 2026-05-19: Resolve open questions — no batch ack tool; auto-reraised
  ack carries new comment's prose for self-contained audit (brent)
- 2026-05-19: Address roborev job 9 — add `ReviewerComment.created_at`
  (precondition for ordered-read), add `for_ticket_chronological`
  store method, expand reviewer-role list to all six judgment
  reviewers, use `FIX_LOOP_RETRY` consistently, drop `ticket_id` from
  tool signatures (implicit from agent's per-spawn MCP context) (brent)
- 2026-05-19: Address roborev job 10 — `for_ticket_chronological`
  walks `_docs` directly (insertion-ordered) rather than sorting by
  `created_at` (which had non-deterministic input from the set-backed
  index); `created_at` is display-only, not the ID ordering key.
  Non-goal worded as "no reviewer-emitted payload changes" to allow
  the internal `created_at` schema addition (brent)
- 2026-05-19: Address roborev job 11 — fix the algorithm description
  to say insertion order via `for_ticket_chronological` (not
  `created_at` ascending); switch `created_at` from `default_factory`
  to a static `default=datetime.min` + write-path stamping so legacy
  rows load with the sentinel instead of getting "now" rewritten on
  every load (brent)
