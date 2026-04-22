# Phase 5 Task H — Waiver authority as capability

Status: design approved, ready for implementation plan.

## Goal

Retire the flat `config.waiver_authority: list[str]` and express
waiver authorization as a first-class capability in the policy layer
introduced by Phase 5 Tasks F–G. After this change, "who may waive
which kinds of things" is declared per role (with phase overrides) in
the same YAML slot as tool and path capabilities — one authorization
model for every kind of permit.

## Non-goals

- No migration path. The project has no legacy users; the old
  `config.waiver_authority` field is deleted outright, not
  deprecated.
- No warning infrastructure in `validate_catalog`. Unknown waiver
  tokens fail validation outright (consistent with unknown tool
  names and bad path globs today). A warn-level channel in the
  catalog validator is a separate, optional change if warnings
  become useful elsewhere.
- No runtime capability elevation. Authorization is fixed at spawn
  time — same contract as the rest of the capability layer (doc 16
  §Runtime override: not supported).
- No plumbing for user-driven waives. There's no TUI/CLI path for
  users to post a `Waiver` today. `user.yaml` ships ready for when
  one lands; no agent-side code is wired for it yet.

## Schema

Add `waivers` as a fourth sub-field on `CapabilityDeclaration` in
`jig/capabilities.py`:

```python
class CapabilityWaivers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_waive: list[str] = []


class CapabilityDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: CapabilityTools | None = None
    tool_params: CapabilityToolParams | None = None
    paths: CapabilityPaths | None = None
    waivers: CapabilityWaivers | None = None  # new
```

`can_waive` is a flat list of string tokens. Recognised tokens in v0
are:

- `"objection"` — any `Objection` thread entry (no subtype today).
- `"check_failure:required"` — `SystemEvent(event_type="check_failure")`
  where `check_severity == "required"`.
- `"check_failure:warning"` — same, `check_severity == "warning"`.

The colon-delimited shape (`"<kind>:<qualifier>"`) extends cleanly
when new waiveable dimensions land — e.g. `"objection:security"` if
objections grow a `kind`, or `"check_failure:advisory"` for a future
third severity. No schema churn per new dimension; just a new string
in the recognised set.

Single source of truth for recognised tokens:

```python
# jig/capabilities.py
WAIVE_TOKENS: frozenset[str] = frozenset({
    "objection",
    "check_failure:required",
    "check_failure:warning",
})


def is_known_waive_token(token: str) -> bool:
    return token in WAIVE_TOKENS
```

### YAML shape

```yaml
# e.g. a project's PO-helper role template
role: po_helper
phase_prompt: ...
capabilities:
  waivers:
    can_waive:
      - objection
      - check_failure:required
      - check_failure:warning
```

### Phase override

Waiver capability can be broadened per phase:

```yaml
# workflow phase that grants warning-level waivers to a worker role
phases:
  - name: integration
    role: dev
    capability_overrides:
      waivers:
        can_waive:
          - check_failure:warning
```

## Merge semantics

Phase overrides **union** with the base, same as every other list
field on the capability declaration. Extend `merge_declarations` in
`jig/capabilities.py`:

```python
merged_waivers: CapabilityWaivers | None = None
if base.waivers is not None or override.waivers is not None:
    merged_waivers = CapabilityWaivers(
        can_waive=_merge_str_lists(
            (base.waivers.can_waive if base.waivers else []),
            (override.waivers.can_waive if override.waivers else []),
        ),
    )

return CapabilityDeclaration(
    tools=merged_tools,
    tool_params=merged_params,
    paths=merged_paths,
    waivers=merged_waivers,
)
```

Consequence: phase overrides **broaden** waiver authority but cannot
narrow it. Matches doc 16's "permit-and-deny, phase broadens" model;
keeps the merge rule uniform across the capability layer (no special
case for waivers).

## Compiler

Add `CompiledWaiverRules` mirroring the source shape and include it on
`CompiledRules` in `jig/capability_compiler.py`. Bump
`SCHEMA_VERSION: 1 → 2`.

```python
class CompiledWaiverRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_waive: list[str] = []


class CompiledRules(BaseModel):
    schema_version: int = SCHEMA_VERSION
    tools: CompiledToolRules = Field(default_factory=CompiledToolRules)
    bash: CompiledBashRules = Field(default_factory=CompiledBashRules)
    paths: CompiledPathRules = Field(default_factory=CompiledPathRules)
    waivers: CompiledWaiverRules = Field(default_factory=CompiledWaiverRules)
```

`compile()` extracts `merged.waivers.can_waive` and populates
`CompiledRules.waivers`. Waivers also land in the on-disk
`rules.json` so the compiled artefact reflects the full policy even
though only orchestrator code reads the waivers block. Hook scripts
don't read or act on it — they ignore unknown top-level keys.

## Plumbing

Waiver authorization runs **in the orchestrator process**, not in
the sandbox. The sandbox `rules.json` exists for Claude Code hook
scripts (PreToolUse / check-bash / check-write / check-path); those
scripts gate tool invocations. `thread_waive` / `thread_waive_check`
are MCP tools whose handlers live in `jig/thread_mcp.py` and execute
orchestrator-side. They authorize by checking the agent's compiled
`can_waive` set.

**Spawn-time flow:**

1. `jig/agent.py::_materialize_capability_policy` already compiles
   `role.capabilities` + `phase.capability_overrides`. It extends to
   also extract `compiled.waivers.can_waive`.
2. `create_agent_mcp_server` gains a new keyword argument:
   `can_waive: frozenset[str] = frozenset()`.
3. Agent spawn passes `can_waive=frozenset(compiled.waivers.can_waive)`.
4. MCP server stores the frozenset on its closure and forwards it to
   the two waiver handlers.

**Handler signatures change:**

```python
async def handle_thread_waive(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    can_waive: frozenset[str],   # new
    args: dict[str, Any],
    # project_path removed — no more config.waiver_authority read
) -> dict[str, Any]: ...


async def handle_thread_waive_check(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    can_waive: frozenset[str],   # new
    args: dict[str, Any],
    # project_path removed
) -> dict[str, Any]: ...
```

**Why compile-once at spawn rather than lookup-on-every-waive:**

- Role and phase don't change mid-phase, so re-resolving per call is
  wasted work.
- Keeps `thread_mcp` handlers free of filesystem / config I/O at
  waive-time.
- Mirrors how `allowed_tools` is already plumbed
  (`RoleConfig → agent.py → ClaudeAgentOptions`).

**Token construction at waive-time:**

- `handle_thread_waive` — always checks `"objection"`.
- `handle_thread_waive_check` — severity comes from the SystemEvent,
  so the authorization check has to run **after** event lookup, not
  before (inverted from today's order, where the config-based auth
  ran first). An unauthorized caller who passes a bogus
  `check_failure_id` now gets a "not found" error rather than a
  "not authorized" one; this is deemed acceptable since any caller
  with thread-read access can confirm existence via other tools.
- Defensive fail-closed: if a check_failure event's `check_severity`
  is `None` (the type allows it, though populated-for-sure in
  practice), token construction yields `"check_failure:None"`, which
  is not in `WAIVE_TOKENS` and matches nothing — the handler denies
  rather than authorizing by default.

## Error messages

Current:

```
'dev' is not authorized to waive objections
(config.waiver_authority=['po', 'sa', 'user'])
```

New, for objections:

```
role 'dev' cannot waive objections — capabilities.waivers.can_waive
must include 'objection' (current: ['check_failure:warning'])
```

New, for check failures:

```
role 'dev' cannot waive check failures of severity 'required' —
capabilities.waivers.can_waive must include 'check_failure:required'
(current: ['check_failure:warning'])
```

Rules:

- Always name the exact token that would authorize the call.
- Always show the actor's current `can_waive` list (sorted for
  determinism) to speed diagnosis.
- Lead with the **role** name, not `sender`. Today `sender` *is* the
  role, but framing the error as "this role is missing this
  capability" points directly at the YAML that needs editing.
- Exception type stays `ThreadError`.

Small helper shared between both handlers lives in `thread_mcp.py`:

```python
def _require_waive_token(
    token: str,
    *,
    role: str,
    can_waive: frozenset[str],
    subject: str,
) -> None:
    if token not in can_waive:
        raise ThreadError(
            f"role {role!r} cannot waive {subject} — "
            f"capabilities.waivers.can_waive must include {token!r} "
            f"(current: {sorted(can_waive)})"
        )
```

## Catalog validation

`jig/catalog.py::_validate_capabilities` extends with a waiver-token
check:

```python
if decl.waivers is not None:
    for token in decl.waivers.can_waive:
        if not is_known_waive_token(token):
            yield (
                f"waivers.can_waive contains unknown token {token!r} "
                f"(known: {sorted(WAIVE_TOKENS)})"
            )
```

Unknown tokens fail validation outright (`fail()` path). Rationale:

- Consistent with how unknown tool names in `tools.allowed` are
  treated today.
- Typos are the dominant failure mode. Handler's exact-string
  membership check would silently deauthorize a role whose author
  believed it had the capability — the inverse of what `jig validate`
  exists to catch.
- Forward-compat tokens (writing `"objection:security"` before the
  feature lands) lose support. Trade accepted: extending the
  recognised set is a one-line change when the feature lands.

Existing `config.waiver_authority` validation block is deleted
outright along with the field.

## The "user" sentinel

Ship `jig/defaults/roles/user.yaml`:

```yaml
role: user
phase_prompt: ""
capabilities:
  waivers:
    can_waive:
      - objection
      - check_failure:required
      - check_failure:warning
```

`phase_prompt` is relaxed to `str = ""` default on `RoleConfig` in
`jig/models.py` to accommodate non-dispatched roles. No spawn path
exercises an empty `phase_prompt` today — the user role is never
dispatched to Claude Code — so the relaxation is free.

Authorization flow for user-driven waives (future):

- A future TUI/CLI "waive" action would load `user` role via
  `load_role(project_path, "user")`, compile its capabilities, and
  invoke `handle_thread_waive` / `handle_thread_waive_check` with the
  resulting `can_waive` frozenset — same plumbing as the agent path.
- No code wires that path in Task H. The file ships ready.

The user explicitly wanted `user.yaml` to go through the same gate as
agents so future "type this phrase to confirm"-style confirmation
flows for humans can hook into the same authorization codepath.

## Files touched

**Delete:**

- `Config.waiver_authority` (`jig/config.py` lines 114–120) and every
  `cfg.waiver_authority` reference in `jig/thread_mcp.py`.
- The `waiver_authority` validation block in `jig/catalog.py`
  (~lines 231–248).
- `_write_config(tmp_path, waiver_authority=...)` helper and all
  call sites in `tests/test_thread_mcp.py`.
- Waiver-authority tests in `tests/test_catalog_validation.py`.

**Add:**

- `jig/defaults/roles/user.yaml`.
- `CapabilityWaivers` + `WAIVE_TOKENS` + `is_known_waive_token` in
  `jig/capabilities.py`.
- `CompiledWaiverRules` in `jig/capability_compiler.py`.
- `_require_waive_token` helper in `jig/thread_mcp.py`.

**Modify:**

- `jig/capabilities.py` — `CapabilityDeclaration` gains `waivers`;
  `merge_declarations` handles the new field.
- `jig/capability_compiler.py` — `CompiledRules` gains `waivers`;
  `SCHEMA_VERSION` bumps to 2; `compile()` propagates the new field.
- `jig/catalog.py` — `_validate_capabilities` checks `can_waive`
  tokens; `waiver_authority` block deleted.
- `jig/models.py` — `phase_prompt: str = ""` default.
- `jig/agent.py` — `_materialize_capability_policy` threads the
  compiled `can_waive` frozenset into MCP server construction.
- `jig/mcp_server.py` — `create_agent_mcp_server` accepts `can_waive`;
  forwards to both waiver handlers; tool docstrings updated to
  reference capabilities, not config.
- `jig/thread_mcp.py` — handler signatures change per the plumbing
  section; error messages updated; `project_path` + `load_config`
  dependencies removed from these two handlers.

**Documentation:**

- `docs/08-threads.md` — update the `thread_waive` MCP tool
  description to reference capabilities.
- `docs/16-policy-and-enforcement.md` — add a short section on
  waiver capability shape (token mapping to waiveable events).
- `docs/implementation-plan.md` — check the four Task H boxes at
  implementation time; strike through the migration line.

## Tests

**Existing tests rewritten** (`tests/test_thread_mcp.py`):

- `_write_config(waiver_authority=...)` helper deleted.
- All `handle_thread_waive` / `handle_thread_waive_check` calls
  updated: drop `project_path`, add
  `can_waive=frozenset({...})`.
- Authorization tests rewritten to assert new error shape +
  capability-based deny.
- Positive-case tests preserved; only signatures change.

**New tests — capabilities layer** (`tests/test_capabilities.py`):

- `CapabilityWaivers` parses, rejects extras.
- `merge_declarations` unions `waivers.can_waive` across base and
  override without cross-contamination with other fields.
- Base-only, override-only, both-declared, neither-declared cases.

**New tests — compiler** (`tests/test_capability_compiler.py`):

- `compile()` propagates `waivers.can_waive` into
  `CompiledRules.waivers`.
- Empty declaration produces empty `CompiledWaiverRules`.
- `SCHEMA_VERSION == 2` in output.
- `rules.json` on disk carries the `waivers` block.

**New tests — catalog validation** (`tests/test_catalog_validation.py`):

- Unknown `can_waive` token fails validation, error names known
  tokens.
- Role with valid `can_waive` passes.
- Phase override with unknown token fails.

**New tests — thread_mcp handlers** (`tests/test_thread_mcp.py`):

- Authorized waive succeeds when matching token is in `can_waive`.
- Unauthorized waive fails with new `ThreadError` naming the exact
  missing token.
- Check-failure waive routes severity into the token — a role with
  `can_waive=["check_failure:warning"]` is denied a `required`
  waive and allowed a `warning` waive.

**Integration test** (`tests/test_agent_streaming.py`):

- `_materialize_capability_policy` threads compiled waiver tokens
  into the MCP server's `can_waive` set. A phase override's
  `can_waive` shows up in the MCP server's frozenset.
