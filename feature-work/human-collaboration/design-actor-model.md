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
- **Agents:** `roles` is *derived* from the role name/config so we don't hand-maintain a second list — every
  agent role is a `WORKER`; a role whose name starts `reviewer` (or that appears in any phase's `reviewers`)
  also gets `REVIEWER`; the role configured as a close-phase evaluator contributes `VALIDATOR`. (Derivation is
  best-effort and only used where an agent could fill that seat; humans are the primary validators in #116.)
- **System:** the singleton `orchestrator` actor, `roles=∅`.

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
- **Seeding:** `init_project` writes one `HumanEntry` whose `handle` is slugified from `git config user.name`
  (fallback `user.email` local-part, then `"operator"`), `display_name` from git, all three roles.
- **Handle shape** reuses the safe-segment regex so handles never carry colons or collide with path/topic
  syntax.

### 3. `resolve_actor(handle, config)`

```python
def resolve_actor(handle: str, config: Config) -> Actor:
    if handle in ("orchestrator", "system"):
        return _SYSTEM_ACTOR
    if handle == "user":                       # legacy alias
        return _current_or_seeded_human(config)
    for h in config.humans:                    # humans win ties (collisions rejected at load)
        if h.handle == handle:
            return _human_actor(h)
    if role_exists(handle, config):            # defaults/roles/ or .jig/roles/
        return _agent_actor(handle, config)
    raise UnknownActorError(handle)
```

- **Single flat handle namespace.** A human handle must not equal an agent role name; the collision is rejected
  at config load with a loud error (so resolution is unambiguous and callers branch on `actor.type`, never on
  string parsing).
- **`"user"` legacy alias** keeps every historical JSONL record (`author="user"`) resolvable with no backfill.

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

### 5. Assignee validation (widen, don't replace)

`mcp_server.py:138` currently builds `_allowed_assignees = valid_roles | {"orchestrator", "user"}`. Widen it to
include known human handles, delegating to the registry so there's no second list to maintain:

```python
def _check_assignee(assignee: str | None) -> None:
    if not assignee:
        return
    try:
        resolve_actor(assignee, config)        # roles ∪ humans ∪ {orchestrator, user}
    except UnknownActorError:
        raise ValueError(f"unknown assignee {assignee!r}")
```

`find_by_assignee` and the `assignee` index already work; they now receive meaningful values. `assignee` may
name an agent role (today's behavior) or a human handle (new) — per-ticket human assignment falls out for free.

### 6. Seams left for later sub-projects (no behavior here)

- `actor.type == HUMAN` → SP3 dispatch branches "park for human" vs. spawn.
- `VALIDATOR in actor.roles` → SP6 close-gate; complements the existing `SpecificHumanEvaluator(user)` path
  (the configured validator must be a human whose `roles` include `validator`).
- `REVIEWER in actor.roles` → SP6 gate before a human may post federation findings / be added to
  `reviewer_set`.

These predicates are made available and unit-tested in isolation; nothing calls them for runtime decisions in
SP1.

## Interfaces

- **New module `jig/actor.py`:** `ActorType`, `ActorRole`, `Actor`, `resolve_actor(handle, config)`,
  `resolve_current_actor(config)`, `UnknownActorError`.
- **`jig/config.py`:** new `HumanEntry` model + `Config.humans: list[HumanEntry]`; collision/handle-shape
  validation in `load_config`; export additions in `__all__`.
- **`.jig/config.yaml`:** new optional `humans:` section (back-compatible default `[]`).
- **`mcp_server.py`:** `_check_assignee` widened to registry-backed validation.
- **`ws_server.py`:** operator attribution sourced from `resolve_current_actor`.

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
  `roles[]` not present in `humans:`). Mitigation: `load_config` emits a warning (not a hard error) when a
  `roles[].human` / `escalation.default_human` / evaluator `user` names a handle absent from `humans:`. A
  future sub-project may consolidate.
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
  alias, and unknown (raises). Agent `roles` derivation (plain → `{worker}`; `reviewer_*` → `{worker,
  reviewer}`; configured validator role → includes `{validator}`). Human/agent handle collision rejection.
- **Unit (config):** parse a multi-entry `humans:`; default `[]` still validates; handle-shape rejection; git
  seeding when `humans == []`; drift warning when `roles[].human` names an unknown handle.
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
2. Add `HumanEntry` + `Config.humans` + collision/shape/drift validation; default `[]` keeps old configs valid.
3. Widen `_check_assignee` to registry-backed validation.
4. Swap hard-coded `"user"` attribution in `ws_server.py` for `resolve_current_actor().handle`.
5. `init_project` seeds a one-entry `humans:` from git for new projects.

Nothing changes runtime dispatch or review behavior, so SP1 merges independently; SP3/SP6 consume the seams
afterward.

## Open questions

- [ ] Should the `roles[].human` ↔ `humans:` drift check be warn-only (proposed) or a hard error? Leaning warn,
      to avoid breaking configs that staff a role with a human not yet in `humans:`.
- [ ] Does any existing consumer of `config.roles[].human` need to resolve through `resolve_actor` now, or can
      that wait for SP3/SP6? (Proposed: wait — SP1 stays read-additive.)

## Change log

- 2026-05-30: Initial draft (brent)
- 2026-05-30: Rewrote to build on shipped Phase-3 human machinery (config.roles[].human, ownership.py,
  SpecificHumanEvaluator); humans declared in a `config.yaml` `humans:` section with global per-person roles[];
  corrected two errors in the first draft (_check_assignee is a real validator, not a stub; a human/role model
  already exists). (brent)
