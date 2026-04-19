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

- [ ] Add scheme dispatcher in `context_resolver.py` keyed on URI
      prefix; return resolver function per scheme
- [ ] `project://<path>` → read `.jig/context/project/<path>`. If
      no extension, try `.md` then raw
- [ ] `role://<role>/<path>` → read `.jig/context/roles/<role>/<path>`
- [ ] `ticket://description` → current `issue://description` behavior
- [ ] `ticket://design` → current `issue://design` behavior (decision
      comments + `docs/design*` etc. in worktree)
- [ ] `ticket://plan` → current `issue://plan` behavior
- [ ] `ticket://thread` → concatenate comments for the ticket in
      chronological order (stub for Phase 4 thread work; reads from
      `CommentStore` today)
- [ ] `decision://<id>` → read `.jig/decisions/<id>.md`
- [ ] `repo://<path>` → read `<worktree>/<path>` (escape hatch)
- [ ] Keep `issue://` as a transitional alias mapping to `ticket://`,
      logging a deprecation warning once per process

**B. Required vs optional references**

Doc 07 §Required vs. optional. Today every URI is silently skipped if
unresolved — no distinction between "critical" and "nice to have".

- [ ] Extend `RoleConfig` with `required_context: list[str]` alongside
      the existing `default_context` (kept as optional). Or adopt a
      structured form (`{uri: str, required: bool}`) — pick one, document
      choice in commit message
- [ ] At spawn, fail (raise → surfaces as `ticket_failed`) if any
      required URI fails to resolve; warn on optional failures
- [ ] Load-time validation walks every role template and asserts
      required URIs would resolve against the project layout (project://
      and role:// only — ticket:// / repo:// are per-spawn)

**C. Catalog resolution order**

Doc 17 §Resolution order: project repo first, shipped default second.
Today `load_role` / `load_workflow` only check the project repo —
defaults are copied in at `jig init`.

- [ ] Rewrite `load_role(name)` / `load_workflow(name)` to:
  1. try `<project>/.jig/<kind>/<name>.yaml`; if present, use it
  2. else try `jig/defaults/<kind>/<name>.yaml`; if present, use it
  3. else raise `FileNotFoundError` with both searched paths
- [ ] Add `list_roles` / `list_workflows` variants that merge the two
      layers (project overrides default, dedup by name)
- [ ] Stop copying shipped defaults into `.jig/roles/` and
      `.jig/workflows/` at `jig init`. `save_default_roles` /
      `save_default_workflow` deprecated in the hot path; kept as a
      `jig role init <name>` scaffolding command for teams that want
      to customize one
- [ ] Migration: existing projects already have full copies under
      `.jig/`; they continue to resolve via step 1 untouched

**D. Workflow resolution from config**

Promote Phase 1's parsed-but-unused `workflows:` section to drive
actual behavior at ticket creation.

- [ ] Add `resolve_workflow(config, *, work_type, size, explicit=None)
      -> str` in `jig/config.py`:
  - explicit override wins if provided and is in `available`
  - else `workflows.by_type.<work_type>.default_by_size.<size>`
  - else `workflows.by_type.<work_type>.available[0]` if single-item
  - else top-level `workflows.default_by_size.<size>`
  - else fall back to `"default"` (preserves current behavior)
- [ ] Wire into `ticket_mcp.create_ticket`: if caller didn't specify
      `workflow`, resolve one. Persist the resolved name on the ticket
      (already persisted — no schema change)
- [ ] Validate that resolved workflow is in `workflows.available` (if
      populated); reject with explicit error otherwise
- [ ] Phase 1 warnings upgrade to errors at load (Task F)

**E. Check catalog loader (shape only; execution is Phase 5)**

- [ ] Add `jig/checks.py` with a `CheckCatalog` pydantic model
      covering all three check types per doc 10 (scripted,
      implementation_aware_agent, black_box_agent)
- [ ] Parse `.jig/checks.yaml` at load; empty catalog is valid
- [ ] Validate per-check required fields (type, command or template,
      severity)
- [ ] Don't execute — just assert shape and collect names for
      cross-reference validation in Task F

**F. Load-time validation (full, fail-loud)**

Upgrade Phase 1F's advisory warnings to hard errors. Add the full
set from doc 17 §Validation at load.

- [ ] New `jig/catalog.py` module exposing
      `validate_catalog(project_path) -> None` that raises
      `CatalogError` on the first failure, with a list-all mode
      `validate_catalog(..., collect=True) -> list[str]`
- [ ] Checks performed:
  - Unknown role names referenced from workflow phases
  - Unknown workflow names in `config.yaml`'s `default_by_size`,
    `available`, `by_type.*.default_by_size`, `by_type.*.available`
  - Unknown check names referenced from workflows (catalog + phase
    checks — stubs for now)
  - Missing required context URIs in role templates
    (`project://`, `role://` only)
  - Malformed YAML (covered by pydantic today; surface cleanly)
- [ ] `jig start` calls `validate_catalog` before any loop starts;
      exit non-zero with readable error on failure
- [ ] `jig validate` (no `--ticket-id`) becomes a catalog dry-run
      that calls the same validator and prints the collect-all list;
      current per-ticket form stays available via explicit flag

**G. Documentation and default-template updates**

- [ ] Migrate shipped role defaults' `default_context` entries from
      `issue://design` etc. to `ticket://design`. Keep one test asserting
      the `issue://` alias still works (Task A)
- [ ] Update doc 07 / doc 17 cross-references in the shipped defaults'
      `phase_prompt` wording where it mentions "issue"/"work unit" →
      "ticket" (doc sweep done in Phase 0; this is a code/yaml sweep)

**H. Tests**

- [ ] Context resolver: one test per scheme, plus the deprecation
      alias path. Required vs optional: missing required raises;
      missing optional warns and continues
- [ ] Catalog resolution order: project override wins, default
      fallback loads, missing both raises with both paths named
- [ ] Workflow resolution: cover explicit override, per-type
      default_by_size, top-level default_by_size, fallback to
      `"default"`, rejection of not-in-`available`
- [ ] Check catalog: empty file OK, missing required field errors,
      shape validation per type
- [ ] `validate_catalog`: exercise each failure mode; collect-all
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

## Phase 3 — Structured specs & ownership

*Detailed plan added after Phase 2 review.*

## Phase 4 — Threads & checkpoints

*Detailed plan added after Phase 3 review.*

## Phase 5 — Verification & policy

*Detailed plan added after Phase 4 review.*

## Phase 6 — SCM integration

*Detailed plan added after Phase 5 review.*

## Phase 7 — Service & TUI alignment

*Detailed plan added after Phase 6 review.*
