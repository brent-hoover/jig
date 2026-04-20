# Implementation Plan

How we get from the current code to the architecture described in
`docs/01-*.md` through `docs/17-*.md`.

Phase-by-phase. Each phase has a goal, a task list, exit criteria, and
an explicit list of what's deferred to later phases. Review at phase
boundaries before committing to the next.

## Status conventions

Within each phase task list:

- `[ ]` — not started
- `[~]` — in progress
- `[x]` — done
- `[-]` — dropped / deferred (with reason)

## Phase list

1. **Foundation** — terminology alignment, classification schema on
   tickets, directory layout per doc 17, dead-code cleanup. Unblocks
   every later phase.
2. **Catalog & context resolution** — role/workflow/check catalogs
   with project-override semantics, three-scope URI resolution
   (`project://`, `role://`, `ticket://`), load-time validation.
3. **Structured specs & ownership** — ticket specs per work_type,
   owner roles (PO, SA), Proposal thread entries, self-certification
   guard.
4. **Threads & checkpoints** — typed thread entries with gating
   (question/answer/objection/resolution/proposal/request-for-human),
   per-phase checkpoint discipline with deferred-item tracking.
5. **Verification & policy** — check catalog execution, asymmetric
   validation agent, policy enforcement via Claude Code hooks.
6. **SCM integration** — mostly-local mode, then hybrid mode with
   issue round-tripping.
7. **Service & TUI alignment** — WebSocket replay-since-sequence,
   auth abstraction, TUI views for new data shapes, possible SQLite
   swap for message bus.

Cross-cutting every phase: tests, migration of any live `.jig/` data,
TUI updates for that phase's new model.

---

## Phase 1 — Foundation

### Goal

Align the code's terminology and core ticket schema with the docs.
Adopt doc 17's directory layout. Fix the small dead-code items the
survey turned up. The prize: subsequent phases don't trip over stale
names or missing classification fields.

No behavioral changes to the orchestrator's dispatch logic yet. No new
phases in the workflow model. No policy enforcement. Just foundational
alignment.

### Tasks

**A. Terminology renames in code**

- [x] `.jig/agent_types/` → `.jig/roles/` (project directory)
- [x] `jig/defaults/agent_types/` → `jig/defaults/roles/` (shipped
      defaults)
- [x] `AgentTypeConfig` → `RoleConfig` (class), update all imports
      (kept `AgentTypeConfig` as a transitional alias during Phase 1)
- [x] `agent_type` → `role` in variable/field names where it refers to
      the role template (not the runtime agent instance)
- [x] `issue_id` → `ticket_id` in `MemoryStore` models (`Handoff`,
      `Learning`) and related JSONL index keys
- [x] `templates/` (repo root) → `jig/defaults/project_templates/`
      per doc 17
- [x] Grep for any `.agents/` string literals in code; replace with
      `.jig/`

**B. Ticket schema — classification axes**

Current: `Ticket.type: TicketType` with values
`{feature, bug, chore, task, question}`.

Target: `Ticket.work_type: WorkType` with values
`{feature, bugfix, refactor, spike, perf, migration, docs}` (doc 03),
plus `Ticket.size: Size` with values `{xs, s, m, l, xl}`.

- [x] Add `WorkType` enum per doc 03's shipped set
- [x] Add `Size` enum (`xs`, `s`, `m`, `l`, `xl`)
- [x] Rename `Ticket.type` → `Ticket.work_type`, expand enum values
- [x] Add `Ticket.size` field (default: `m`; TUI exposes it in the
      creation form)
- [x] Migration mapping for existing ticket data:
  - `bug` → `bugfix`
  - `chore` → `refactor` (closest fit)
  - `task` → `refactor` (closest fit; re-tag later; `workflow="thread"`
    preserved so dispatch behavior is unchanged)
  - `question` → dropped as work_type; questions live as thread
    entries per doc 08 (Phase 4). For now, migrate to `feature` and
    flag for manual cleanup (`workflow="thread"` preserved)
- [x] Keep `TicketType` import alias for one release cycle to soften
      the rename, then drop
- [x] Update `TicketStore.TOP_LEVEL_TYPES` to match new enum (renamed
      to `TOP_LEVEL_WORK_TYPES`; old name kept as alias)

**C. Config file migration**

Current: `.jig/project.json` with `Project` model.

Target: `.jig/config.yaml` per doc 17 with `project:`, `workflows:`,
`ownership:`, `roles:`, `escalation:` sections.

- [x] Define `Config` pydantic model with nested sections
- [x] Load `.jig/config.yaml` as primary; fall back to
      `.jig/project.json` with a deprecation warning for one cycle
- [x] `jig init` writes config.yaml, not project.json
- [x] `workflows.by_type.<type>.default_by_size` and
      `workflows.by_type.<type>.available` accepted and parsed; not
      yet *used* to drive workflow resolution (that's Phase 2)
- [x] `ownership:` section accepted; not yet enforced (Phase 3)
- [x] `roles:` section for po/sa assignments accepted; not yet used
      (Phase 3)

**D. Directory layout per doc 17**

Project repo `.jig/`:

- [x] Create `.jig/spec/` (empty placeholder; populated Phase 3)
- [x] Create `.jig/context/project/` (empty placeholder; populated
      Phase 2)
- [x] Create `.jig/context/roles/` (empty placeholder)
- [x] Create `.jig/decisions/` (empty placeholder; populated Phase 3)
- [x] Create `.jig/archive/` (empty placeholder)
- [x] Create `.jig/checks.yaml` (empty catalog; Phase 5 populates)
- [x] Keep `.jig/worktrees/` (operational; not in doc 17 but needed)
- [x] Keep `.jig/store/` JSONL layout (SQLite migration deferred
      until Phase 7)

Core repo `jig/`:

- [x] `jig/defaults/roles/` (renamed from `agent_types/`)
- [x] `jig/defaults/workflows/` (exists; keep)
- [x] `jig/defaults/project_templates/` (moved from root
      `templates/`)

**E. Dead-code fixes from the survey**

- [x] Interpolate `PhaseConfig.task_template` in `prompt_builder.py`
      when building the agent prompt (currently stored, never
      expanded)
- [x] Surface `PhaseConfig.acceptance_criteria` in the agent prompt
      (currently stored, never shown to the agent)
- [x] Wire `Project.merge_strategy` into the workflow's terminal
      phase so squash vs PR vs direct merge actually does something
      different (minimal; full merge behavior is Phase 6) — already
      wired via `orchestrator._on_ticket_completed` →
      `worktree.merge_ticket(..., strategy)`
- [x] Drop or rename `AgentTypeConfig.can_message` — it's a stub with
      no enforcement; either delete or document as Phase 5 policy
      input. Dropped; capability policy moves to role templates per
      doc 16 in Phase 5.

**F. Load-time validation (partial)**

Full catalog validation per doc 17 §Validation at load arrives in
Phase 2. For Phase 1:

- [x] Parse `config.yaml`'s `workflows.by_type` section, warn (don't
      fail) on undefined workflow references. Phase 2 upgrades warn
      to fail.
- [x] Validate that ticket's `work_type` field is a known value;
      reject unknowns at `create_ticket` MCP handler

**G. TUI surfacing**

- [x] `new-ticket-form.tsx` — add dropdown for `work_type` and `size`
- [x] `ticket-detail.tsx` — show `work_type` and `size` fields
- [x] `ticket-list.tsx` / `kanban-view.tsx` — show size as a small
      chip next to the title
- [x] Hydration path in `use-jig-socket.ts` — include new fields in
      Ticket reconstruction

**H. Tests**

- [x] Unit tests for new `WorkType` / `Size` enums and validation
- [x] Migration test: legacy tickets with old `type` values load
      correctly (model-level kwarg + on-disk JSONL round-trip)
- [x] Config loader test: `.jig/config.yaml` parsed correctly;
      fallback to `.jig/project.json` works (covered in Task C tests)
- [x] Catalog structure test: `jig/defaults/roles/` loads all shipped
      role files (covered in `TestDefaultRoles`)
- [-] TUI smoke test: no Bun test infra exists yet; deferred to
      Phase 2 per the plan's own hedge. `bunx tsc --noEmit` on the
      typed surface stays green.

### Exit criteria

- `jig start` runs against a fresh-init project without warnings or
  errors.
- `jig init` lays down `.jig/` matching doc 17's layout.
- Existing `.jig/` projects continue to work via the project.json
  fallback, with a deprecation warning pointing at `config.yaml`.
- All pytest tests pass; `ruff check jig/` clean.
- TUI shows `work_type` and `size` on tickets.

### Explicitly deferred out of Phase 1

- Actually *using* `workflows.by_type` to resolve workflow at ticket
  creation → Phase 2.
- Per-work-type spec schemas → Phase 3.
- Ownership enforcement (Proposals, self-certification guard) → Phase 3.
- Thread typing and gating → Phase 4.
- Checkpoint store → Phase 4.
- Three-scope URI resolution → Phase 2.
- Check catalog execution and asymmetric validation → Phase 5.
- Policy hooks → Phase 5.
- SCM mode handling → Phase 6.

### Risks and decisions to make during Phase 1

- **`type` → `work_type` migration risk.** Existing tickets stored
  with `type: bug|chore|task|question` need to keep loading. The
  migration mapping above is lossy (task → refactor is a guess). Since
  we have no production projects yet (confirmed in earlier
  discussion), this is low risk — but the migration logic should
  exist regardless, for any local dev data.
- **Keep `TicketType` enum class?** Options: (a) rename to `WorkType`
  cleanly; (b) keep `TicketType` as an alias for `WorkType` during
  transition. Vote: (a). No legacy projects to appease.
- **`config.yaml` vs `config.toml`.** Doc 17 specifies YAML. Keep.
  The code already pulls PyYAML.
- **Worktree path convention.** Doc 17 doesn't mention `.jig/worktrees/`
  because it's an operational concern, not part of the shipped
  surface. Kept as-is. Worth a brief note in the doc; not blocking.

---

## Phase 2 — Catalog & context resolution

### Goal

Make the catalog and context systems behave the way docs 07 and 17
say they should:

- **Three-scope context URIs** (`project://`, `role://`, `ticket://`,
  plus `decision://` and `repo://`) replacing the current single
  `issue://` scheme.
- **Project-override semantics for catalogs** — project repo's
  `.jig/roles/<name>.yaml` replaces `jig/defaults/roles/<name>.yaml`
  at resolution time, rather than being pre-copied at `jig init`.
- **Workflow resolution from config** — ticket creation consults
  `workflows.by_type.<type>.default_by_size` / `default_by_size` /
  explicit override, promoting Phase 1F's parsed-but-unused config
  into actual behavior.
- **Load-time validation that fails loud** — unknown role/workflow/
  check references, missing required context URIs, malformed YAML.
  Phase 1F's advisory warnings become errors.

No agent-behavior changes beyond what falls out of the better
context. No ownership enforcement, no thread typing, no check
execution — those stay in later phases.

### Tasks

**A. Context URI scheme — multi-scheme resolver**

Current: `jig/context_resolver.py` handles `issue://description`,
`issue://design`, `issue://plan` only.

Target per doc 07: `project://`, `role://`, `ticket://`,
`decision://`, `repo://`.

- [x] Add scheme dispatcher in `context_resolver.py` keyed on URI
      prefix; return resolver function per scheme
- [x] `project://<path>` → read `.jig/context/project/<path>`. If
      no extension, try `.md` then raw
- [x] `role://<role>/<path>` → read `.jig/context/roles/<role>/<path>`
- [x] `ticket://description` → current `issue://description` behavior
- [x] `ticket://design` → current `issue://design` behavior (decision
      comments + `docs/design*` etc. in worktree)
- [x] `ticket://plan` → current `issue://plan` behavior
- [x] `ticket://thread` → concatenate comments for the ticket in
      chronological order (stub for Phase 4 thread work; reads from
      `CommentStore` today)
- [x] `decision://<id>` → read `.jig/decisions/<id>.md`
- [x] `repo://<path>` → read `<worktree>/<path>` (escape hatch)
- [x] Keep `issue://` as a transitional alias mapping to `ticket://`,
      logging a deprecation warning once per process

**B. Required vs optional references**

Doc 07 §Required vs. optional. Today every URI is silently skipped if
unresolved — no distinction between "critical" and "nice to have".

- [x] Extend `RoleConfig` with `required_context: list[str]` alongside
      the existing `default_context` (kept as optional). Or adopt a
      structured form (`{uri: str, required: bool}`) — pick one, document
      choice in commit message
- [x] At spawn, fail (raise → surfaces as `ticket_failed`) if any
      required URI fails to resolve; warn on optional failures
- [x] Load-time validation walks every role template and asserts
      required URIs would resolve against the project layout (project://
      and role:// only — ticket:// / repo:// are per-spawn)

**C. Catalog resolution order**

Doc 17 §Resolution order: project repo first, shipped default second.
Today `load_role` / `load_workflow` only check the project repo —
defaults are copied in at `jig init`.

- [x] Rewrite `load_role(name)` / `load_workflow(name)` to:
  1. try `<project>/.jig/<kind>/<name>.yaml`; if present, use it
  2. else try `jig/defaults/<kind>/<name>.yaml`; if present, use it
  3. else raise `FileNotFoundError` with both searched paths
- [x] Add `list_roles` / `list_workflows` variants that merge the two
      layers (project overrides default, dedup by name)
- [x] Stop copying shipped defaults into `.jig/roles/` and
      `.jig/workflows/` at `jig init`. `save_default_roles` /
      `save_default_workflow` deprecated in the hot path; kept as a
      `jig role init <name>` scaffolding command for teams that want
      to customize one
- [x] Migration: existing projects already have full copies under
      `.jig/`; they continue to resolve via step 1 untouched

**D. Workflow resolution from config**

Promote Phase 1's parsed-but-unused `workflows:` section to drive
actual behavior at ticket creation.

- [x] Add `resolve_workflow(config, *, work_type, size, explicit=None)
      -> str` in `jig/config.py`:
  - explicit override wins if provided and is in `available`
  - else `workflows.by_type.<work_type>.default_by_size.<size>`
  - else `workflows.by_type.<work_type>.available[0]` if single-item
  - else top-level `workflows.default_by_size.<size>`
  - else fall back to `"default"` (preserves current behavior)
- [x] Wire into `ticket_mcp.create_ticket`: if caller didn't specify
      `workflow`, resolve one. Persist the resolved name on the ticket
      (already persisted — no schema change)
- [x] Validate that resolved workflow is in `workflows.available` (if
      populated); reject with explicit error otherwise
- [x] Phase 1 warnings upgrade to errors at load (Task F)

**E. Check catalog loader (shape only; execution is Phase 5)**

- [x] Add `jig/checks.py` with a `CheckCatalog` pydantic model
      covering all three check types per doc 10 (scripted,
      implementation_aware_agent, black_box_agent)
- [x] Parse `.jig/checks.yaml` at load; empty catalog is valid
- [x] Validate per-check required fields (type, command or template,
      severity)
- [x] Don't execute — just assert shape and collect names for
      cross-reference validation in Task F

**F. Load-time validation (full, fail-loud)**

Upgrade Phase 1F's advisory warnings to hard errors. Add the full
set from doc 17 §Validation at load.

- [x] New `jig/catalog.py` module exposing
      `validate_catalog(project_path) -> None` that raises
      `CatalogError` on the first failure, with a list-all mode
      `validate_catalog(..., collect=True) -> list[str]`
- [x] Checks performed:
  - Unknown role names referenced from workflow phases
  - Unknown workflow names in `config.yaml`'s `default_by_size`,
    `available`, `by_type.*.default_by_size`, `by_type.*.available`
  - Unknown check names referenced from workflows (catalog + phase
    checks — stubs for now)
  - Missing required context URIs in role templates
    (`project://`, `role://` only)
  - Malformed YAML (covered by pydantic today; surface cleanly)
- [x] `jig start` calls `validate_catalog` before any loop starts;
      exit non-zero with readable error on failure
- [x] `jig validate` (no `--ticket-id`) becomes a catalog dry-run
      that calls the same validator and prints the collect-all list;
      current per-ticket form stays available via explicit flag

**G. Documentation and default-template updates**

- [x] Migrate shipped role defaults' `default_context` entries from
      `issue://design` etc. to `ticket://design`. Keep one test asserting
      the `issue://` alias still works (Task A)
- [x] Update doc 07 / doc 17 cross-references in the shipped defaults'
      `phase_prompt` wording where it mentions "issue"/"work unit" →
      "ticket" (doc sweep done in Phase 0; this is a code/yaml sweep)

**H. Tests**

- [x] Context resolver: one test per scheme, plus the deprecation
      alias path. Required vs optional: missing required raises;
      missing optional warns and continues
- [x] Catalog resolution order: project override wins, default
      fallback loads, missing both raises with both paths named
- [x] Workflow resolution: cover explicit override, per-type
      default_by_size, top-level default_by_size, fallback to
      `"default"`, rejection of not-in-`available`
- [x] Check catalog: empty file OK, missing required field errors,
      shape validation per type
- [x] `validate_catalog`: exercise each failure mode; collect-all
      returns every failure, raise-first stops at first

### Exit criteria

- `jig init` on a fresh repo writes `.jig/` without any role or
  workflow files under `.jig/roles/` or `.jig/workflows/` — resolution
  comes from the shipped package at runtime. Context/decision
  placeholders remain.
- Existing projects with pre-copied roles/workflows under `.jig/`
  continue to work (project layer wins per Task C).
- Creating a ticket with `work_type=bugfix, size=xs` through the MCP
  picks the workflow named in
  `workflows.by_type.bugfix.default_by_size.xs` without the caller
  specifying it.
- A role template referencing `project://architecture` when that
  artifact is missing causes `jig start` to fail with a readable error.
- `jig validate` (no args) walks the catalog and reports every load
  issue, exit code non-zero if any.
- All pytest tests pass; `ruff check jig/` clean; `bunx tsc --noEmit`
  in `tui/` clean.

### Explicitly deferred out of Phase 2

- **Typed thread entries** — `ticket://thread` in Phase 2 reads the
  flat CommentStore. Phase 4 replaces the entries with the typed
  (question/answer/objection/…) set, and `ticket://thread` changes
  shape accordingly.
- **Checkpoint store** — no `ticket://checkpoints` URI yet. Phase 4.
- **Per-phase context composition** — roles declare
  `default_context`; phases today don't add context. Doc 07's
  four-layer composition (base/role/phase/ticket) with override
  semantics waits until a phase actually needs to override role
  context, probably alongside the thread work in Phase 4.
- **Check execution** — Task E validates shape only. Phase 5 runs
  scripted / agent-based checks and wires evaluator judgment.
- **Custom URI resolvers** (`jira://`, `wiki://`) — scheme is
  extensible but no registration API lands in Phase 2. Deferred until
  a team needs one.
- **Ownership enforcement** — config's `ownership:` / `roles:`
  sections parsed but still not enforced. Phase 3.

### Risks and decisions

- **`default_context` schema change.** Moving to a structured
  `{uri, required}` form is the cleanest; keeping `default_context`
  list-of-strings + adding `required_context` list-of-strings is the
  smallest-delta approach. Vote: the latter — fewer touched YAMLs,
  easier migration. Revisit if phase-level context overrides (deferred)
  warrant the restructure.
- **Stop pre-copying defaults at init.** Behavior change for anyone
  who's scripted against the post-init layout. Mitigation: the runtime
  still serves the same templates; the only visible delta is that
  `.jig/roles/` starts empty on new projects. Call out in the commit
  message; no existing projects affected because their copies are
  already there.
- **`jig validate` scope shift.** Current form removes a ticket's
  worktree. That's useful and shouldn't vanish. Decision: promote
  catalog dry-run to `jig validate` (no args); move per-ticket cleanup
  to `jig validate --ticket-id` (same flag as today) — mapping is
  "with `--ticket-id` → current behavior, without → new catalog
  check". Existing scripts using `--ticket-id` keep working.
- **`issue://` deprecation.** Shipped defaults use it. Migrate those
  in Task G, keep the alias one cycle, then drop. Risk is projects
  that already customized role YAMLs with `issue://` — they'll get a
  warning and keep working until the alias is removed.
- **Circular role/workflow references.** Doc 17 lists "circular
  template references" among load errors, with a footnote "when
  extends/merge lands." Full override means no cycles are possible
  today; skip this check until extends/merge arrives (deferred in
  doc 17 §Deliberately deferred).

## Phase 3 — Structured specs and ownership

### Goal

Turn ticket specs into first-class structured artifacts keyed by
`work_type` (doc 03), and wire the ownership map from Phase 1C into
actual proposal routing (doc 04). This phase lands the foundations
that asymmetric validation (doc 10 / Phase 5) and the full thread
model (doc 08 / Phase 4) build on.

Concretely:

- **Work-type schemas** (`jig/defaults/work_types/*.yaml`) declare
  required vs optional spec fields, a `required_by_size` mapping,
  and a section-level ownership map. Projects override under
  `.jig/work_types/`.
- **Ticket specs** live as structured YAML per ticket. They're
  schema-validated at write and at load (extending Phase 2F's
  `validate_catalog`). The `ticket://spec.<section>` URI scheme
  resolves against them.
- **Proposal** becomes the first typed thread entry. Carries
  structured fields (target, section, change, rationale, owners,
  state) alongside its prose. The remaining ten entry types stay
  untyped comments until Phase 4.
- **Owner resolution** reads the ownership map and each role's
  assignment (human / human_with_helper / agent) to determine where
  a proposal routes. Phase 3 just records the routing decision;
  spawning helper agents is deferred to Phase 5.
- **Self-certification guard** makes it structurally impossible for
  the same actor to both propose and resolve a proposal without
  being marked as self-approving in the audit.

Out of scope (explicit):

- Project-level spec (`.jig/spec/project.md` + `.structured.yaml`)
  with capability tree. Doc 02 is its own sizeable chunk of work;
  lives in a later phase once the ticket-spec plumbing has bedded
  in.
- Full thread typing (objection/resolution/waiver/handoff/etc.) —
  Phase 4.
- Helper-agent spawning for human_with_helper roles — Phase 5.
- Asymmetric validation agent that derives tests from the spec —
  Phase 5.
- Capability-policy enforcement (hook compilation, bwrap path
  locking) — Phase 5.
- Schema DSL for custom validation rules beyond required/optional
  and `required_by_size`.

### Tasks

**A. Work-type schema loader**

New `jig/work_types.py` paralleling `jig/checks.py`. Parses and
validates per-work-type schema YAML. Schema lists required and
optional fields, a `required_by_size` map (size → list of fields
mandatory for that size), and a `ownership` map (field → owner role
alias such as `po`/`sa`).

- [x] Pydantic model `WorkTypeSchema` with:
  - `work_type: str` (matches one of `WorkType` enum values —
    projects may add new enum values in a later phase; for Phase 3,
    schemas for unknown work_types are an error)
  - `required: list[str]`
  - `optional: list[str]`
  - `required_by_size: dict[str, list[str]]`
  - `ownership: dict[str, str]`  # field → owner role alias
  - `section_locks: dict[str, str] = {}`  # parsed but unused; Phase
    5 honors `locked_after_phase`
- [x] `load_work_type_schema(project_path, name)` with the same
      project-override → shipped-default fallback Phase 2C wired up
      for roles/workflows. Shipped defaults under
      `jig/defaults/work_types/`; project overrides under
      `.jig/work_types/`.
- [x] `list_work_type_schemas` for the load-time validator.
- [x] `jig init` drops an empty `.jig/work_types/` directory (no
      copies — shipped defaults serve via fallback, per Phase 2C).

**B. Shipped work-type schemas**

Write the schemas for the seven shipped work types per doc 03 §Work
types. Keep them deliberately small — Phase 3 verifies the plumbing
works, not that every field a team might want is pre-listed.

- [x] `jig/defaults/work_types/feature.yaml`: `required:
      [summary, behaviors, acceptance_criteria, out_of_scope]`,
      `optional: [edge_cases, design, technical_risks, dependencies]`,
      `required_by_size` xs→[summary], s→[summary,behaviors],
      m→required, l→required + design, xl→required + design +
      technical_risks. Ownership per doc 03 §Structured content.
- [x] `bugfix.yaml`: required `[summary, symptom, fix_approach,
      regression_test]`, sized down for xs→[summary, fix_approach],
      up for l→+ `impact_analysis`.
- [x] `refactor.yaml`, `spike.yaml`, `perf.yaml`, `migration.yaml`,
      `docs.yaml` — shapes from doc 03.
- [x] Spike schemas may have no `behaviors` section; docs schemas
      likewise skip design/technical_risks. Schemas express that by
      simply not listing those fields as required/optional.

**C. Ticket spec model + storage**

Ticket specs are structured YAML files, one per ticket, stored at
`.jig/specs/<ticket-id>.yaml`. Using per-file YAML (rather than a
JSONL store) gives humans something diffable and auditable in
review; matches how shipped checks.yaml and config.yaml work.

- [x] Pydantic `TicketSpec` model: loose envelope with
  - `ticket_id: str`
  - `work_type: WorkType` (snapshot — doc 03 §Immutability)
  - `size: Size` (snapshot)
  - `fields: dict[str, Any]` — free-form structured content keyed by
    field name. Shape is validated against the work-type schema at
    write time, not at model-construction time.
  - `version: int` — bump on every write; proposals reference the
    version they target.
  - `created_at: datetime`, `updated_at: datetime`.
- [x] `load_ticket_spec(project_path, ticket_id) -> TicketSpec |
      None`.
- [x] `save_ticket_spec(project_path, spec)` — writes YAML, bumps
      `version`, touches `updated_at`, validates against the schema
      before writing. Raises `SpecValidationError` listing missing
      required fields or unknown fields.
- [x] `delete_ticket_spec(project_path, ticket_id)` — for test
      cleanup and ticket close → archive handoff (archive itself is
      Phase 4-ish; Phase 3 just needs to allow removal).

**D. Spec URI resolution**

Wire `ticket://spec.<section>` into the context resolver so agents
can reference spec sections in their role templates.

- [x] Extend `_resolve_ticket` in `jig/context_resolver.py` to
      handle `ticket://spec`, `ticket://spec.behaviors`, etc. Unknown
      `.<section>` segments log a warning and return empty (same
      semantics as `ticket://design`).
- [x] The resolver reads the spec via `load_ticket_spec`. If no
      spec exists the URI returns `None` (unresolved — `strict=True`
      callers raise `MissingContextError` per Phase 2B).
- [x] Format: render the requested section as
      `### <Section Name>\n\n<YAML block>\n` so the agent sees the
      structured content directly rather than a prose paraphrase.
      Full spec (`ticket://spec`) renders every field.

**E. Proposal thread entry type**

Extend the current `Comment` model with a proposal kind and a
structured payload. Full thread typing is Phase 4; we take the
minimum Phase 3 needs.

- [x] Add `"proposal"` to `Comment.kind`'s Literal.
- [x] Add optional payload fields on `Comment`:
  - `proposal_target: str | None` — e.g.,
    `ticket://spec.behaviors`, or a project-level artifact path.
  - `proposal_section: str | None` — optional sub-section.
  - `proposal_change: str | None` — the concrete change (YAML
    fragment or prose block).
  - `proposal_state: Literal["pending","accepted","rejected",
    "refining"] | None`.
  - `proposal_parent_id: str | None` — resolver entries reference
    the originating proposal.
  - `proposal_owners: list[str] = []` — computed at routing time
    (Task F); stored so later queries don't re-derive.
- [x] Thread ordering remains chronological for now. Phase 4
      rethinks it.

**F. Owner resolution and proposal routing**

New `jig/ownership.py`. Given a proposal's target, compute the
owner(s) and (if staffed) the concrete assignee list.

- [x] `resolve_owner(config, target: str, section: str | None) ->
      OwnerRouting` returning:
  - `role: str` — `"po"` / `"sa"` / other declared role
  - `assignee: str | None` — from `config.roles.<role>.human` when
    assignment is `human` or `human_with_helper`; `None` for `agent`
    (no staffing decision made yet in Phase 3)
  - `helper_template: str | None` — from the same role assignment
  - `assignment: Literal["human","human_with_helper","agent",
    "unstaffed"]` — `"unstaffed"` when the role has no assignment in
    config (orphaned per doc 04).
- [x] Resolution order:
  1. If target is `ticket://spec.<field>`, look up
     `config.ownership.spec.<field>`; fall back to the whole-spec
     owner if the field isn't listed.
  2. For project-level artifacts (`project://architecture`, etc.),
     look up `config.ownership.<key>` directly.
  3. Unknown target → `CatalogError` at load / routing time.
- [x] Proposal creation (via MCP, Task G) records
      `proposal_owners` from the routing result so downstream
      queries stay cheap.

**G. Self-certification guard + MCP tools**

New MCP tools for agents to participate in the proposal mechanism,
plus the structural guard.

- [x] `propose_change(target, change, rationale, section=None)` —
      creates a `Comment(kind="proposal")` on the ticket the agent
      is scoped to. Populates `proposal_owners` via Task F.
- [x] `resolve_proposal(proposal_id, verdict, reasoning)` —
      verdict ∈ `{accept, reject, refine}`. Creates a second
      proposal entry referencing the first. If
      `comment.author == proposal_comment.author`, either:
  - If `config.self_approval == "blocked"`, raise / refuse.
  - If `config.self_approval == "warn"` (default), emit a
    `status_change` with `"self_approval_with_justification: true"`
    and require `reasoning` to be non-empty.
- [x] `list_proposals(ticket_id=None, state=None, target=None)` —
      query helper for humans + TUI later.
- [x] Add `self_approval` field to `Config` (default `"warn"`);
      surface through `.jig/config.yaml`.
- [x] Accepted proposals that target `ticket://spec.<field>` apply
      the change via `save_ticket_spec`. For Phase 3 the change
      payload is treated as opaque YAML the accepter hand-merged —
      automated merge on accept is deferred. Record the new spec
      `version` on the acceptance entry.

**H. Load-time validation + CLI + tests**

Integrate with Phase 2F's `validate_catalog` and add the usual test
surface.

- [x] Extend `validate_catalog` to:
  - Load each project + shipped work-type schema; surface
    validation errors.
  - Cross-check ownership map fields against real work-type
    schemas (unknown field in `ownership.spec.<field>` is an
    error).
  - Verify `config.roles.po` / `config.roles.sa` assignments
    reference role names that exist in the role catalog when
    `assignment == "human_with_helper"` (the `helper_template` is
    a role name).
- [x] Unit tests per task (work-type schema loader, ticket spec
      load/save, URI resolver for `ticket://spec.*`, proposal comment
      roundtrip, owner resolution, self-cert guard both modes).
- [x] End-to-end test: create ticket → write spec → propose a
      change → second actor accepts → spec version bumps.

### Exit criteria

- A ticket can carry a YAML spec at `.jig/specs/<ticket-id>.yaml`
  that validates against its work-type schema (or fails loud with
  a readable error).
- `ticket://spec.behaviors` (and friends) resolve through the
  context resolver.
- `jig propose_change` / `resolve_proposal` MCP tools are
  registered and work end-to-end for at least the spec target.
- A proposal's owner(s) are determined via the ownership map and
  recorded on the proposal.
- A second actor (distinct from the proposer) must resolve a
  proposal, or the resolution is marked self-approving per the
  configured `self_approval` mode.
- `jig validate` fails loud on: unknown work-type schema, unknown
  ownership field reference, missing required spec field at the
  declared size.

### Explicitly deferred out of Phase 3

- **Project-level spec.** `.jig/spec/project.md` +
  `project.structured.yaml` with the capability tree, state
  transitions, and spec-agent synchronization. Needs its own
  sizeable implementation pass; owning a later phase.
- **Spec agent role itself.** The translation/consistency agent
  from doc 02. Blocked on project-level spec.
- **All non-proposal thread entry types.** Question/answer are
  partial today; objection/resolution/waiver/handoff/escalation/
  uncertain/note land in Phase 4.
- **Section locks** (`locked_after_phase`). Parsed by the schema
  loader, enforced in Phase 5.
- **Automated merge on proposal-accept.** The acceptor provides
  the merged YAML (or the proposed change is applied verbatim for
  trivial fields). Richer merge semantics — text diffing, conflict
  detection — stay out.
- **Helper-agent spawning for human_with_helper roles.** Owner
  resolution reports the `helper_template` name; Phase 5 actually
  spawns the helper before the human sees the proposal.
- **Refinement loop state machine.** Phase 3 recognizes
  `"refining"` as a state but doesn't model multi-round
  back-and-forth beyond chronological comments.
- **Cross-ticket proposal routing.** Proposals against the project
  spec (and other project-level artifacts) wait on the project
  spec landing.

### Risks and decisions

- **Spec storage location.** Per-ticket YAML under `.jig/specs/`
  vs. as a field on the Ticket JSONL record. Going with separate
  files: humans read them during review, they diff cleanly, and
  they're naturally archivable on ticket close. Cost: one extra
  file per ticket and a separate load path.
- **Schema ownership enum vs string.** Doc 04 names PO and SA as
  the two built-in owner roles but also says projects add others.
  We type ownership values as `str` (not an enum) so projects can
  declare `security`, `qa`, etc. without touching code.
  Load-time validation checks the value against `config.roles`.
- **Proposal payload on Comment vs. new ThreadEntry.** Phase 4
  plans a proper typed thread entry set; extending `Comment` now
  means Phase 4 has migration work. We accept the migration cost
  — having a half-typed Phase 3 and a full-typed Phase 4 is
  clearer than deferring all thread structure to Phase 4.
- **Self-approval policy.** Shipped default is `warn`, not
  `blocked`, because solo-dev projects genuinely need the escape
  hatch (doc 04 §Scaling down). Teams that want enforcement flip
  the config flag.
- **WorkType schemas as code vs. YAML.** Putting shipped schemas
  in YAML under `jig/defaults/work_types/` (not Python code) keeps
  them tweakable via project override with the same mechanism
  Phase 2C uses for roles/workflows.
- **Content of accepted change.** Without automated merge, the
  proposal `change` field is effectively advisory: the accepter is
  responsible for producing the new spec content. Acceptable for
  Phase 3 because agents that propose also know how to write the
  new YAML, and the guard keeps a human in the loop. Revisit if
  this becomes a sharp edge.

## Phase 4 — Threads & checkpoints

### Goal

Replace the envelope-on-Comment approach with first-class typed thread
entries (doc 08) and stand up the checkpoint channel (doc 09). This
phase makes "unresolved blocking entry → phase can't advance" a real
gate, enforces resolution asymmetry (asker/objector — not
answerer/fixer — closes the entry), and gives agents the scope-
discipline outlet (`checkpoint_deferred`) that doc 09 leans on.

Concretely:

- **Typed thread entries.** The remaining ten types from doc 08 —
  Question, Answer, Objection, Resolution, Waiver, Decision, Handoff,
  Escalation, Uncertain, Note — become proper payload-carrying
  records alongside Proposal (Phase 3E). The underlying store stays
  JSONL; the discrimination lives on the pydantic side.
- **Gating state machine.** Each entry type has a resolved/unresolved
  bit and an `is_blocking` predicate. The workflow/dispatch layer
  consults both before advancing a phase.
- **Resolution asymmetry.** `resolve_*` tools refuse to close an
  entry unless the actor matches the entry's resolver role (asker
  closes a Question; objector closes an Objection; waiver authority
  is check-guarded).
- **Checkpoint channel.** `.jig/store/checkpoints.jsonl` separate
  from the thread store, with its own model and MCP tools
  (`checkpoint_milestone`, `checkpoint_decision`,
  `checkpoint_deferred`). Harness-triggered checkpoints (after
  commit, after test, before handoff) land alongside agent-triggered
  ones.
- **Handoff + deferred-item review.** Handoff entries carry the
  deferred-item list assembled from the phase's checkpoints.
  Evaluator review happens through the existing phase-transition
  path; accepted handoffs advance the workflow, rejected ones loop
  back per the workflow's on-failure edge.

Out of scope (explicit):

- **Compaction** of checkpoints — the deferred-for-later item in
  doc 09. Phase 4 captures every checkpoint; Phase 5-or-later lands
  the summary-rollup agent/task once token pressure is real.
- **Orchestrator-as-resolver-of-last-resort.** Deadlock handling
  per doc 08 §Deadlock handling. The gating layer surfaces the
  blocker; auto-nudging and auto-reassignment land with workflow
  escalation in Phase 5.
- **Deferred-item-to-ticket promotion UX.** Doc 09's promotion
  flow. Evaluator marks a deferred item as "promote" via a Handoff
  field; the automatic ticket-creation plumbing waits until
  workflow-layer changes in Phase 5.
- **PR-comment channel.** Doc 08 leaves `output_channel:
  thread | pr_comments | both` as a workflow knob. Phase 4 wires
  the `thread` half only; `pr_comments` is Phase 6 (SCM).
- **Cross-workflow escalation_targets enforcement.** The workflow
  model declares legal targets with reasons; enforcing that at
  post-time is Phase 5 policy work.

### Tasks

**A. Typed thread-entry model**

New `jig/thread.py`. Replaces the proposal-specific extension of
`Comment` (Phase 3E) with a typed discriminated union of ten
entries plus the existing Proposal.

- [x] Pydantic models per doc 08, one per type. Shared envelope
      (`id`, `ticket_id`, `author`, `created_at`, `kind`), per-type
      payload:
  - `Question`: `target: str`, `question: str`,
    `blocking: bool = False`, `resolved_by: str | None`.
  - `Answer`: `question_id: str`, `text: str`.
  - `Objection`: `target_artifact: str`, `text: str`,
    `resolved_by: str | None`, `waived_by: str | None`.
  - `Resolution`: `objection_id: str`, `text: str`.
  - `Waiver`: `objection_id: str`, `justification: str`.
  - `Decision`: `decision: str`, `rationale: str`.
  - `Handoff`: `outputs: list[str]`, `summary: str`,
    `deferred_items: list[DeferredItem]`, `phase: str`,
    `acceptance_state: Literal["pending","accepted","rejected"]`.
  - `Escalation`: `reason: str`, `details: str`, `target: str`.
  - `Uncertain`: `details: str`.
  - `Note`: `text: str`.
  - `Proposal` — migrate from `Comment` (Phase 3E) keeping the
    same fields.
- [x] Discriminator lives on `kind`; a top-level `ThreadEntry =
      Annotated[Union[Question | ... | Note], Field(discriminator=
      "kind")]` for the store layer.
- [x] `ThreadEntry.is_blocking() -> bool` per type. Default
      unblocking; Question respects its own `blocking` bit;
      Objection, Escalation, and un-accepted Handoff are blocking.
- [x] `ThreadEntry.is_resolved() -> bool` per type. Decision /
      Note / Answer / Waiver / Resolution auto-resolved; Question
      resolves when `resolved_by` is set; Objection resolves when
      `resolved_by` or `waived_by` set; Handoff resolves when
      `acceptance_state != "pending"`.

**B. Thread-entry store + Comment migration**

Migrate `jig/store/comments.py` to a thread-entry store. The doc-09
checkpoint channel ships in Task G; thread entries stay on the
existing JSONL file with a one-time record migration for the
proposal entries Phase 3 wrote.

- [x] Introduce `ThreadStore` alongside `CommentStore` on the same
      JSONL file (`.jig/store/comments.jsonl`). Renaming the legacy
      store would churn every Phase 3 MCP handler in one pass; we
      defer the rename + CommentStore removal to Task H so Tasks C–G
      can migrate handlers one channel at a time.
- [x] `ThreadStore.post(entry: ThreadEntry)` — same append-JSONL
      semantics; validates via the discriminated union.
- [x] `ThreadStore.for_ticket`, `update` (field-level patch for
      close-by-asker / accept-handoff), `has_unresolved_blocking`,
      `find_by_kind(ticket_id, kind)` — enough query surface to
      drive the gating check without re-scanning every load.
- [x] `ThreadStore.has_unresolved_blocking(ticket_id) -> list[
      ThreadEntry]` — single helper the dispatch layer calls to
      decide whether the current phase can advance.
- [x] Migration on read: old `Comment(kind="comment"|"commit"|
      "phase_run"|"status_change"|"decision"|"question"|"answer"|
      "proposal")` records load as the new typed entries. The three
      system-primitive kinds fold into a shared `SystemEvent` subtype
      rather than becoming first-class thread entries — they're
      audit trail, not conversations. Idempotent: already-new-shape
      records pass through. Bad records fail loud via pydantic's
      discriminated-union validator.
- [x] Route remaining `CommentStore` imports through
      `ThreadStore` and drop the legacy module. Landed in
      `5f0cfd4`; both the `Comment` model and `CommentStore` class
      are gone; the on-disk file (`.jig/store/comments.jsonl`) stays
      under its legacy name so historical JSONL continues to load via
      `ThreadStore._migrate_legacy`.

**C. Question / Answer tools and gating**

Agent-facing MCP tools per doc 08 §Agent tools. Replaces the
ad-hoc `handle_ask_question` / `handle_answer_questions` flow in
`jig/ticket_mcp.py` (which pre-dates the typed model).

- [x] `thread_ask(ticket_id, target, question, blocking=False)` —
      creates a `Question` entry and publishes a typed bus event
      (`QUESTION`) so subscribers react in real time. Target
      validation against the phase's `escalation_targets` /
      `questions_to` lists is deferred to Phase 5 per the plan
      note — no current workflow declares them.
- [x] `thread_answer(question_id, text)` — creates an `Answer` and
      pings the asker via an `ANSWER` bus event. Does not resolve
      the Question; fails loud if the target is already resolved or
      isn't a question.
- [x] `thread_resolve_question(question_id, accepted_answer_id=
      None, reason=None)` — asker-only close. Refuses if
      `sender != question.author`. Optionally records
      `accepted_answer_id` after validating it points back at the
      question.
- [x] Retire `handle_ask_question` / `handle_answer_questions` in
      `ticket_mcp.py`. The operator-pause UX now lives at its
      surfaces: `ask_question` (MCP tool) inlines the `needs_info`
      transition in `mcp_server.py`; `answer_questions` (WebSocket
      command) inlines the resume flow in `ws_server.py`. Agent-to-
      agent Q&A continues to use the typed `thread_ask` /
      `thread_answer` / `thread_resolve_question` tools.

**D. Objection / Resolution / Waiver**

- [x] `thread_object(ticket_id, target_artifact, text)` — creates
      an `Objection`. Always blocking.
- [x] `thread_resolve_objection(objection_id, text)` — posts a
      `Resolution` entry. Does NOT mark the Objection resolved —
      that requires the objector to confirm. Fails if the
      Objection is already resolved or waived.
- [x] `thread_accept_resolution(objection_id)` — objector-only
      close. Sets `resolved_by=sender` on the Objection. Non-
      objector actors get a clear error.
- [x] `thread_waive(objection_id, justification)` — creates a
      `Waiver` entry and flips the Objection to waived-with-
      reason. Authorization check: doc 08 says "authorized actors
      only"; Phase 4 reads `config.waiver_authority: list[str]`
      (roles allowed to waive; default `["po", "sa", "user"]`).
      Full policy enforcement lands Phase 5.
- [x] Both the Objection and its Waiver stay in the thread; the
      audit trail is the point.

**E. Decision / Note / Uncertain / Escalation**

Lower-gating-weight entries. Uncertain has a routing side effect.

- [x] `thread_decide(ticket_id, decision, rationale)` — creates a
      `Decision`. Auto-resolved. Also writes a standalone decision
      record under `.jig/decisions/<ticket-id>-<seq>.md` per doc 17
      (the per-ticket sequence is file-scoped; collisions take the
      higher `created_at`).
- [x] `thread_note(ticket_id, text)` — creates a `Note`. Auto-
      resolved.
- [x] `thread_escalate(ticket_id, reason, details, target="human")`
      — creates an `Escalation`. Always blocking. Target validation
      against the phase's `escalation_targets` is best-effort in
      Phase 4: log a warning but don't refuse.
- [x] `thread_uncertain(ticket_id, details)` — creates an
      `Uncertain`. Phase 4 routes inline in the handler: if
      `details` mentions a known role name as a whole word, we
      reshape the uncertain as a non-blocking Question targeting
      that role; otherwise we emit a blocking Escalation targeting
      `human`. Orchestrator-level subscription + smarter routing is
      Phase 5.

**F. Handoff entry + phase-completion hook**

- [x] `thread_handoff(ticket_id, outputs, summary, deferred_items=
      [])` — creates a `Handoff` entry in `acceptance_state=
      "pending"`. Outputs are artifact references; deferred_items
      is the list gathered from checkpoints (Task G) for the phase
      the handoff closes.
- [x] `thread_accept_handoff(handoff_id)` / `thread_reject_handoff
      (handoff_id, reason)` — evaluator-only. Evaluator resolves
      via explicit `PhaseConfig.evaluator` → next phase's role →
      warn-but-allow when neither is available (capability-policy
      enforcement lands Phase 5).
- [x] On accept: publish a `thread_handoff_accepted` message on
      `tickets.<id>` (+ `orchestrator`) so the orchestrator
      advances the workflow.
- [x] On reject: publish a `thread_handoff_rejected` message
      with the rejection reason; orchestrator follows the phase's
      on-failure edge.
- [ ] Retire the existing `phase_result` field on `Comment`
      (Phase 1-ish) — Handoff is the canonical phase-completion
      record now. Legacy `phase_run` comments remain readable
      (system-event subtype).

**G. Checkpoint channel**

New `jig/store/checkpoints.py` + `jig/checkpoints.py` for the
models and MCP handlers. Separate from thread per doc 09.

- [x] Pydantic `Checkpoint` model:
  - `id`, `ticket_id`, `phase: str`, `author`, `created_at`.
  - `completed: list[str]` — recent concrete completions.
  - `position: str` — current state.
  - `plan: str` — next intended step.
  - `ruled_out: list[RuledOut]` — `approach: str, reason: str`.
  - `deferred: list[DeferredItem]` — `item: str, reason: str,
    status: Literal["open","done","promoted","accepted"] =
    "open"`.
  - `open_questions: list[str]` — pre-thread-Question scoping.
  - `trigger: Literal["auto_commit","auto_test","auto_pre_handoff",
    "agent_milestone","agent_decision","agent_deferred"]`.
- [x] `CheckpointStore` with append-JSONL semantics mirroring
      `ThreadStore`. Queries: `for_ticket`, `for_phase(ticket_id,
      phase)`, `latest(ticket_id)`, `deferred_items_open(ticket_id,
      phase)`.
- [x] Harness-triggered checkpoints fire from three hooks:
  - `worktree.py` commit path (after the commit succeeds).
  - `worktree.py` lint/test path (after run, regardless of
    result).
  - `thread_handoff` (write a `auto_pre_handoff` checkpoint first).
- [x] Agent MCP tools:
  - `checkpoint_milestone(description, position, plan,
    completed=[], ruled_out=[])`.
  - `checkpoint_decision(decision_id, rationale)` — references a
    thread `Decision`; checkpoint mirrors its context.
  - `checkpoint_deferred(item, reason)` — single-item append;
    becomes a `DeferredItem` on the ticket's current phase.
- [x] Phase-boundary pruning: when a phase completes successfully
      (Handoff accepted), mark that phase's checkpoints
      `historical=True`. Historical checkpoints stay on disk for
      audit but are skipped by `latest`/`for_phase` queries by
      default. Phase-failure retries skip the prior attempt's
      checkpoints the same way (resumption reads only the current
      attempt).

**H. Gating + load-time validation + tests**

- [x] `orchestrator.py` dispatch consults
      `ThreadStore.has_unresolved_blocking(ticket_id)` before
      advancing a phase. Blocked advancement surfaces to the TUI
      via the existing `orchestrator` topic as a
      `phase_blocked_by_thread` event.
- [x] `validate_catalog` extensions:
  - Workflow phases that declare `questions_to` or
    `escalation_targets` reference known role names.
  - `config.waiver_authority` references known roles.
- [x] Unit tests per task. Minimum surface:
  - thread_ask/answer/resolve_question roundtrip + asker-only
    close.
  - Objection blocks handoff acceptance; Resolution is inert
    until objector accepts.
  - Waiver closes an Objection and preserves audit.
  - Unauthorized waiver (role not in `waiver_authority`) refused.
  - Uncertain → Question routing (role in details) and → Escalation
    (no role match).
  - Handoff accept/reject publishes the expected bus message.
  - Checkpoint auto-triggers fire on commit and test events.
  - `checkpoint_deferred` items surface on the next Handoff.
  - Phase-boundary pruning: prior-phase checkpoints excluded by
    default queries.
- [x] End-to-end test: ticket with blocking Objection on phase 1
      → thread_handoff refused until thread_accept_resolution →
      handoff accepted → orchestrator advances to phase 2 →
      deferred item from phase 1 surfaces in the evaluator's view.

### Exit criteria

- All eleven thread entry types from doc 08 have typed models,
  MCP tools, and resolution semantics matching the doc.
- Resolution asymmetry is mechanically enforced: a non-asker
  cannot close a Question; a non-objector cannot close an
  Objection; an unauthorized role cannot Waive.
- The orchestrator refuses to advance a phase with an open
  blocking entry and surfaces which entry is blocking.
- Checkpoints land for every harness-triggered event plus
  agent-triggered milestones/deferrals.
- A Handoff carries the phase's deferred items and the evaluator
  sees them at accept/reject time.
- `jig validate` fails loud on unknown roles referenced by
  `questions_to`, `escalation_targets`, or `waiver_authority`.
- Phase 3's proposal entries continue to roundtrip through the
  new typed thread store (migration is read-compatible).

### Explicitly deferred out of Phase 4

- **Checkpoint compaction.** Doc 09 §Compaction. Token pressure
  isn't real yet; capture everything, compact later.
- **Deadlock auto-resolution.** Orchestrator-as-resolver-of-
  last-resort per doc 08. Phase 4 surfaces the blocker; Phase 5
  auto-nudges / auto-escalates on timeouts.
- **Deferred-item → ticket promotion.** The plumbing to create a
  new ticket from a promoted deferred item. Phase 4 records the
  `status="promoted"` intent; ticket creation is Phase 5.
- **PR-comment output channel.** `output_channel: pr_comments |
  both` for review phases. Phase 6 (SCM integration).
- **Capability-policy enforcement for thread targets.** Workflow-
  declared `questions_to`/`escalation_targets` are validated at
  load; enforcement at post-time waits for Phase 5's
  capability-policy layer.
- **Waiver authority as a capability.** Phase 4 reads a flat list
  from config; Phase 5 ties it to the capability-policy model.
- **Harness-capabilities meta-tool.** Doc 08 mentions
  `harness_capabilities()` for agent introspection. Nice-to-have;
  not blocking.
- **Thread summarization / compaction-entry type.** Noted in doc
  08 §Thread as context for later spawns. Tied to checkpoint
  compaction; same deferral window.

### Risks and decisions

- **Discriminated union vs envelope + extras.** Phase 3E extended
  the `Comment` envelope to ship the Proposal payload without
  holding up the rest of Phase 3. Phase 4 pays that debt: each
  type gets its own pydantic model so the store can enforce
  payload shape at write time. Cost: one-shot migration of
  existing JSONL records. Mitigated by keeping legacy system-
  primitive kinds (`commit`, `phase_run`, `status_change`) as a
  single `SystemEvent` subtype rather than splitting them further.
- **Renaming `CommentStore` → `ThreadStore`.** Touches every MCP
  handler. Doing it in Phase 4 rather than coexisting with
  `CommentStore` forever. Alias + deprecation warning through
  the commit; delete on the Phase 4 boundary.
- **Resolution asymmetry author check.** Easiest check is
  `sender == entry.author`. That works for Question (asker
  closes) but Objection's "objector closes" is the same rule
  since the objector *is* the author. If future work splits
  author from resolver (e.g., a human PO inherits an agent's
  Objection), revisit.
- **Waiver authority via config.** A flat `waiver_authority:
  list[str]` is enough for Phase 4. Doc 16's capability-policy
  model supersedes this in Phase 5 — intentional stepping-stone.
- **Checkpoint hooks coupled to worktree.py.** The auto-commit
  and auto-test hooks live where the actions happen today.
  When Phase 5 adds a proper check-execution layer, move the
  hooks to the event source there. Not worth factoring the hook
  interface out ahead of time.
- **Uncertain routing heuristic.** Role-name-in-details is
  embarrassingly simple. It's chosen to keep Phase 4 out of the
  orchestrator routing rules; a better router comes with Phase
  5 policy. If the heuristic fires too often wrongly, upgrade
  early.

## Phase 5 — Verification & policy

*Detailed plan added after Phase 4 review.*

## Phase 6 — SCM integration

*Detailed plan added after Phase 5 review.*

## Phase 7 — Service & TUI alignment

*Detailed plan added after Phase 6 review.*
