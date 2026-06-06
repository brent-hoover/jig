---
title: Quality Feedback Loop — Completion Record
type: notes
status: active
owner: Brent Hoover
created: 2026-06-05
updated: 2026-06-05
---

# Quality Feedback Loop — Completion Record

Handoff manifest for everything shipped as part of this feature. Authoritative record of what is
now in the codebase as a result of this work.

## What shipped

Five coordinated changes that close two quality feedback gaps identified in the hn-cli eval run.
First, deterministic taxonomy hits that had no matching specialist reviewer were previously dropped
silently; the `_code_metrics_section` fallback now surfaces them all to any reviewer that owns no
taxonomy entries (e.g. `reviewer-generalist` in small-profile runs), annotated with their owning
specialist. Second, notable reviewer findings were never delivered to the dev — `build_fix_loop_bundle`
previously filtered them out entirely. Notables now appear in the dev's fix-loop bundle (all cycles,
pre-filtered via the hallucination guard), and a new gate in `_run_review_phase_federation` returns
`"blocked"` whenever any in-scope notable is unacknowledged. A new `reject` ack kind gives the dev
a machine-verifiable way to formally dispute a finding rather than ignore it; rejections require
non-empty prose rationale, reviewer sign-off to close (silence re-blocks), and are surfaced
distinctly as "DEV REJECTED" in the verify bundle. The WebSocket event stream (`ws_server.py`), dev prompt, and reviewer verify-bundle
prompt are all updated to reflect the new semantics.

## New modules / files

- `tests/test_orchestrator_notable_gate.py` — new file; unit tests for the pure
  `_unacked_notable_finding_ids` helper covering all satisfying/non-satisfying ack transitions

## New / modified tests

- `tests/test_fix_loop_bundle.py` — `TestNotableInclusion` class: 8 new tests for notable
  inclusion (all-cycles, ack satisfaction, routing bypass)
- `tests/test_prompt_builder_metrics.py` — 3 new tests for the taxonomy fallback (fires only when
  reviewer owns no entries, not just when no hits match this diff)
- `tests/test_finding_ack_mcp.py` — 5 new tests in `TestRejectKind`: reject write, default kind,
  invalid kind, empty rationale, whitespace rationale
- `tests/test_store_finding_acks.py` — 1 new test: `reject` kind accepted by Pydantic model
- `tests/test_prompt_builder_verify.py` — 2 new tests: silence warning present for rejected
  notables, absent when no reject findings
- `tests/test_verify_bundle.py` — 3 new tests: `status="reject"` emitted, same-cycle
  addressed-then-reject and reject-then-addressed ordering

## Modified files

- `jig/store/finding_acks.py` — `FindingAck.kind` Literal gains `"reject"` (4th valid value)
- `jig/finding_ack_mcp.py` — `AckKind` gains `"reject"`; `handle_mark_finding_addressed` adds
  optional `kind` param (default `"addressed"`); empty/whitespace reject rationale rejected
- `jig/mcp_server.py` — `mark_finding_addressed` tool schema exposes `kind` param with docs
- `jig/fix_loop_bundle.py` — `build_fix_loop_bundle` gains `in_scope_notable_comments` param;
  notable path selects from all cycles (not latest only), bypasses `_route_one`, uses
  append-order ack satisfaction; `_blocking_findings_section` split into blocking + non-blocking
  sections; `build_verify_bundle` surfaces `reject` ack prose as `dev_claim` with `kind` field
  and `>` ordering for same-cycle claim selection
- `jig/orchestrator.py` — `_unacked_notable_finding_ids` pure module-level helper extracted;
  `_run_review_phase_federation` gains unacked-notable check (fail-closed, hallucination-filtered);
  `_build_fix_loop_bundle_for_phase` now collects/filters notables and passes them to dev-phase
  bundles via `in_scope_notable_comments`
- `jig/prompt_builder.py` — `_code_metrics_section` fallback for unowned taxonomy hits;
  `_verify_findings_section` injects silence warning when rejected notables present, uses
  "DEV REJECTED" label for reject claims; docstring updated with `reject`/`reraised` status values
- `jig/defaults/roles/dev.yaml` — explicit instruction to call `mark_finding_addressed` for every
  finding (including notables) before resolving; `kind="reject"` documented as honest exit
- `jig/ws_server.py` — `_get_findings_for_ticket` recognises `reject` as a valid latest ack status

## Dependencies added

None.

## Interface changes

- **`mark_finding_addressed` MCP tool** — new optional `kind` field (`"addressed"` | `"reject"`,
  default `"addressed"`). Existing callers that omit `kind` are unaffected.
- **`FindingAck.kind` Literal** — widens from `{"addressed","resolved","reraised"}` to include
  `"reject"`. Existing JSONL rows deserialise unchanged.
- **`build_fix_loop_bundle`** — new optional `in_scope_notable_comments` parameter. Callers that
  omit it get the old behavior (no notables in bundle).
- **`build_verify_bundle` return shape** — `dev_claim` dict gains a `kind` field; `status` now
  includes `"reject"` and `"reraised"` as valid values.
- **`_unacked_notable_finding_ids`** — new module-level function in `jig.orchestrator`, exported
  for testability.

## Configuration changes

None. The `reject` ack kind and notable gate are enabled unconditionally; no feature flags required.

## Known issues / follow-ups

- **SA escalation for reject loops** (#131) — when a dev repeatedly rejects and the reviewer
  repeatedly re-flags the same finding, the ticket cycles until `max_fix_cycles` fails it. SA
  escalation after N reject→reraised cycles is deferred; the sa-consult dispatch path in the
  live phase loop does not yet exist.
- **Dev submission notes** (#130) — dev agents have no PR-description equivalent when handing off
  to review. The reviewer sees the diff, metrics, and findings but no dev narrative. Filed as a
  separate feature.
- **Rejected important gate** — a reviewer who silences on a rejected important (neither re-flags
  nor calls `mark_finding_resolved`) allows the important to pass through the existing blocking
  path unblocked. The new gate only enforces silence-blocking for rejected notables. Importants
  rely on the reviewer re-flagging as today.
- **Cross-ticket notable carry-forward** — unresolved notables are not surfaced in the next
  ticket's context on the same epic. Per-ticket gate is sufficient to prevent escapes within a
  ticket; cross-ticket visibility was out of scope.

## Deferred work

None.

## Next steps

- Merge PR #133 once CI passes and any remaining review comments are resolved
- Run the hn-cli eval scenario end-to-end to confirm notables appear in `finding_acks.jsonl` and
  the generalist reviewer prompt includes the unrouted taxonomy hits
- File follow-up issues for SA escalation (#131) and dev submission notes (#130) if not already
  open
- Watch first production run for unexpected `blocked` loops from the fail-closed gate

## Change log

- 2026-06-05: Completed (Brent Hoover)
