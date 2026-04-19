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

- [ ] `.jig/agent_types/` → `.jig/roles/` (project directory)
- [ ] `jig/defaults/agent_types/` → `jig/defaults/roles/` (shipped
      defaults)
- [ ] `AgentTypeConfig` → `RoleConfig` (class), update all imports
- [ ] `agent_type` → `role` in variable/field names where it refers to
      the role template (not the runtime agent instance)
- [ ] `issue_id` → `ticket_id` in `MemoryStore` models (`Handoff`,
      `Learning`) and related JSONL index keys
- [ ] `templates/` (repo root) → `jig/defaults/project_templates/`
      per doc 17
- [ ] Grep for any `.agents/` string literals in code; replace with
      `.jig/` (doc sweep done; code may still have them in path
      constants)

**B. Ticket schema — classification axes**

Current: `Ticket.type: TicketType` with values
`{feature, bug, chore, task, question}`.

Target: `Ticket.work_type: WorkType` with values
`{feature, bugfix, refactor, spike, perf, migration, docs}` (doc 03),
plus `Ticket.size: Size` with values `{xs, s, m, l, xl}`.

- [ ] Add `WorkType` enum per doc 03's shipped set
- [ ] Add `Size` enum (`xs`, `s`, `m`, `l`, `xl`)
- [ ] Rename `Ticket.type` → `Ticket.work_type`, expand enum values
- [ ] Add `Ticket.size` field (default: `m` until creation UI exposes
      it)
- [ ] Migration mapping for existing ticket data:
  - `bug` → `bugfix`
  - `chore` → `refactor` (closest fit)
  - `task` → `refactor` (closest fit; re-tag later)
  - `question` → dropped as work_type; questions live as thread
    entries per doc 08 (Phase 4). For now, migrate to `feature` and
    flag for manual cleanup
- [ ] Keep `TicketType` import alias for one release cycle to soften
      the rename, then drop
- [ ] Update `TicketStore.TOP_LEVEL_TYPES` to match new enum

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

- [ ] Interpolate `PhaseConfig.task_template` in `prompt_builder.py`
      when building the agent prompt (currently stored, never
      expanded)
- [ ] Surface `PhaseConfig.acceptance_criteria` in the agent prompt
      (currently stored, never shown to the agent)
- [ ] Wire `Project.merge_strategy` into the workflow's terminal
      phase so squash vs PR vs direct merge actually does something
      different (minimal; full merge behavior is Phase 6)
- [ ] Drop or rename `AgentTypeConfig.can_message` — it's a stub with
      no enforcement; either delete or document as Phase 5 policy
      input

**F. Load-time validation (partial)**

Full catalog validation per doc 17 §Validation at load arrives in
Phase 2. For Phase 1:

- [ ] Parse `config.yaml`'s `workflows.by_type` section, warn (don't
      fail) on undefined workflow references. Phase 2 upgrades warn
      to fail.
- [ ] Validate that ticket's `work_type` field is a known value;
      reject unknowns at `create_ticket` MCP handler

**G. TUI surfacing**

- [ ] `new-ticket-form.tsx` — add dropdown for `work_type` and `size`
- [ ] `ticket-detail.tsx` — show `work_type` and `size` fields
- [ ] `ticket-list.tsx` / `kanban-view.tsx` — show size as a small
      chip next to the title
- [ ] Hydration path in `use-jig-socket.ts` — include new fields in
      Ticket reconstruction

**H. Tests**

- [ ] Unit tests for new `WorkType` / `Size` enums and validation
- [ ] Migration test: legacy tickets with old `type` values load
      correctly
- [ ] Config loader test: `.jig/config.yaml` parsed correctly;
      fallback to `.jig/project.json` works
- [ ] Catalog structure test: `jig/defaults/roles/` loads all shipped
      role files
- [ ] TUI smoke test: new fields render without crashing (if
      existing TUI test infra supports it; otherwise defer to Phase 2)

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

*Detailed plan added after Phase 1 review.*

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
