---
title: Quality Feedback Loop — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-06-05
updated: 2026-06-06
design: ./design.md
---

# Quality Feedback Loop — Implementation Plan

## Overview

Five steps in dependency order. The `reject` ack kind is the foundation — all gate logic and bundle
changes depend on the expanded Literal. Bundle changes (§2) come next so tests can verify notables
flow to dev before the gate is wired. The taxonomy fallback (§1) is independent and can land in
any order. The orchestrator gate (§3) requires the bundle changes to be complete. Prompt injections
(§5 and `dev.yaml`) land last since they depend on the gate being in place.

## Preconditions

- [x] Design approved (`feature-work/quality-feedback-loop/design.md` at `active`)
- [x] Worktree at `.worktrees/quality-feedback-loop` on branch `feat/quality-feedback-loop`
- [x] `uv run pytest tests/ -v` passes on `develop` (4321 tests)

## Steps

### 1. `reject` ack kind — data model and MCP layer

**What:**
- `jig/store/finding_acks.py`: add `"reject"` to `FindingAck.kind: Literal[...]`
- `jig/finding_ack_mcp.py`:
  - `AckKind = Literal["addressed", "resolved"]` → add `"reject"`
  - `handle_mark_finding_addressed`: add optional `kind: Literal["addressed", "reject"]`
    parameter (default `"addressed"`); replace hardcoded `kind="addressed"` at line 141 with
    the threaded param
- `jig/mcp_server.py`: thread `kind` through the `mark_finding_addressed` tool schema and
  registration so agents can call `mark_finding_addressed(..., kind="reject")`

**Why:** Every subsequent step reads or writes `reject` acks. The Literal expansion must land
first so the store validates correctly and existing rows continue to deserialise.

**Verify:**
```bash
uv run pytest tests/ -k "finding_ack" -v
```
Add a test: `handle_mark_finding_addressed(..., kind="reject")` writes a `FindingAck` with
`kind="reject"`; omitting `kind` defaults to `"addressed"`; passing `kind="resolved"` is rejected
— enforced by the `Literal["addressed", "reject"]` type annotation at the MCP schema boundary
(the SDK raises on out-of-enum values; add an explicit runtime check in `handle_mark_finding_addressed`
with a clear error message as a defense-in-depth). Also update `build_verify_bundle`'s docstring
to include `reject` and `reraised` in the documented `status` value set — both are already
produced by the existing `finding_acks[-1].kind` logic but are undocumented.

---

### 2. `build_fix_loop_bundle` — include notables from all cycles

**What:** `jig/fix_loop_bundle.py`:
- Restructure `build_fix_loop_bundle` so the two severity branches are computed independently
  before any early return:
  1. **Importants/criticals** (blocking): filter `all_comments` to `latest_cycle` +
     `severity in ("critical", "important")`, then route through `_route_one` as today.
  2. **Notables** (non-blocking): `_build_fix_loop_bundle_for_phase` (the orchestrator method
     that calls `build_fix_loop_bundle`, at orchestrator.py:2905) calls
     `await self._filter_out_of_scope_comments(candidates)` on the candidate notable comments
     before passing them in. Add an `in_scope_notable_comments: list[ReviewerComment]`
     parameter to `build_fix_loop_bundle` (in addition to `all_acks`, which stays). Inside
     the function, select from `in_scope_notable_comments` those with no satisfying ack in
     `all_acks` (latest ack kind `addressed` or `resolved`; `reject` and `reraised` are NOT
     satisfying). Bypass `_route_one` — notables always target dev. **Only pass
     `in_scope_notable_comments` when `target_phase_idx` is a dev phase**; when the retry target
     is a non-dev phase (e.g. test), pass an empty list so notables are withheld from that bundle
     and left for the unacked-notable gate's dev fallback path.
     **Update `_build_fix_loop_bundle_for_phase` signature** to add the filter call and pass
     results into `build_fix_loop_bundle`.
  3. Early-return: return empty only when **both** lists are empty. The current early-return
     at line 75-76 (`if not blocking: return {}`) must be moved to after both lists are built.
- Update signature to accept `all_acks: list[FindingAck]` (available at all call sites).
- Render in two sections: `## Blocking findings (must fix)` and
  `## Non-blocking findings (fix or explain why not)`.

**Why:** Notables must be visible to the dev before the gate can enforce acks. This step makes
notables available; the gate in step 4 enforces them.

**Verify:**
```bash
uv run pytest tests/ -k "fix_loop_bundle" -v
```
Add tests:
- Notable from cycle N appears in bundle when no ack exists, even if not present at cycle N+1.
- Notable-only ticket (no importants at any cycle): bundle is non-empty and contains the notable.
- Notable with `kind="addressed"` latest ack is excluded from bundle.
- Notable with `kind="reject"` latest ack (no subsequent `resolved`) is included in bundle.
- Notable with `kind="reject"` followed by `resolved` is excluded from bundle.
- Notable with `kind="reraised"` latest ack is included in bundle.
- Out-of-scope notable (file outside reviewer's `reads_glob`) is excluded from bundle.
- Importants still respect `latest_cycle` and `_route_one`.

---

### 3. `_code_metrics_section` taxonomy fallback — `prompt_builder.py`

**What:** In `_code_metrics_section`, after calling `hits_for_reviewer(metrics.taxonomy_hits,
role)`: if the result is empty (reviewer owns no entries), fall back to the full
`metrics.taxonomy_hits` list. Annotate each hit in the fallback block with its `TaxonomyHit.reviewer`
field so the reviewer knows which specialist would normally handle it.

**Why:** Taxonomy hits are silently unrouted in single-reviewer dispatches. Independent of the
gate — can land in any order but belongs in its own step for a clean diff.

**Verify:**
```bash
uv run pytest tests/ -k "code_metrics" -v
```
Add tests:
- Reviewer with no owned taxonomy entries gets the full fallback list, each hit annotated with
  `TaxonomyHit.reviewer`.
- Reviewer with owned entries gets only its entries (no fallback).
- `role=None` still returns the universal block only (no fallback).

---

### 4. Unacked-notable gate — `_run_review_phase_federation` in `orchestrator.py`

**What:** After the federation runs and `blocking` (critical/important) is computed, add the
unacked-notable check before returning `"success"`:

1. Load `ReviewCommentsStore.for_ticket_chronological(ticket_id)` for full comment history.
2. Collect candidate notables: all comments with `severity == "notable"` across all cycles.
3. Filter candidates through `await self._filter_out_of_scope_comments(candidates)` — the same
   hallucination filter used for the blocking path (orchestrator.py:935-936). This removes
   notables whose referenced file is outside the issuing reviewer's `reads_glob`. Store the
   result as `in_scope_notables`. **Do not stash or pass this set** — the gate and bundle use
   independent calls to `_filter_out_of_scope_comments` on the same store state. Because both
   derive the set from disk at the same logical point in the ticket lifecycle, they produce the
   same result deterministically (no concurrent writes between gate return and bundle build).
5. Add `from jig.finding_ids import compute_finding_ids` to local imports in
   `_run_review_phase_federation`.
6. Compute `finding_ids = compute_finding_ids(all_comments)`.
7. Load `FindingAcksStore.for_ticket(ticket_id)`. Build satisfied set by sorting acks per
   `finding_id` by `cycle` ascending then JSONL append order (latest-ack-kind rule from design
   §3 step 3). Satisfied: `addressed` or `resolved`. Not satisfied: `reject`, `reraised`, absent.
8. For each notable in `in_scope_notables`: if its `finding_id` is not in the satisfied set →
   unacked-notable block.
9. If unacked notables exist → return `RunAgentResult(status="blocked", ...)`. Wrap entire
   check in `try/except`; fail-**closed** on error (return `"blocked"`) — silent escape is
   worse than a false block.

**Why:** This is the enforcement mechanism. Without this gate, notables visible in the bundle
(step 2) still have no consequence if ignored.

**Verify:**
```bash
uv run pytest tests/ -k "review_phase_federation" -v
```
Add tests (using mock orchestrator):
- Notable with no ack → gate returns `"blocked"`.
- Notable with `addressed` ack → gate returns `"success"`.
- Notable with `reject` ack (no `resolved`) → gate returns `"blocked"`.
- Notable with `reject` ack followed by `resolved` → gate returns `"success"`.
- Notable with `reraised` ack → gate returns `"blocked"`.
- Notable from cycle N not re-emitted at cycle N+1 → gate still blocks (full-history check).
- Out-of-scope notable (file outside reviewer's `reads_glob`) → filtered out, gate passes.
- No notables → gate passes (returns `"success"`).
- Exception in ack read → gate returns `"blocked"` (fail-closed).

Integration note: verify that a notable-only `"blocked"` return flows through
`_route_blocked_phase`'s `check-failure-fallback` branch to the dev phase, and that
`_build_fix_loop_bundle_for_phase` produces a non-empty bundle for that dev re-run. If the
bundle is empty, `_build_fix_loop_bundle_for_phase` returns `None` (orchestrator.py:2946-2947)
and the dev re-runs without finding context — an unbreakable loop. Add an integration test or
explicit assertion that the notable bundle is non-empty when the gate fires notable-only.

---

### 5. Reviewer prompt injection and `dev.yaml` update

**What:**
- `jig/prompt_builder.py`: in the verify-bundle section, when the bundle contains any finding
  with `status="reject"`, inject a paragraph instructing the reviewer to handle rejected findings
  explicitly — call `mark_finding_resolved` to accept, or re-flag to dispute. Reviewer silence
  leaves rejected findings unsatisfied (the gate will re-block).
- `jig/defaults/roles/dev.yaml`: add instruction paragraph after the ruff-check line:

  > Every finding in the fix-loop bundle must be acknowledged before resolving — call
  > `mark_finding_addressed` for each one. For **blocking findings**: fix the issue and call
  > `mark_finding_addressed(finding_id=..., how_resolved="...")`. For **non-blocking findings
  > (fix or explain why not)**: use `kind="addressed"` if you fix it, or `kind="reject"` with
  > a prose explanation if you genuinely disagree. Skipping any finding will block the review
  > phase from advancing.

**Why:** Agents need explicit instruction to use the new mechanism. The prompt injection ensures
all reviewer roles get the `reject` handling instruction without YAML edits.

**Verify:**
```bash
uv run pytest tests/ -k "prompt_builder or dev_role" -v
```
- `build_verify_bundle` returns `status="reject"` for a finding whose latest ack has
  `kind="reject"` (confirming the prompt-injection trigger is exercised end-to-end).
- Verify-bundle prompt with a `reject`-status finding includes the handling instruction.
- Verify-bundle prompt with no `reject` findings does not include it.
- `dev.yaml` loads cleanly via `load_role("dev")`.

---

## Rollback

All changes are in Python source and YAML. No schema migrations, no store format changes
(the new `reject` Literal widens acceptance — existing JSONL rows are unaffected). Roll back
by reverting the branch. The only persistent effect is any `reject` acks written during testing;
these are regenerable from scratch.

## Out of scope for this plan

- SA escalation for `reject`→`reraised` loops (filed as jig #131)
- Dev submission notes / PR-description equivalent (filed as jig #130)
- Cross-ticket carry-forward of unresolved notables
- Auto-applying notables with a `suggested_diff`
- TUI visibility during solo-agent phases (filed as jig #128)

## Change log

- 2026-06-05: Initial draft (Brent Hoover)
