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

### Task C / Task O — evaluator spawn prompt composition
_Commits: 52a56bc, f55f455 (O2b)._

* The evaluator agent is spawned via `_spawn_evaluator` with an
  `initial_bus_message` naming the handoff id, but the _prompt
  composition_ (handoff entry + check results + check-failure
  audit + active waivers, structured not free-text) isn't yet
  wired. Resolver + spawn plumbing are done; composition goes
  into `agent.py`'s prompt builder next to the context-bundle
  wiring. Tracked by Task C bullet 4 and Task E "evaluator view"
  bullet, both still `[ ]` in the plan.

### Task E — evaluator view of active waivers
_Commit: 3f212d0._

* Evaluator prompt doesn't yet surface active waivers alongside
  check results. Data is already readable via thread iteration
  (`kind=waiver` + `check_failure_id` non-null), so scripted
  evaluators can find it — but the convenience surfacing waits
  on the same prompt-composition work as Task C above.

### Task F — shadow-pattern detection in `jig validate`
_Commit: 9ec1c77._

* `jig validate` doesn't warn when a `writable` path is fully
  subsumed by a `denied` glob (or vice versa). Not a correctness
  bug — the compiler still emits a deterministic ruleset — but
  it leaves a silent footgun where a permit looks effective in
  YAML but never fires at the hook boundary. Landing this needs
  a glob-subsumption check similar to the segment matcher in
  `_hooklib.py`.

### Task J — promoted deferred-items in evaluator prompt
_Commit: c515c82._

* When a checkpoint deferred-item is promoted to a child ticket,
  the spawning agent's prompt doesn't yet announce the promoted
  ticket id. Blocked on Task C's evaluator-prompt composition
  (same prompt-builder work as Task E). Tracked as bullet 3
  under Task J in the plan.

### Task K — doc cross-refs for human-target escape hatch
_Commit: f069bb6._

* The `"human"` / `"any_human"` escape-hatch semantics are
  encoded in `_HUMAN_TARGET_ESCAPE_HATCH` and locked down by
  tests, but docs 08 ("threads") and 16 ("policy-and-
  enforcement") don't mention them explicitly. Next time either
  doc is edited, add a one-liner referencing this constant.

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

### Task M — section-lock enforcement
_Tracked as a carry-over task in the plan, not yet started._

* Spec section locks (`locked_after_phase`) parsed in Phase 3F
  but not enforced in the proposal-accept path. Full plan:
  `docs/implementation-plan.md` §Task M.

### Task N — helper-agent spawning
_Tracked as a carry-over task in the plan, not yet started._

* `human_with_helper` owner resolution returns the
  `helper_template` name (Phase 3), but the pre-human spawn
  isn't wired. Full plan: `docs/implementation-plan.md` §Task N.

### Task P — Phase 5 E2E integration tests
_Tracked as a test task in the plan, bullets all `[ ]`._

* Unit coverage is in place per-task, but the seven E2E paths
  listed under Task P (required-fail-then-fix, self-cert
  conflict, black-box QA can't read src, hook-level rm -rf /
  force-push / spec-write deny, promoted deferred-item, T2
  escalation) are still open. Meaningful when the dogfood
  harness is observable enough to assert them (memory note:
  observability is the current next priority).

## Phase 5 — landed

* ~~**Deferred-item → ticket promotion** (Phase 4 carry-over).~~
  Landed as Task J in c515c82.
* ~~**Thread-target enforcement** (Phase 4 parsed-not-enforced).~~
  Landed as Task K in f069bb6.
* ~~**Deadlock auto-resolution** (Phase 4 carry-over).~~
  Landed as Task L in 73ee3c0.
* ~~**Waiver authority as capability** (Phase 4 carry-over).~~
  Landed as Task H in ed8bb45.

## Cross-phase — still open

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
