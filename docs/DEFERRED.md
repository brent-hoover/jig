# Deferred Items

Running log of things we _almost_ shipped but cut from scope at the
last minute, with why + where the breadcrumb lives. Companion to the
implementation plan's per-phase `### Explicitly deferred out of
Phase N` sections — those document planned-out-of-scope from the
start; _this_ file documents in-flight trims that landed during a
task.

**Rules of engagement:**

* One entry per defer. Cross-ref the commit that shipped the
  cut-down version so reviewers can see what was on the table.
* Note _why_ it was deferred (scope, dependency, dep-free
  preference, blocked on another task). Without the "why" this
  becomes a stale TODO list.
* Strike through (`~~...~~`) when picked up, with the commit that
  did it. Keep the history — it documents "we thought about this,
  here's when it landed."
* New defers go at the top of their section, most recent first.
* For planned per-phase scoping, see the plan itself:
  `docs/implementation-plan.md` → `### Explicitly deferred out of
  Phase N`.

---

## Phase 5 — still open

_Phase 5 is effectively closed. The only items still actionable are
the two Task L extensions (per-phase threshold overrides, freezegun
consolidation) and Task P (E2E integration tests). The observability
milestone that gated Task P shipped in plan
`2026-04-23-per-ticket-story-logs` (structured JSONL logs with
correlation contextvars, timing SystemEvents, `jig.story` library,
`jig story` CLI). Everything else in this section is struck-through
and retained for history._


### ~~Task C / Task O — evaluator spawn prompt composition~~
_Landed._

* ~~The evaluator agent is spawned via `_spawn_evaluator` with an
  `initial_bus_message` naming the handoff id, but the _prompt
  composition_ (handoff entry + check results + check-failure
  audit + active waivers, structured not free-text) isn't yet
  wired.~~ Composition landed in `prompt_builder._evaluator_section`:
  orchestrator pre-assembles `latest_batch` into
  `initial_bus_message.check_results`, prompt builder renders
  Handoff record, structured Check results (fenced excerpts on
  non-pass), Check-failure audit (with WAIVED flag), Active
  waivers (both variants), and Helper-agent drafts. EVALUATOR
  branch in `_instructions_section` pins the handoff id
  literally and references `thread_accept_handoff` /
  `thread_reject_handoff`.

### ~~Task E — evaluator view of active waivers~~
_Landed._

* ~~Evaluator prompt doesn't yet surface active waivers
  alongside check results.~~ Rendered by `_evaluator_section`'s
  "Active waivers" block — walks the ticket's waivers and
  surfaces both variants (check-failure and objection).

### ~~Task F — shadow-pattern detection in `jig validate`~~
_Landed._

* ~~`jig validate` doesn't warn when a `writable` path is fully
  subsumed by a `denied` glob (or vice versa).~~ Landed as
  `catalog.collect_policy_warnings`: conservative segment-wise
  subsumption (`_segs_subsume` + `_segment_subsumes`) over
  `resolve_uri_glob`-normalised forms, surfaced as `[WARN]` in
  `jig validate`. Advisory only; catalog still compiles to a
  deterministic ruleset.

### ~~Task J — promoted deferred-items in evaluator prompt~~
_Landed._

* ~~When a checkpoint deferred-item is promoted to a child
  ticket, the spawning agent's prompt doesn't yet announce the
  promoted ticket id.~~ Landed with Task C: `_evaluator_section`
  walks the handoff's `deferred_items` and renders each item's
  `status` plus the child `promoted_ticket_id` when present.

### ~~Task K — doc cross-refs for human-target escape hatch~~
_Landed._

* ~~The `"human"` / `"any_human"` escape-hatch semantics are
  encoded in `_HUMAN_TARGET_ESCAPE_HATCH` and locked down by
  tests, but docs 08 ("threads") and 16 ("policy-and-
  enforcement") don't mention them explicitly.~~ Added
  explicit notes in both docs referencing the constant
  location (`jig/thread_mcp.py`).

### Task L — per-phase deadlock threshold overrides
_Commit: 73ee3c0._

* Shipped project-wide `deadlock.nudge_after_s` /
  `escalate_after_s` only. Doc 08 leaves per-phase overrides as
  an open extension; not a working-capacity constraint today.
  Revisit when a project wants differentiated cadence (tighter
  review phase, looser dev).

### Task L — freezegun-based tests
_Commit: 73ee3c0._

* Tests use an injected `now` kwarg on `sweep_blocking_entries`
  rather than pulling in `freezegun` as a dep. Pure-function
  shape is easier to test anyway, but if we ever add freezegun
  for a different reason the deadlock tests are a candidate for
  consolidation.

### ~~Task M — section-lock enforcement~~
_Landed._

* ~~Spec section locks (`locked_after_phase`) parsed in Phase 3F
  but not enforced in the proposal-accept path.~~ Enforcement
  wired in `proposal_mcp.handle_resolve_proposal` via
  `jig/section_locks.py`; `jig validate --ticket-id` surfaces
  the active lock map as part of pre-flight.

### ~~Task N — helper-agent spawning~~
_Landed._

* ~~`human_with_helper` owner resolution returns the
  `helper_template` name (Phase 3), but the pre-human spawn
  isn't wired.~~ Spawn wired in
  `proposal_mcp.handle_propose_change` via
  `jig/helper_spawn.py`: short-lived check-agent-style spawn
  with `submit_helper_draft` as the single scoped MCP tool,
  best-effort on timeout / crash / no-draft, draft posted as
  a `Note` with `responds_to=<proposal.id>` and
  `author=<helper_role_name>` so readers can tell it's a
  helper draft. Explicit "helper-draft" labeling in the
  evaluator prompt still waits on Task C (see new entry
  below).

### ~~Helper-draft labeling in evaluator prompt~~
_Landed._

* ~~The helper's `Note` is visible to evaluators via thread
  iteration, but isn't singled out as a "helper draft" vs a
  plain human Note in the prompt-composition layer.~~ Landed
  with Task C: `_evaluator_section` computes the set of
  proposal ids and labels any `Note` whose `responds_to` is in
  that set under a dedicated "Helper-agent drafts" heading.

### Task P — Phase 5 E2E integration tests
_Tracked as a test task in the plan, bullets all `[ ]`._

* Unit coverage is in place per-task, but the seven E2E paths
  listed under Task P (required-fail-then-fix, self-cert
  conflict, black-box QA can't read src, hook-level rm -rf /
  force-push / spec-write deny, promoted deferred-item, T2
  escalation) are still open. The observability gate has
  lifted (see
  `docs/superpowers/plans/2026-04-23-per-ticket-story-logs.md`);
  these tests can now assert on `jig story` narratives with
  `phase_start` / `agent_run` / `phase_end` timing events and
  structured log correlation.

## Phase 5 — landed

* ~~**Shadow-pattern detection** (Task F).~~ Landed this commit —
  `catalog.collect_policy_warnings`.
* ~~**Evaluator spawn prompt composition** (Tasks C/E/J₃ +
  helper-draft labeling).~~ Landed in the prior commit —
  `prompt_builder._evaluator_section` + orchestrator bundle
  assembly.
* ~~**Helper-agent spawning** (Phase 3 parsed-routing carry-over).~~
  Landed as Task N (this commit).
* ~~**Section-lock enforcement** (Phase 3F parsed-not-enforced).~~
  Landed as Task M.
* ~~**Deferred-item → ticket promotion** (Phase 4 carry-over).~~
  Landed as Task J in c515c82.
* ~~**Thread-target enforcement** (Phase 4 parsed-not-enforced).~~
  Landed as Task K in f069bb6.
* ~~**Deadlock auto-resolution** (Phase 4 carry-over).~~
  Landed as Task L in 73ee3c0.
* ~~**Waiver authority as capability** (Phase 4 carry-over).~~
  Landed as Task H in ed8bb45.

## Cross-phase — still open

### TUI integration of per-ticket story view
_Plan: `2026-04-23-per-ticket-story-logs.md` §Layer F._

* The library (`jig.story`) and the CLI (`jig story`) shipped.
  The TUI has no story view yet — users who want a narrative
  shell out to `jig story <tid>`. Proper integration would
  expose `build_story` / `stream_story` through a new
  `ws_server.py` handler (`story.request` / `story.subscribe`)
  and render it in a ticket-detail pane. Lands when the TUI
  grows a ticket-detail view; today's ticket list view has no
  need for it.

### `issue_id` → `ticket_id` alias shim in memory store
_Commit: 4196b91 (I3)._

* `_migrate_issue_id` in `jig/store/memory.py` silently aliases
  pre-rename records so they still load. Called "cheap shim;
  drop in a later release" at the time. No production users
  existed then and none do now, so the shim has no constituency
  — but nobody's set a release to actually remove it. When
  Phase 6 (SCM integration) touches migrations, drop it.

### Custom URI resolvers (`jira://`, `wiki://`)
_Plan: Phase 2 §Explicitly deferred._

* URI scheme dispatcher in `context_resolver.py` is extensible
  but there's no registration API for third-party schemes.
  Deferred until a team actually needs one. Not blocking any
  downstream work.

### Per-phase context composition (doc 07's four-layer model)
_Plan: Phase 2 §Explicitly deferred._

* Roles declare `default_context`; phases can't add/override
  context today. Doc 07 describes base/role/phase/ticket
  layering. Lands when a phase actually needs per-phase
  context — probably alongside the evaluator prompt work above.

### Project-level spec (doc 02 `.jig/spec/project.md`)
_Plan: Phase 3 §Explicitly deferred. Also blocks spec-agent role
and cross-ticket proposal routing._

* Standalone implementation pass owning its own phase. Several
  downstream items (spec agent, cross-ticket proposals) are
  blocked on it.

### Refinement loop state machine
_Plan: Phase 3 §Explicitly deferred._

* `"refining"` is recognized as a proposal state, but multi-
  round back-and-forth is modeled only as chronological
  comments. Upgrade when proposal churn on a single ticket
  becomes a real pain.

### Automated merge on proposal-accept
_Plan: Phase 3 §Explicitly deferred._

* The acceptor hand-produces the merged YAML today. Text diff
  + conflict detection stays out until needed.

### Checkpoint compaction
_Plan: Phase 4 + Phase 5 §Explicitly deferred._

* Doc 09 §Compaction. Token pressure isn't real yet; capture
  every checkpoint, compact later. Tied to thread-summarization
  entry type below (same deferral window).

### Thread summarization / compaction-entry type
_Plan: Phase 4 + Phase 5 §Explicitly deferred._

* Doc 08 §Thread as context for later spawns. Waits on
  checkpoint compaction landing.

### Harness-capabilities meta-tool
_Plan: Phase 4 §Explicitly deferred._

* Doc 08 mentions `harness_capabilities()` for agent
  introspection. Nice-to-have; not blocking.

### PR-comment output channel
_Plan: Phase 4 §Explicitly deferred → Phase 6._

* `output_channel: pr_comments | both` for review phases.
  Belongs with SCM integration.

### Phase 5 explicitly out-of-scope (for posterity)
_Plan: Phase 5 §Explicitly deferred / Out of scope._

* Agent-check evaluation harness (calibration test set).
* Policy testing framework (unit-testable rule declarations).
* Policy bypass analytics (waiver/deny dashboards).
* Runtime capability elevation — v2+; escalation path is
  sufficient.
* Check result caching / partial re-runs — full re-run is the
  v1 answer.
* Dynamic (runtime-computed) rules — everything compiles at
  spawn.
* Cross-project policy sharing — one project per service.
* Helper-agent UI / interactive editing — Phase 7 (spawn lands
  in Task N).
