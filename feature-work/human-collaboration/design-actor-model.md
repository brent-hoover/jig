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

## Overview

Introduce a thin `Actor` abstraction so that every actor in jig — agent role or human — resolves to a uniform
`Actor(type, handle, roles)` value through a single `resolve_actor(handle)` function. Humans come from a new
one-entry-today `.jig/humans.yaml` roster (seeded from git identity); agents come from the existing
`defaults/roles/*.yaml`. Attribution stays a **bare handle** (`author="dev"`, `author="brent"`) — no colon
namespacing — so the change is additive and the existing JSONL stores and code keep working unchanged. This is
sub-project 1 of the [#116](https://github.com/brent-hoover/jig/issues/116) epic and the foundation the dispatch
router (SP3) and review/validator gate (SP6) build on.

## Background / Context

See [problem.md](./problem.md) for the epic framing. Key facts from the current codebase that shape this design:

- **Attribution is a bare role-name string.** MCP tool handlers record `sender=agent_role` (e.g. `"dev"`,
  `"review"`) into `Ticket.created_by`, `ThreadEntry.author`, `ReviewerComment.reviewer`. The human operator is
  hard-coded `author="user"` / `sender="user"` in `ws_server.py` (the `/now` answer path).
- **`agent_id` already uses a colon** (`f"{role}:{ticket_id[:8]}"` in `orchestrator.py`) but only as a *logging
  id*, never as the attribution string. Our bare-handle decision avoids colliding with this.
- **`safe_path.py` rejects colons** in path segments. Actor strings never reach paths today; keeping handles
  colon-free keeps it that way.
- **`assignee` already exists and is store-ready.** `Ticket.assignee: str | None` (ticket.py:280) and
  `find_where(..., assignee=...)` filtering (store/tickets.py) are wired, but `assignee` is never populated and
  never dispatched on. The data layer is ready; only semantics are missing.
- **Assignee validation is currently a no-op.** `_check_assignee` in `mcp_server.py` is a `pass` stub. (An
  earlier `_allowed_assignees = valid_roles | {"orchestrator", "user"}` notion is the natural place to widen.)
- **`any_human` / `human` escape hatch** already exists for Question targets
  (`_HUMAN_TARGET_ESCAPE_HATCH = {"human", "any_human"}` in thread_mcp.py).

## Goals / Non-Goals

**Goals**

- A single `resolve_actor(handle) -> Actor` seam that classifies any handle as human or agent and exposes its
  roles, used by dispatch (SP3), the review/validator gate (SP6), and assignee validation.
- A `.jig/humans.yaml` roster with one entry today, seeded from git identity, but able to hold many.
- A `resolve_current_actor()` seam that returns "who is acting now" — the sole roster human today; later it can
  read `JIG_ACTOR` / `--as` / a picker without changing callers.
- Widen `assignee` validation to accept agent roles ∪ human handles ∪ `{orchestrator}`.
- Replace the hard-coded `"user"` attribution with the resolved operator handle.
- Keep all of it **additive**: bare-handle attribution, no migration, existing records and tests unaffected.

**Non-Goals**

- No colon-namespaced refs (`human:brent`). Rejected to avoid `safe_path` friction and a wide refactor.
- No authentication. "Current actor" is *selected/trusted*, never verified.
- No selection UX (env/flag/picker) built now — only the seam that will later host it.
- No dispatch behavior change here (SP3), no review/validator wiring here (SP6). This sub-project only defines
  and exposes the abstraction and wires identity/validation.
- No multi-human UX. The roster may hold many entries; nothing today renders or selects among them.

## Proposed design

### 1. The `Actor` value and `ActorType`

A new module `jig/actor.py`:

```python
class ActorType(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"   # "orchestrator"

class Actor(BaseModel):
    handle: str               # bare, colon-free, e.g. "dev" or "brent"
    type: ActorType
    display_name: str         # "dev" for agents; roster display name for humans
    roles: frozenset[str]     # hats the actor may wear: worker | reviewer | validator
```

For agents, `roles` is derived (every agent role is a `worker`; reviewer-`*` roles also carry `reviewer`; the
validator role carries `validator`). For humans, `roles` comes straight from the roster entry. `system` is the
fixed `orchestrator` actor.

### 2. The human roster — `.jig/humans.yaml`

```yaml
# .jig/humans.yaml  (one entry today; seeded from git on init)
humans:
  - handle: brent
    display_name: Brent Hoover
    roles: [worker, reviewer, validator]
```

- **Loader** lives in `persistence.py` next to `load_role`/`load_workflow`:
  `load_humans(project_path) -> dict[str, Actor]`, keyed by handle.
- **Seeding:** `init_project` (and a lazy fallback if the file is absent) writes a single entry whose `handle`
  is derived from `git config user.name`/`user.email` (slugified, colon-free; fallback `"operator"`), with all
  three roles. Absence of the file is not an error — it degenerates to a single git-seeded operator so existing
  projects keep working.
- **Handle constraint:** must match the existing safe-segment shape (`^[a-z0-9][a-z0-9_-]*$`) so handles are
  always path/topic-safe and never collide with the `:` used elsewhere.

### 3. `resolve_actor(handle)`

```python
def resolve_actor(handle: str, project_path: Path) -> Actor:
    if handle == "orchestrator":
        return _SYSTEM_ACTOR
    humans = load_humans(project_path)
    if handle in humans:
        return humans[handle]
    if role_exists(handle, project_path):        # defaults/roles/ or .jig/roles/
        return _agent_actor(handle, project_path)
    raise UnknownActorError(handle)
```

Resolution order is human-first then agent. Handles are a **single flat namespace**: a human handle and an
agent role must not collide (enforced at roster load — reject a human handle that shadows a known role). This is
how "bare handle, type resolved not encoded" works: callers branch on `actor.type`, never on string parsing.

Back-compat: `"user"` is accepted as an alias that resolves to the sole/seeded operator, so old JSONL records
authored by `"user"` still resolve.

### 4. `resolve_current_actor()` — the selection seam

```python
def resolve_current_actor(project_path: Path) -> Actor:
    # today: the sole human in the roster (or the git-seeded operator).
    # later: JIG_ACTOR env / --as flag / TUI picker, validated against the roster.
    humans = load_humans(project_path)
    return _single_or_seeded(humans)
```

This is the one function the human-facing surfaces call to attribute an action. `ws_server.py`'s `/now` answer
path stops hard-coding `"user"` and instead writes `author=resolve_current_actor(...).handle`. Because the
seam exists from day one, adding `JIG_ACTOR`/`--as`/picker later touches only this function.

### 5. Assignee validation

Replace the `_check_assignee` stub with a real check that delegates to the registry:

```python
def _check_assignee(handle: str | None, project_path: Path) -> None:
    if handle is None:
        return
    try:
        resolve_actor(handle, project_path)   # agents ∪ humans ∪ {orchestrator}
    except UnknownActorError:
        raise ValueError(f"unknown assignee: {handle!r}")
```

No new allow-list to maintain — the registry *is* the allow-list. `find_by_assignee` already works; it now
receives meaningful values.

### 6. What this sub-project deliberately leaves as seams (consumed later)

- `actor.type == HUMAN` is what SP3's dispatch will branch on to "park for human" vs. spawn.
- `"validator" in actor.roles` is what SP6's close-gate will check.
- `"reviewer" in actor.roles` is what SP6 will check before letting a human post federation findings.

None of those behaviors are implemented here — only the predicates they will call are made available.

## Alternatives considered

- **Colon-namespaced refs (`agent:dev` / `human:brent`).** More self-describing, but pushes colons into
  attribution strings that the explore pass showed would collide with `safe_path` segment rules and ripple
  through ~40 files, and would require backfilling existing `"user"`/role records. Rejected: the registry gives
  us type resolution without encoding it in the string. (User explicitly chose bare handles.)
- **Single operator constant (no roster).** Cheapest, but caps items 7/8/9 at "the operator does everything"
  and forecloses a team without a later repaint. Considered and reversed in favor of B-shaped backend. The
  one-entry roster is nearly as cheap and keeps the data team-ready.
- **Git-derived identity, no file.** Zero config, but no place to declare per-person `roles` (who may
  validate), so SP6 would reconstruct a roster anyway. We keep git as the *seed* for the roster, not the
  mechanism.
- **No roster file; per-ticket validator config.** Pushes "who may close" onto each ticket/workflow instead of
  the actor. Rejected: capability belongs to the actor, not duplicated per ticket; roster is the single source.

## Risks / Trade-offs

- **Handle/role collision.** A human handle equal to an agent role name would make resolution ambiguous.
  Mitigation: reject at roster load with a loud error.
- **`"user"` legacy records.** Existing JSONL has `author="user"`. Mitigation: `resolve_actor("user")` aliases
  to the operator; no backfill needed (consistent with "no migration — data is regenerable").
- **Seam that looks like dead code.** `roles` on agents and `resolve_current_actor` have no behavioral consumer
  until SP3/SP6. Trade-off accepted: they're the foundation's whole point, and they're cheaply unit-testable in
  isolation. We avoid speculative fields beyond the three confirmed (handle/display_name/roles).
- **Roster absence.** A project with no `.jig/humans.yaml` must still work. Mitigation: lazy git-seeded
  single operator on load.

## Testing strategy

- **Unit (`jig/actor.py`):** `resolve_actor` for agent role, human handle, `orchestrator`, `user` alias, and
  unknown (raises). Agent `roles` derivation (plain role → `{worker}`; `reviewer_*` → `{worker, reviewer}`;
  validator role → `{validator,...}`). Handle/role collision rejection.
- **Unit (roster loader):** parse a multi-entry `humans.yaml`; git-seeding when absent; handle-shape
  validation; `resolve_current_actor` returns the sole entry.
- **Integration:** `assignee` round-trips through `create_ticket`/`update_ticket` for an agent role and a human
  handle; an unknown assignee is rejected; `find_by_assignee` returns the assigned ticket.
- **Regression:** existing thread/reviewer attribution tests still pass with bare handles; a record authored by
  `"user"` still resolves.
- Run `uv run pytest tests/ -v`, `uv run ruff check jig/`, and `uv run ruff format --check jig/` before any PR.

## Migration / Rollout

No data migration (jig has no production deployments; `.jig/store` is regenerable). Rollout is purely additive:

1. Add `jig/actor.py` + roster loader + `resolve_actor`/`resolve_current_actor`; default-seed on load so
   absence of `humans.yaml` is harmless.
2. Replace the `_check_assignee` stub with registry-backed validation.
3. Swap the hard-coded `"user"` attribution in `ws_server.py` for `resolve_current_actor().handle`.
4. `init_project` writes a git-seeded one-entry `humans.yaml` for new projects.

SP3 (dispatch routing) and SP6 (review/validator) consume the seams afterward; nothing in this sub-project
changes runtime dispatch or review behavior, so it can merge independently.

## Change log

- 2026-05-30: Initial draft (brent)
