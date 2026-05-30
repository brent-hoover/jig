---
title: Actor model — Design
type: design
status: draft
owner: brent
problem: ./problem.md
created: 2026-05-30
updated: 2026-05-30
---

# Actor model — Design

## Summary

Add a thin `Actor` abstraction that classifies any actor reference in jig — agent role or human — into a
uniform `Actor(handle, type, display_name, roles)` via one `resolve_actor(handle, config)` function. Humans are
declared in a new `humans:` section of the existing `.jig/config.yaml` (seeded from git on init, one entry
today, many allowed), each carrying a global capability list `roles: [worker, reviewer, validator]`. Agents
resolve from the existing role catalog. Attribution stays a **bare handle** (`author="dev"` / `author="brent"`)
— no colon namespacing — so the change is additive: existing JSONL stores, the shipped Phase-3 human machinery,
and current tests are untouched. This is sub-project 1 of the [#116](https://github.com/brent-hoover/jig/issues/116)
epic; the dispatch router (SP3) and review/validator gate (SP6) consume the seam it exposes.

## Background / Context

See [problem.md](./problem.md) for the epic framing. **jig already models humans in three shipped places** —
this design builds on them rather than replacing them:

1. **`config.roles.<role>` (`RoleAssignment`, `config.py:75`)** — a project role (po/sa/extras) declares
   `assignment: "" | human | human_with_helper | agent` plus a `human:` identity string and optional
   `helper_template`. `jig/ownership.py` (`resolve_owner`) already reads this to route a proposal target to a
   role and decide "notify a human vs. spawn an agent vs. unstaffed."
2. **`config.escalation.default_human` (`config.py:122`)** — the project's fallback human, target of deadlock
   auto-escalation.
3. **`SpecificHumanEvaluator(user: str)` (`models.py:128`)** — a *named human* can already gate a phase as its
   evaluator. This is the direct prior art for SP6's "validator."

Other relevant current facts:

- **Attribution is a bare role-name string.** MCP handlers record `sender=agent_role` (`"dev"`, `"review"`) into
  `Ticket.created_by`, `ThreadEntry.author`, `ReviewerComment.reviewer`. The operator's `/now` answer is
  hard-coded `author="user"` in `ws_server.py`.
- **`assignee` is store-ready but semantically inert.** `Ticket.assignee: str | None` (`ticket.py:278`) and
  `find_by_assignee` / the `assignee` index (`store/tickets.py`) are wired; nothing dispatches on it.
- **`_check_assignee` is a real validator, not a stub.** `mcp_server.py:138` builds
  `_allowed_assignees = valid_roles | {"orchestrator", "user"}` and rejects anything outside it. This design
  *widens* that set to include known human handles — it does not introduce validation where there was none.
- **`agent_id` uses a colon** (`f"{role}:{ticket_id[:8]}"`) but only as a logging id, never as attribution.
- **`safe_path.py` rejects colons** in segments; bare handles keep actor strings path/topic-safe.
- **`any_human` / `human` escape hatch** already exists for Question targets (`thread_mcp.py`).

## Goals / Non-Goals

**Goals**

- One `resolve_actor(handle, config) -> Actor` seam classifying any handle as human / agent / system, exposing
  its capability `roles`. Used by dispatch (SP3), review/validator (SP6), and assignee validation.
- A `humans:` section in `.jig/config.yaml` (handle, display_name, roles[]), seeded from git on init, one entry
  today, many allowed — co-located with the `roles:`/`escalation:` that already name humans.
- A `resolve_current_actor(config)` seam returning "who is acting now" — the sole human today; later reads
  `JIG_ACTOR`/`--as`/picker without touching callers.
- Widen `assignee` validation to accept agent roles ∪ known human handles ∪ `{orchestrator}`; keep the existing
  loud-rejection behavior.
- Replace the hard-coded `"user"` attribution in `ws_server.py` with the resolved current-actor handle.
- Strictly additive: bare-handle attribution, no migration, shipped Phase-3 human machinery and existing tests
  unaffected.

**Non-Goals**

- **No refactor of `config.roles[].human`, `ownership.py`, or `SpecificHumanEvaluator`.** They stay; the actor
  model reads alongside them. (User: "reuse role-staffing, no new roster"; "don't migrate shipped code.")
- **No colon-namespaced refs** (`human:brent`). Bare handles only. Type is resolved, never encoded.
- **No authentication.** Current actor is selected/trusted, never verified.
- **No selection UX** (env/flag/picker) built now — only the seam.
- **No dispatch or review behavior change here.** SP1 only defines/exposes the abstraction and wires identity +
  validation. SP3 and SP6 consume it.
- **No multi-human UX.** The `humans:` list may hold many entries; nothing renders or selects among them yet.

## Approach

### 1. `Actor` value (`jig/actor.py`)

```python
class ActorType(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"      # the fixed "orchestrator" actor

class ActorRole(str, Enum):
    WORKER = "worker"
    REVIEWER = "reviewer"
    VALIDATOR = "validator"

class Actor(BaseModel):
    handle: str                    # bare, colon-free
    type: ActorType
    display_name: str
    roles: frozenset[ActorRole]    # capabilities this actor may exercise
```

- **Humans:** `roles` comes straight from the `humans:` entry.
- **Agents:** `roles` is *derived* from the role id so we don't hand-maintain a second list. Derivation uses
  explicit, checkable rules against the **actual shipped role ids**, which are all hyphenated (the underscore
  forms like `reviewer_security` are only the YAML *filenames* — the `role:` field inside is hyphenated, and
  `review.yaml`'s id is `reviewer-generalist`):
  - every agent role contributes `WORKER`;
  - `REVIEWER` if `role.startswith("reviewer-")` (covers `reviewer-generalist`, `reviewer-security`,
    `reviewer-architectural`, `reviewer-performance`, `reviewer-error-handling`, `reviewer-pattern-conformance`,
    `reviewer-test-adequacy`). (If a project defines a custom role whose id uses an underscore, that's a
    compatibility consideration for the rule, not a shipped id.)
  - `VALIDATOR` if `role == "validate"` (the shipped validation-phase role; jig's validation workflows set
    `role: validate` and do not configure an evaluator, so validator capability must key off the role id, not a
    "close-phase evaluator"). Human validator capability is separate — it comes from a human's `roles`
    containing `validator`, and SP6 additionally honours `SpecificHumanEvaluator(user)` (see §6).
- **System:** the singleton `orchestrator` actor (handle `"orchestrator"`), `roles=∅`. `"user"`, `"system"`,
  and `"orchestrator"` are **reserved handles** — a `humans:` entry may not claim them (rejected at load).

### 2. `humans:` config section (`jig/config.py`)

```yaml
# .jig/config.yaml
humans:
  - handle: brent
    display_name: Brent Hoover
    roles: [worker, reviewer, validator]
```

```python
class HumanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str                          # ^[a-z0-9][a-z0-9_-]*$  (safe-segment shape)
    display_name: str = ""
    roles: list[ActorRole] = Field(default_factory=lambda: [WORKER, REVIEWER, VALIDATOR])

class Config(BaseModel):
    ...
    humans: list[HumanEntry] = Field(default_factory=list)
```

- **Backward compatible:** `humans` defaults to `[]`, so every existing `config.yaml` still validates. An empty
  list degenerates to a single git-seeded operator at resolution time (see §4), so old projects keep working
  with zero edits.
- **`extra="forbid"` is deliberate and scoped to the entry.** A typo'd key inside a human entry (e.g. `role:`
  instead of `roles:`) should fail loud, not be silently dropped — consistent with `ProfileSection`,
  `OrchestratorSection`, and `PhaseConfig`, which also use `forbid`. The codebase mixes conventions
  (`SpecOwnership`/`OwnershipSection`/`RolesSection` use `extra="allow"` because they intentionally accept
  open-ended extension keys; `Config` itself uses the pydantic default). `forbid` on `HumanEntry` governs **only
  the keys within a single human dict** — it does not, and cannot, reject extension keys at the `Config` level
  or in the `allow` sections, which validate independently. So adding `humans:` does not tighten validation of
  any existing config section.
- **Seeding:** `init_project` writes one `HumanEntry` whose `handle` is slugified from `git config user.name`
  (fallback `user.email` local-part, then `"operator"`), `display_name` from git, all three roles.
- **Handle shape** reuses the safe-segment regex so handles never carry colons or collide with path/topic
  syntax.

### 3. `resolve_actor(handle, config)`

The resolver needs the **role catalog**, which is project-path based (`jig/persistence.py` exposes
`load_role(project_path, name)` and `list_role_names(project_path)`; there is no `role_exists(handle, config)`
and `Config` does not carry role ids). So the resolver takes `project_path` (or a precomputed role-name set)
alongside `config`:

```python
def resolve_actor(handle: str, *, config: Config, project_path: Path) -> Actor:
    if handle in ("orchestrator", "system"):
        return _SYSTEM_ACTOR
    if handle == "user":                          # legacy alias for the operator (also the shipped `user` role)
        return _current_or_seeded_human(config)
    for h in config.humans:                       # humans win ties (collisions rejected at load)
        if h.handle == handle:
            return _human_actor(h)
    if handle in list_role_names(project_path):   # existing project-path-based catalog API
        return _agent_actor(handle, project_path)
    raise UnknownActorError(handle)
```

- **Single flat handle namespace.** A human handle must not equal an agent role name; the collision is rejected
  at config load with a loud error (so resolution is unambiguous and callers branch on `actor.type`, never on
  string parsing). Collision-checking uses `list_role_names(project_path)`, the same catalog API.
- **`"user"` is the operator alias.** jig already ships a `user` *pseudo-role* (`role: user`,
  `work_type: thread`, "operator-authored thread entries attributed to a human"), so intercepting `"user"`
  before catalog lookup and returning the operator human is *coherent* with its existing meaning — not a
  shadowing hack. It also keeps every historical JSONL record (`author="user"`) resolvable with no backfill.

### 4. `resolve_current_actor(config)` — the selection seam

```python
def resolve_current_actor(config: Config) -> Actor:
    # today: the sole human (or git-seeded operator if humans == []).
    # later: JIG_ACTOR env / --as flag / picker, validated against config.humans.
    return _current_or_seeded_human(config)
```

`ws_server.py`'s `/now` answer path stops hard-coding `"user"` and writes
`author=resolve_current_actor(config).handle`. Because the seam exists now, adding selection later touches only
this one function.

### 5. Assignee validation (widen, and validate at the shared layer)

`mcp_server.py:138` currently builds `_allowed_assignees = valid_roles | {"orchestrator", "user"}` and rejects
anything outside it — but **that check only fires on the MCP create/update path.** Tickets are also created and
updated through the WebSocket path (`ws_server.py`) and the `jig` CLI; today those can persist an arbitrary
`assignee`. To avoid leaving invalid actor handles for SP3's dispatcher to choke on, the registry-backed check
must live in the **shared ticket handler** that all three surfaces call (`ticket_mcp.handle_create_ticket` /
`handle_update_ticket`), not in the MCP-only `_check_assignee` wrapper:

```python
# in the shared create/update handler, reached by MCP, WS, and CLI:
def _validate_assignee(assignee: str | None, *, config: Config, project_path: Path) -> None:
    if not assignee:
        return
    try:
        resolve_actor(assignee, config=config, project_path=project_path)   # roles ∪ humans ∪ reserved
    except UnknownActorError:
        raise ValueError(f"unknown assignee {assignee!r}")
```

`mcp_server.py`'s existing `_check_assignee` becomes a thin pass-through (or is removed in favour of the shared
check). `find_by_assignee` and the `assignee` index already work; they now receive meaningful, validated values
on **every** surface. `assignee` may name an agent role (today's behavior) or a human handle (new) — per-ticket
human assignment falls out for free.

### 6. Seams left for later sub-projects (no behavior here)

- `actor.type == HUMAN` → SP3 dispatch branches "park for human" vs. spawn.
- `VALIDATOR in actor.roles` → SP6 close-gate; complements the existing `SpecificHumanEvaluator(user)` path
  (the configured validator must be a human whose `roles` include `validator`).
- `REVIEWER in actor.roles` → SP6 gate before a human may post federation findings / be added to
  `reviewer_set`.

These predicates are made available and unit-tested in isolation; nothing calls them for runtime decisions in
SP1.

## Interfaces

- **New module `jig/actor.py`:** `ActorType`, `ActorRole`, `Actor`,
  `resolve_actor(handle, *, config, project_path)`, `resolve_current_actor(config)`, `UnknownActorError`.
- **`jig/config.py`:** new `HumanEntry` model + `Config.humans: list[HumanEntry]`; handle-shape, reserved-handle,
  and role-collision validation in `load_config` (the latter using `list_role_names(project_path)`); drift check
  for `roles[].human` / `escalation.default_human` (config-local fields only — see Risks); export additions in
  `__all__`.
- **Shared ticket handler (`ticket_mcp.py`):** registry-backed `assignee` validation on create/update, reached
  by MCP, WS, and CLI. `mcp_server.py`'s `_check_assignee` becomes a pass-through / is removed.
- **`.jig/config.yaml`:** new optional `humans:` section (back-compatible default `[]`).
- **`ws_server.py`:** operator attribution sourced from `resolve_current_actor`.
- **Workflow/catalog validation:** `SpecificHumanEvaluator.user` references validated against `config.humans`
  here (not in `load_config`), since evaluator specs live in workflow YAML — see Risks.

## Data model

`HumanEntry` (above) is the only new persistent shape, embedded in `config.yaml`. No new store, no new JSONL
file, no change to `Ticket`/`ThreadEntry`/`ReviewerComment` schemas (attribution remains a bare string).

## Alternatives considered

### New standalone `.jig/humans.yaml` roster
A dedicated file matching the `.jig/roles/`, `.jig/workflows/` file-per-concern pattern, with its own
`load_humans`. Rejected: `config.yaml` already hosts where humans are declared (`roles[].human`,
`escalation.default_human`); a parallel file creates a second "where are humans?" site. (User chose the
`humans:` section.)

### Colon-namespaced refs (`agent:dev` / `human:brent`)
Self-describing, but pushes colons into attribution strings that collide with `safe_path` segment rules, ripple
through ~40 files, and need backfilling existing `"user"`/role records. Rejected — the registry resolves type
without encoding it. (User chose bare handles.)

### Reuse role-staffing *only* (no per-person capability list)
Treat a human purely as how a role is staffed (`config.roles[].human`), with capabilities positional (validator
= ticket's evaluator, reviewer = membership in `reviewer_set`). Simpler, max reuse — but offers no guardrail
that a given human *may* validate, and can't express per-ticket human assignment cleanly (a role is human-or-
agent, not per-ticket). Rejected in favor of a thin global `roles[]` per human that *complements* the existing
positional placement. (User chose global per-person roles[].)

### Full migration to an actor-centric roster
Make a roster the single source and refactor `config.roles[].human` / `SpecificHumanEvaluator` to reference it.
Cleanest end-state, one model — but a real refactor touching shipped Phase-3 ownership/evaluator code and their
tests, for no near-term benefit. Rejected. (User: reuse, don't migrate.)

### Chosen
A thin additive `Actor` registry over a `humans:` config section, with bare-handle attribution and derived
agent capabilities, leaving all shipped human machinery in place. Lowest blast radius; team-ready data; the
single-operator experience is the one-entry degenerate case.

## Risks / Trade-offs

- **Two human-declaration surfaces coexist** (`humans:` for actor identity/capabilities vs. `roles[].human` for
  role staffing). Trade-off accepted for this sub-project (no migration). Risk: they can drift (a `human:` in
  `roles[]` not present in `humans:`). Mitigation, **split by where the data lives**:
  - `load_config` **hard-errors** when a *config-local* human reference — `config.roles[].human` or
    `config.escalation.default_human` — names a handle absent from a non-empty `humans:`. These fields are in
    `.jig/config.yaml`, so `load_config` can see them.
  - `SpecificHumanEvaluator.user` references are **not** checked in `load_config`: evaluator specs live in
    **workflow YAML** (`PhaseConfig.evaluator`), which `load_config` does not (and should not) parse. They are
    validated in the **workflow/catalog validation** pass, where both the workflows and `config.humans` are
    available. (Earlier drafts wrongly assigned this to `load_config`.)

  `humans:` is the single source of truth for who exists; a staffed-but-undeclared human is a config bug that
  fails loud, not a silent fallback. Practically: every human named elsewhere must appear in `humans:`. (jig has
  no production deployments and config is regenerable, so this is a one-time authoring step, not a migration.) A
  future sub-project may consolidate the two surfaces entirely.
- **Handle/role collision** would make resolution ambiguous → rejected loudly at `load_config`.
- **Agent capability derivation is heuristic** (`reviewer*` → reviewer, etc.). Trade-off: humans are the
  primary reviewers/validators in #116; agent derivation only matters where an agent fills those seats, and is
  best-effort. If it proves wrong, a later explicit mapping can replace it.
- **`"user"` legacy records** resolve via alias; no backfill (consistent with "no migration — data
  regenerable").
- **Seam without consumer.** `resolve_current_actor` and capability predicates have no runtime caller until
  SP3/SP6. Accepted: they are the foundation's purpose and are independently unit-testable.

## Testing strategy

- **Unit (`jig/actor.py`):** `resolve_actor` for an agent role, a human handle, `orchestrator`, the `"user"`
  alias, and unknown (raises). Agent `roles` derivation against real (hyphenated) role ids: `dev` →
  `{worker}`; `reviewer-generalist` and `reviewer-security` → `{worker, reviewer}`; `validate` →
  `{worker, validator}`. Human/agent handle collision rejection; reserved-handle (`user`/`system`/
  `orchestrator`) rejection in `humans:`.
- **Unit (config):** parse a multi-entry `humans:`; default `[]` still validates; handle-shape rejection; git
  seeding when `humans == []`; **hard error** when `roles[].human` / `escalation.default_human` names a handle
  absent from a non-empty `humans:`.
- **Unit (workflow/catalog validation):** **hard error** when a `SpecificHumanEvaluator.user` in a workflow
  names a handle absent from `config.humans` (validated here, not in `load_config`).
- **Unit (current actor):** `resolve_current_actor` returns the sole human; returns git-seeded operator when
  `humans == []`.
- **Integration:** `assignee` round-trips through `create_ticket`/`update_ticket` for an agent role and a human
  handle; unknown assignee rejected; `find_by_assignee` returns the ticket.
- **Regression:** existing `ownership.py` / evaluator / thread / reviewer tests pass unchanged; a record
  authored `"user"` still resolves.
- Gate: `uv run pytest tests/ -v`, `uv run ruff check jig/`, `uv run ruff format --check jig/`.

## Migration / Rollout

No data migration (no production deployments; `.jig/store` regenerable). Additive rollout:

1. Add `jig/actor.py` (Actor + resolve_actor + resolve_current_actor).
2. Add `HumanEntry` + `Config.humans` + handle-shape/reserved/collision/drift validation; default `[]` keeps
   old configs valid.
3. Move registry-backed `assignee` validation into the shared ticket handler (MCP/WS/CLI); reduce
   `_check_assignee` to a pass-through.
4. Swap hard-coded `"user"` attribution in `ws_server.py` for `resolve_current_actor().handle`.
5. `init_project` seeds a one-entry `humans:` from git for new projects.

Nothing changes runtime dispatch or review behavior, so SP1 merges independently; SP3/SP6 consume the seams
afterward.

## Open questions

- [ ] Does any existing consumer of `config.roles[].human` need to resolve through `resolve_actor` now, or can
      that wait for SP3/SP6? (Proposed: wait — SP1 stays read-additive.)

## Resolved questions

- **Drift check is a hard error** (settled 2026-05-30), applied at the layer that can see each field:
  `load_config` hard-errors on `roles[].human` / `escalation.default_human` absent from a non-empty `humans:`;
  workflow/catalog validation hard-errors on `SpecificHumanEvaluator.user` absent from `config.humans`.
  `humans:` is the single source of truth for who exists; no silent fallback.

## Change log

- 2026-05-30: Initial draft (brent)
- 2026-05-30: Rewrote to build on shipped Phase-3 human machinery (config.roles[].human, ownership.py,
  SpecificHumanEvaluator); humans declared in a `config.yaml` `humans:` section with global per-person roles[];
  corrected two errors in the first draft (_check_assignee is a real validator, not a stub; a human/role model
  already exists). (brent)
- 2026-05-30: Addressed roborev review (jobs #237–241): `resolve_actor` takes `project_path` and uses
  `list_role_names` (no invented `role_exists`); explicit agent-capability rules against real role ids
  (`review`/`reviewer-*`/`reviewer_*` → reviewer, `validate` → validator); assignee validation moved to the
  shared ticket handler so WS/CLI paths validate too; drift check split by data location (`load_config` for
  config-local fields, workflow/catalog validation for `SpecificHumanEvaluator.user`); `user`/`system`/
  `orchestrator` reserved handles; clarified `"user"` is the shipped operator pseudo-role; problem.md
  current-state claims corrected (assignee partially used; CRUD spans MCP/WS/TUI/CLI; bare handles not
  namespaced refs). (brent)
