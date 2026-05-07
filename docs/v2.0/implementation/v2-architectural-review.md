### Major architectural issues

1. `jig/orchestrator.py:189`: the agent lifecycle integration spine is too centralized in `_run_agent_with_analytics`.

   The method now owns lifecycle analytics, dev-env provisioning, fixture-mode injection intent, cleanup, tier promotion, and calibration recording. This is workable for v2, but it is already behaving like an implicit plugin chain without plugin-chain structure. Every new cross-cutting behavior has to edit the same method, and ordering/error semantics are informal.

   v2.x note: introduce an explicit lifecycle hook registry with ordered phases (`before_spawn`, `after_result`, `after_failure`, `after_cleanup`) and a clear policy for hook failures.

2. `jig/reviewers/dispatch.py:144`: reviewer federation has competing execution models.

   Mechanical reviewers are Python classes returning `ReviewerComment`; judgment/specialty reviewers are role configs plus `reviewer_post_comment`; visual/accessibility/responsive reviewers are Python classes again. Selection and execution are split, and not every selected reviewer has an execution path. This makes it easy for the federation to say a reviewer is selected while nothing actually runs.

   v2.x note: create one reviewer registry contract: reviewer id, cadence, deterministic-vs-agent-backed mode, execution adapter, and comment persistence path.

3. `jig/sim/driver.py:400`: the simulator is mostly a scripted integration harness, not a true synthetic operator.

   The `INVOKE_*` table is readable, but many steps directly call MCP handlers or mutate project state, then assert shallow effects. That validates handler plumbing more than operator-driven workflow composition. This is acceptable as a v2 harness if described honestly, but it should not be treated as full end-to-end behavioral validation.

   v2.x note: keep direct handler steps for regression coverage, but add a smaller set of true operator-path scenarios that drive the same surfaces a human would use.

4. `jig/schemas/*` versus `jig/ticket.py`: schema discipline is split between v2 schemas and runtime/store models.

   The dedicated v2 schema package has shared kebab-case, URI-shape, and timezone-aware validators. Runtime records such as `Ticket`, store entries, reviewer inputs, simulator params, and path-helper ids remain looser even though they are now v2 coordination artifacts. The system boundary is not only `jig/schemas/*`; runtime records are ingress points too.

   v2.x note: move shared id/path/timestamp validation into reusable constrained types and apply them across `Ticket`, simulator schemas, reviewer inputs, and path helpers.

5. `jig/intent.py:34`: the intent layer is not yet a real orchestration dependency.

   Intent blocks are present and reviewers can inspect them, but downstream systems mostly do not consume intent to make routing, planning, dispatch, or reviewer decisions. Today it functions as cognitive scaffolding and review material, not as a coordination primitive. That may be the right v2 tradeoff, but the implementation should avoid claiming intent "carries" orchestration until something depends on it.

   v2.x note: either keep intent explicitly scoped as authoring discipline, or make selected flows consume it directly, for example planner decomposition checks, reviewer routing, or cascade rationale validation.

### Summary

The major architectural issue is not one bad abstraction. Several cross-cutting systems landed as adjacent slices and still need explicit contracts at their composition boundaries: agent lifecycle hooks, reviewer selection/execution, simulator steps/assertions, and schema/path validation.

• A local MCP for everything is a good default for jig’s agent-facing surface, but not a good universal
  abstraction for all internal composition.

  Use MCP for boundaries where you want explicit permissions, auditability, stable tool contracts, and
  agent ergonomics. That fits PO/SA/PM/VD authoring tools, reviewer comment posting, context lookup,
  ontology edits, fixture controls, and operator-facing actions.

  Do not force MCP between internal Python subsystems that need strong typing, ordering, transactions,
  or shared error semantics. Agent lifecycle hooks, reviewer dispatch internals, simulator assertion
  evaluation, schema validation, and persistence helpers should stay ordinary Python interfaces with
  explicit registries/contracts. Wrapping those in MCP would add serialization and tool-call ceremony
  without improving correctness.

  The useful split is:

  - Agent-facing actions: local MCP tools.
  - Human/CLI/TUI commands: call the same Python service layer that MCP tools call.
  - Internal composition: typed Python registries/interfaces.
  - External services: MCP only when the service is naturally tool-like or third-party; otherwise use
    normal client libraries behind a service boundary.

  For jig specifically, “local MCP for everything agents can do” is aligned with the tenets: strict
  tool surfaces, exact context, auditable actions. “MCP for everything the codebase does” would likely
  make the architecture worse, especially around _run_agent_with_analytics, reviewer execution, and
  simulator internals.