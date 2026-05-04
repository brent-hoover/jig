# Phase 5 Task H — Waiver Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the flat `config.waiver_authority: list[str]` field and express waiver authority as a first-class capability under `CapabilityDeclaration.waivers.can_waive`, merging via the existing role+phase union semantics and gating the two `thread_waive*` MCP handlers on a compiled `can_waive` frozenset plumbed through at agent spawn time.

**Architecture:** Follow the same compile-at-spawn / enforce-at-use contract the Phase 5 F/G capability policy already uses. `CapabilityDeclaration` gains a `waivers` sub-field; `merge_declarations` unions across role base + phase override; `capability_compiler.compile()` surfaces the merged `can_waive` set through a new `CompiledWaiverRules` on `CompiledRules` and bumps `SCHEMA_VERSION` to 2. Because waiver authorization runs in the orchestrator process (not in the sandbox), `thread_waive` / `thread_waive_check` handlers accept a `can_waive: frozenset[str]` argument at call time — threaded through `create_agent_mcp_server` and populated at spawn in `agent.py`. `config.waiver_authority`, its validation, and its tests are deleted outright (no migration — project has no legacy users).

**Tech Stack:** Python 3.12+, pydantic v2, pytest with asyncio_mode=auto, uv for package management, ruff for lint/format. MCP via `claude-agent-sdk`. YAML roles under `jig/defaults/roles/`.

---

## File Structure

### Created

| File | Responsibility |
|------|----------------|
| `jig/defaults/roles/user.yaml` | Pseudo-role carrying default `can_waive` capability for future user-driven waives. Never dispatched to Claude Code. |

### Modified — source

| File | Responsibility |
|------|----------------|
| `jig/capabilities.py` | Add `CapabilityWaivers` class, `WAIVE_TOKENS` frozenset, `is_known_waive_token()` helper; add `waivers` sub-field on `CapabilityDeclaration`; extend `merge_declarations()` to union waivers. |
| `jig/capability_compiler.py` | Add `CompiledWaiverRules`; put `waivers` on `CompiledRules`; bump `SCHEMA_VERSION` from 1 to 2; propagate merged waivers in `compile()`. |
| `jig/models.py` | Relax `RoleConfig.phase_prompt` default from required to `""` so `user.yaml` is loadable. |
| `jig/thread_mcp.py` | Add `_require_waive_token()` helper; change `handle_thread_waive` / `handle_thread_waive_check` signatures — drop `project_path`, add `can_waive: frozenset[str]`; invert order-of-operations in `handle_thread_waive_check` (auth after event lookup); new error messages; remove `load_config` import; update module docstring comment referencing `config.waiver_authority`. |
| `jig/thread.py` | Update line ~178 docstring comment to reference capability layer, not `config.waiver_authority`. |
| `jig/config.py` | Delete the `waiver_authority: list[str]` field from `Config`. |
| `jig/catalog.py` | Delete the `config.waiver_authority` validation block; extend `_validate_capabilities` to check unknown `can_waive` tokens. |
| `jig/mcp_server.py` | Add `can_waive: frozenset[str] = frozenset()` kwarg to `create_agent_mcp_server`; forward to both waiver handlers; update `thread_waive` / `thread_waive_check` tool docstrings. |
| `jig/agent.py` | `_materialize_capability_policy` additionally surfaces the compiled `can_waive` frozenset; `run_agent` threads it into `create_agent_mcp_server`. |

### Modified — tests

| File | Responsibility |
|------|----------------|
| `tests/test_capabilities.py` | Add `CapabilityWaivers` construction + extras-rejection tests; `merge_declarations` waivers union tests (base-only, override-only, both, neither); `compile()` propagates `waivers.can_waive`; `SCHEMA_VERSION == 2`; `rules.json` carries `waivers`. |
| `tests/test_catalog_validation.py` | Delete `TestWaiverAuthorityReferences` class; add new tests for unknown `can_waive` token validation (role + phase override). |
| `tests/test_thread_mcp.py` | Delete `_write_config(waiver_authority=...)` helper; rewrite every `handle_thread_waive` / `handle_thread_waive_check` call — drop `project_path`, add `can_waive=frozenset({...})`; update unauthorized-waive error match strings; add severity-aware tests for `handle_thread_waive_check`. |
| `tests/test_agent_streaming.py` | Extend `TestMaterializeCapabilityPolicy` to assert waiver tokens flow from role/phase declarations into the MCP server's `can_waive` set. |

### Modified — docs

| File | Responsibility |
|------|----------------|
| `docs/08-threads.md` | Update `thread_waive` MCP-tool description: authorization now references `capabilities.waivers.can_waive`, not `config.waiver_authority`. |
| `docs/16-policy-and-enforcement.md` | Add a short section on waiver capability shape (token mapping for waiveable events). |
| `docs/v2.0/implementation-plan.md` | Check the four Task H boxes; strike the migration line; update Phase 4 prose calling out the retirement. |

---

## Task 1: Add `CapabilityWaivers` source shape + token registry

**Files:**
- Modify: `jig/capabilities.py`
- Test: `tests/test_capabilities.py`

- [ ] **Step 1: Write failing tests for the new types**

Append the following to `tests/test_capabilities.py` right after the existing `TestDeclarationConstruction` class (use the position just before `class TestMergeDeclarations`):

```python
class TestCapabilityWaivers:
    def test_empty_waivers_valid(self) -> None:
        from jig.capabilities import CapabilityWaivers

        w = CapabilityWaivers()
        assert w.can_waive == []

    def test_waivers_accepts_all_known_tokens(self) -> None:
        from jig.capabilities import CapabilityWaivers, WAIVE_TOKENS

        w = CapabilityWaivers(can_waive=sorted(WAIVE_TOKENS))
        assert set(w.can_waive) == WAIVE_TOKENS

    def test_waivers_rejects_extra_fields(self) -> None:
        from jig.capabilities import CapabilityWaivers

        with pytest.raises(ValidationError):
            CapabilityWaivers.model_validate(
                {"can_waive": ["objection"], "extra": "nope"}
            )

    def test_known_waive_tokens_registry(self) -> None:
        from jig.capabilities import WAIVE_TOKENS, is_known_waive_token

        assert "objection" in WAIVE_TOKENS
        assert "check_failure:required" in WAIVE_TOKENS
        assert "check_failure:warning" in WAIVE_TOKENS
        assert is_known_waive_token("objection") is True
        assert is_known_waive_token("check_failure:warning") is True
        assert is_known_waive_token("bogus") is False

    def test_declaration_accepts_waivers_field(self) -> None:
        from jig.capabilities import CapabilityDeclaration, CapabilityWaivers

        decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        assert decl.waivers is not None
        assert decl.waivers.can_waive == ["objection"]

    def test_declaration_waivers_defaults_none(self) -> None:
        from jig.capabilities import CapabilityDeclaration

        decl = CapabilityDeclaration()
        assert decl.waivers is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py::TestCapabilityWaivers -v`
Expected: FAIL with `ImportError: cannot import name 'CapabilityWaivers'` (or `WAIVE_TOKENS` / `is_known_waive_token`).

- [ ] **Step 3: Add `CapabilityWaivers`, `WAIVE_TOKENS`, `is_known_waive_token`, and `waivers` field to `jig/capabilities.py`**

In `jig/capabilities.py`, add a new class after `CapabilityPaths` (before `CapabilityDeclaration`):

```python
class CapabilityWaivers(BaseModel):
    """Waiver-authority declaration. ``can_waive`` is a flat list of
    string tokens matched against waiveable thread entries. Recognised
    tokens are:

    * ``"objection"`` — any :class:`jig.thread.Objection` entry.
    * ``"check_failure:required"`` — a
      :class:`jig.thread.SystemEvent` with ``event_type=="check_failure"``
      and ``check_severity=="required"``.
    * ``"check_failure:warning"`` — same, severity ``"warning"``.

    The colon-delimited shape extends cleanly when new waiveable
    dimensions land (e.g. ``"objection:security"`` if objections grow a
    kind field). ``jig/catalog.py::_validate_capabilities`` fails loud
    on unknown tokens at load time."""

    model_config = ConfigDict(extra="forbid")
    can_waive: list[str] = []


WAIVE_TOKENS: frozenset[str] = frozenset(
    {
        "objection",
        "check_failure:required",
        "check_failure:warning",
    }
)


def is_known_waive_token(token: str) -> bool:
    """True if ``token`` is a recognised ``can_waive`` entry. Used by
    ``jig validate`` to catch typos in
    ``capabilities.waivers.can_waive`` at load time."""

    return token in WAIVE_TOKENS
```

Then add the `waivers` field on `CapabilityDeclaration`. Find the existing class (around line 128) and update:

```python
class CapabilityDeclaration(BaseModel):
    """Top-level capability declaration. Both the role template's base
    and a phase-level override parse into this shape — they merge by
    union under the rules above.

    All four sub-fields are optional so a partial declaration (e.g.,
    tools only, or waivers only) is valid and composes cleanly."""

    model_config = ConfigDict(extra="forbid")
    tools: CapabilityTools | None = None
    tool_params: CapabilityToolParams | None = None
    paths: CapabilityPaths | None = None
    waivers: CapabilityWaivers | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py::TestCapabilityWaivers -v`
Expected: PASS, 6 tests passing.

- [ ] **Step 5: Commit**

```bash
git add jig/capabilities.py tests/test_capabilities.py
git commit -m "feat(capabilities): add CapabilityWaivers + WAIVE_TOKENS registry"
```

---

## Task 2: Extend `merge_declarations` to union waivers

**Files:**
- Modify: `jig/capabilities.py` (lines ~157-213, `merge_declarations`)
- Test: `tests/test_capabilities.py`

- [ ] **Step 1: Write failing merge tests**

Append the following to `tests/test_capabilities.py` inside `class TestMergeDeclarations` (add as new methods at the bottom of the class):

```python
    def test_merge_waivers_base_only(self) -> None:
        from jig.capabilities import CapabilityWaivers

        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        merged = merge_declarations(base, None)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["objection"]

    def test_merge_waivers_override_only(self) -> None:
        from jig.capabilities import CapabilityWaivers

        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        merged = merge_declarations(None, override)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["check_failure:warning"]

    def test_merge_waivers_unions_both(self) -> None:
        from jig.capabilities import CapabilityWaivers

        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        merged = merge_declarations(base, override)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == [
            "objection",
            "check_failure:warning",
        ]

    def test_merge_waivers_dedups(self) -> None:
        from jig.capabilities import CapabilityWaivers

        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        merged = merge_declarations(base, override)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["objection"]

    def test_merge_waivers_neither_declared(self) -> None:
        base = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"])
        )
        override = CapabilityDeclaration()
        merged = merge_declarations(base, override)
        assert merged.waivers is None

    def test_merge_waivers_independent_of_other_fields(self) -> None:
        from jig.capabilities import CapabilityWaivers

        base = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"]),
            waivers=CapabilityWaivers(can_waive=["objection"]),
        )
        override = CapabilityDeclaration(
            paths=CapabilityPaths(writable=["ticket://worktree/**"]),
        )
        merged = merge_declarations(base, override)
        assert merged.tools is not None
        assert merged.tools.allowed == ["Read"]
        assert merged.paths is not None
        assert merged.paths.writable == ["ticket://worktree/**"]
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["objection"]
```

- [ ] **Step 2: Run merge tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py::TestMergeDeclarations -v`
Expected: the six new tests FAIL — existing `merge_declarations` doesn't handle the `waivers` field, so `merged.waivers` is always `None`.

- [ ] **Step 3: Extend `merge_declarations`**

In `jig/capabilities.py`, import `CapabilityWaivers` isn't needed (it's declared in-file). Modify `merge_declarations` — add a new merge block for `waivers` right before the final `return` statement, and update the return to include the new field:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py::TestMergeDeclarations -v`
Expected: PASS. All tests in the class (including the six new ones) green.

- [ ] **Step 5: Commit**

```bash
git add jig/capabilities.py tests/test_capabilities.py
git commit -m "feat(capabilities): merge waivers across role + phase overrides"
```

---

## Task 3: Compiler — `CompiledWaiverRules`, `SCHEMA_VERSION=2`, propagation

**Files:**
- Modify: `jig/capability_compiler.py`
- Test: `tests/test_capabilities.py`

- [ ] **Step 1: Write failing compiler tests**

Append to `tests/test_capabilities.py` inside `class TestCompile` (add as new methods at the end of the class):

```python
    def test_schema_version_is_2(self) -> None:
        from jig.capability_compiler import SCHEMA_VERSION

        assert SCHEMA_VERSION == 2

    def test_compile_waivers_empty_when_undeclared(self) -> None:
        from jig.capability_compiler import CompiledWaiverRules

        rules = compile(None, None)
        assert isinstance(rules.waivers, CompiledWaiverRules)
        assert rules.waivers.can_waive == []

    def test_compile_propagates_waivers(self) -> None:
        from jig.capabilities import CapabilityWaivers

        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        rules = compile(base, override)
        assert rules.waivers.can_waive == [
            "objection",
            "check_failure:warning",
        ]

    def test_compile_output_carries_schema_2(self) -> None:
        rules = compile(None, None)
        assert rules.schema_version == 2
```

Also append to `class TestWriteRulesJson`:

```python
    def test_rules_json_carries_waivers(self, tmp_path: Path) -> None:
        from jig.capabilities import CapabilityWaivers

        decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(
                can_waive=["objection", "check_failure:required"]
            )
        )
        rules = compile(decl, None)
        path = tmp_path / "rules.json"
        write_rules_json(rules, path)
        data = json.loads(path.read_text())
        assert data["schema_version"] == 2
        assert data["waivers"] == {
            "can_waive": ["objection", "check_failure:required"]
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py::TestCompile tests/test_capabilities.py::TestWriteRulesJson -v`
Expected: FAIL — `CompiledWaiverRules` doesn't exist, `SCHEMA_VERSION` is still 1.

- [ ] **Step 3: Implement `CompiledWaiverRules`, bump `SCHEMA_VERSION`, propagate in `compile()`**

In `jig/capability_compiler.py`:

Change `SCHEMA_VERSION` (around line 42):

```python
# Compiled-rules schema version. Bump on breaking changes to
# ``CompiledRules`` so stale hook scripts can fail loud rather than
# silently misinterpret fields.
SCHEMA_VERSION: int = 2
```

Add the new class after `CompiledPathRules` (around line 67):

```python
class CompiledWaiverRules(BaseModel):
    """Compiled waiver capability. Orchestrator-side only — hook
    scripts inside the sandbox don't read this block. The two
    ``thread_waive*`` MCP handlers consult ``can_waive`` at call time;
    the set is fixed at spawn (same contract as the other compiled
    rules: compile-once at spawn, enforce-on-use).

    Ships in ``rules.json`` for completeness of the compiled artefact
    (easier debugging, single source of truth for "what policy did
    this spawn see")."""

    model_config = ConfigDict(extra="forbid")
    can_waive: list[str] = []
```

Update `CompiledRules` to include the new field:

```python
class CompiledRules(BaseModel):
    """Enforcement-side compiled ruleset.

    Serialised to ``rules.json`` verbatim. The hook scripts parse this
    shape directly; keep field names stable across versions or bump
    ``schema_version`` and carry a migration in the hook scripts.

    ``schema_version`` is the only required field — empty sub-rules are
    valid (spawn with no declared capabilities still writes a
    well-formed rules.json so a misbehaving hook can't crash on a
    missing file)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    tools: CompiledToolRules = Field(default_factory=CompiledToolRules)
    bash: CompiledBashRules = Field(default_factory=CompiledBashRules)
    paths: CompiledPathRules = Field(default_factory=CompiledPathRules)
    waivers: CompiledWaiverRules = Field(default_factory=CompiledWaiverRules)
```

Update `compile()` to propagate the merged waivers. Find the function body (around line 107) and extend the `return`:

```python
    merged = merge_declarations(role_capabilities, phase_override)

    tools = merged.tools or CapabilityTools()
    paths = merged.paths or CapabilityPaths()
    bash_params = (
        merged.tool_params.Bash
        if merged.tool_params and merged.tool_params.Bash
        else BashToolParams()
    )
    waivers_source = merged.waivers.can_waive if merged.waivers else []

    return CompiledRules(
        schema_version=SCHEMA_VERSION,
        tools=CompiledToolRules(allowed=list(tools.allowed)),
        bash=CompiledBashRules(deny_patterns=list(bash_params.deny_patterns)),
        paths=CompiledPathRules(
            writable=list(paths.writable),
            readable=list(paths.readable),
            denied=list(paths.denied),
        ),
        waivers=CompiledWaiverRules(can_waive=list(waivers_source)),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: PASS — full file green.

- [ ] **Step 5: Commit**

```bash
git add jig/capability_compiler.py tests/test_capabilities.py
git commit -m "feat(capabilities): compile waivers into CompiledRules, bump schema to 2"
```

---

## Task 4: Relax `RoleConfig.phase_prompt` default

**Files:**
- Modify: `jig/models.py` (line 20)

- [ ] **Step 1: Write a failing test for the relaxation**

Append to `tests/test_capabilities.py` (or create a one-off test at the top-level of the file — either is fine; keep it in the same file to avoid new fixtures):

```python
class TestRoleConfigPromptDefault:
    """``user.yaml`` and similar non-dispatched roles ship without a
    phase_prompt — the field default must permit the empty string."""

    def test_empty_phase_prompt_accepted(self) -> None:
        from jig.models import RoleConfig

        r = RoleConfig(role="user")
        assert r.phase_prompt == ""

    def test_explicit_empty_phase_prompt_accepted(self) -> None:
        from jig.models import RoleConfig

        r = RoleConfig(role="user", phase_prompt="")
        assert r.phase_prompt == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_capabilities.py::TestRoleConfigPromptDefault -v`
Expected: FAIL with pydantic ValidationError — `phase_prompt` is currently a required field with no default.

- [ ] **Step 3: Relax the default in `jig/models.py`**

Edit `jig/models.py` line 20:

```python
class RoleConfig(BaseModel):
    role: str
    phase_prompt: str = ""
    response_prompt: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py::TestRoleConfigPromptDefault -v`
Expected: PASS.

Also run the full test suite to confirm no test assumed `phase_prompt` was required: `uv run pytest tests/ -x -q`.
Expected: all green (or same baseline pass count as before the change).

- [ ] **Step 5: Commit**

```bash
git add jig/models.py tests/test_capabilities.py
git commit -m "refactor(models): relax RoleConfig.phase_prompt default to empty string"
```

---

## Task 5: Ship `jig/defaults/roles/user.yaml`

**Files:**
- Create: `jig/defaults/roles/user.yaml`

- [ ] **Step 1: Write a failing test that loads `user` via the catalog resolver**

Append to `tests/test_capabilities.py`:

```python
class TestUserRoleDefault:
    """``user.yaml`` ships as a pseudo-role carrying default waiver
    authority. It's never dispatched to Claude Code; loading + parsing
    must work so future user-driven waive flows can reuse the same
    capability codepath."""

    def test_user_role_loads_with_default_waiver_capability(
        self, tmp_path: Path
    ) -> None:
        from jig.capabilities import WAIVE_TOKENS
        from jig.persistence import load_role, _jig_dir

        # initialize a minimal project skeleton so load_role's resolver
        # can find shipped defaults by falling through to jig/defaults/
        _jig_dir(tmp_path).mkdir(parents=True, exist_ok=True)

        r = load_role(tmp_path, "user")
        assert r.role == "user"
        assert r.phase_prompt == ""
        assert r.capabilities is not None
        assert r.capabilities.waivers is not None
        assert set(r.capabilities.waivers.can_waive) == WAIVE_TOKENS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_capabilities.py::TestUserRoleDefault -v`
Expected: FAIL — `user.yaml` doesn't exist yet.

- [ ] **Step 3: Create `jig/defaults/roles/user.yaml`**

Create the file with exactly this content:

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

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_capabilities.py::TestUserRoleDefault -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/defaults/roles/user.yaml tests/test_capabilities.py
git commit -m "feat(roles): ship default user.yaml with full waiver capability"
```

---

## Task 6: Update `handle_thread_waive` — `can_waive` gate + new error message

**Files:**
- Modify: `jig/thread_mcp.py` (lines ~461-537 for the handler)
- Modify: `tests/test_thread_mcp.py` (TestThreadWaive class + `_write_config` helper)

- [ ] **Step 1: Delete the `_write_config` helper + update `TestThreadWaive` tests**

In `tests/test_thread_mcp.py`, first delete `_write_config` (lines ~462-477) and its import dependencies. Find and remove:

```python
def _write_config(
    tmp_path: Path, *, waiver_authority: list[str] | None = None
) -> None:
    """Persist a minimal `.jig/config.yaml` so `handle_thread_waive`
    can read ``config.waiver_authority``.
    """
    project = Project(
        id="test-project",
        name="test",
        path=str(tmp_path),
    )
    if waiver_authority is None:
        cfg = Config(project=project)  # use default ["po", "sa", "user"]
    else:
        cfg = Config(project=project, waiver_authority=waiver_authority)
    save_config(tmp_path, cfg)
```

Then check and remove now-unused imports at the top of the file (`Project`, `Config`, `save_config` — only if not used elsewhere in the file; search with `grep` first):

```bash
grep -n "Project\|save_config\|from jig.config" tests/test_thread_mcp.py
```

Remove whichever imports become orphaned.

Next, replace the `TestThreadWaive` class body. Find the class (starting around line 806) and replace its five tests with these rewritten versions — every call to `handle_thread_waive` drops `project_path=tmp_path` and adds `can_waive=frozenset({...})`:

```python
class TestThreadWaive:
    @pytest.mark.asyncio
    async def test_authorized_role_waives(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        result = await handle_thread_waive(
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"objection"}),
            args={
                "objection_id": obj["objection_id"],
                "justification": "shipping for demo, ticket tracks real fix",
            },
        )
        waiver = await threads.get(result["waiver_id"])
        assert isinstance(waiver, Waiver)
        assert waiver.objection_id == obj["objection_id"]
        assert waiver.author == "po"
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert o.waived_by == "po"
        assert o.is_resolved()
        assert not o.is_blocking()
        assert await threads.has_unresolved_blocking(ticket_id) == []

    @pytest.mark.asyncio
    async def test_unauthorized_role_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        with pytest.raises(
            ThreadError,
            match=r"cannot waive objections.*capabilities.waivers.can_waive.*'objection'",
        ):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="dev",
                can_waive=frozenset({"check_failure:warning"}),
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "I promise it's fine",
                },
            )
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert o.waived_by is None
        assert o.is_blocking()

    @pytest.mark.asyncio
    async def test_empty_can_waive_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "x",
                "text": "x",
            },
        )
        with pytest.raises(ThreadError, match="cannot waive objections"):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="dev",
                can_waive=frozenset(),
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "no authority",
                },
            )

    @pytest.mark.asyncio
    async def test_empty_justification_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        with pytest.raises(ValueError, match="justification"):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"objection"}),
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "  ",
                },
            )

    @pytest.mark.asyncio
    async def test_fails_on_already_resolved(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_accept_resolution(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"objection_id": obj["objection_id"]},
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"objection"}),
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "too late",
                },
            )

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_waive(
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"objection"}),
            args={
                "objection_id": obj["objection_id"],
                "justification": "shipping",
            },
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_objection_waived" for m in msgs
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_thread_mcp.py::TestThreadWaive -v`
Expected: FAIL — handler still takes `project_path`, not `can_waive`; error message still says "not authorized".

- [ ] **Step 3: Update `handle_thread_waive` + add `_require_waive_token` helper in `jig/thread_mcp.py`**

Add this helper right after the `class ThreadError` definition (around line 69):

```python
def _require_waive_token(
    token: str,
    *,
    role: str,
    can_waive: frozenset[str],
    subject: str,
) -> None:
    """Raise :class:`ThreadError` if ``token`` is not in ``can_waive``.

    Error names the exact missing token so the operator knows what to
    add to ``capabilities.waivers.can_waive`` in the role YAML. The
    current list is sorted for deterministic output."""

    if token not in can_waive:
        raise ThreadError(
            f"role {role!r} cannot waive {subject} — "
            f"capabilities.waivers.can_waive must include {token!r} "
            f"(current: {sorted(can_waive)})"
        )
```

Replace the entire `handle_thread_waive` function (lines ~461-537) with:

```python
async def handle_thread_waive(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    can_waive: frozenset[str],
    args: dict[str, Any],
) -> dict[str, Any]:
    """Override an Objection via authorized Waiver.

    The Waiver and the original Objection both remain in the thread
    — the audit trail is the point per doc 08 §Waivers. The Objection
    flips to waived-with-reason (``waived_by=sender``), which causes
    ``is_blocking()`` to return False.

    Authorization: ``sender``'s compiled ``can_waive`` set must include
    ``"objection"``. The set is materialised at agent spawn time from
    ``RoleConfig.capabilities.waivers.can_waive`` unioned with any
    phase-level ``capability_overrides`` (doc 16 §Capability policy).

    Required args: ``objection_id``, ``justification``.
    """
    objection_id = args["objection_id"]
    justification = args["justification"]

    if not justification.strip():
        raise ValueError("justification is required")

    _require_waive_token(
        "objection",
        role=sender,
        can_waive=can_waive,
        subject="objections",
    )

    o = await threads.get(objection_id)
    if o is None:
        raise KeyError(f"objection {objection_id!r} not found")
    if not isinstance(o, Objection):
        raise ThreadError(
            f"entry {objection_id!r} is a {o.kind!r}, not an objection"
        )
    if o.is_resolved():
        raise ThreadError(
            f"objection {objection_id!r} is already resolved "
            f"(resolved_by={o.resolved_by!r}, waived_by={o.waived_by!r})"
        )

    w = Waiver(
        ticket_id=o.ticket_id,
        author=sender,
        objection_id=objection_id,
        justification=justification,
    )
    wid = await threads.post(w)
    await threads.update(objection_id, {"waived_by": sender})

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_objection_waived",
                "ticket_id": o.ticket_id,
                "objection_id": objection_id,
                "waiver_id": wid,
                "waived_by": sender,
                "justification": justification,
            },
            topic=f"tickets.{o.ticket_id}",
        )
    )
    return {
        "waiver_id": wid,
        "objection_id": objection_id,
        "waived_by": sender,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_thread_mcp.py::TestThreadWaive -v`
Expected: PASS — all six tests in the class green.

- [ ] **Step 5: Commit**

```bash
git add jig/thread_mcp.py tests/test_thread_mcp.py
git commit -m "feat(thread): gate thread_waive on can_waive capability token"
```

---

## Task 7: Update `handle_thread_waive_check` — severity-aware capability gate

**Files:**
- Modify: `jig/thread_mcp.py` (lines ~570-677)
- Modify: `tests/test_thread_mcp.py` (TestThreadWaiveCheck class + integration test at line ~2402)

- [ ] **Step 1: Update `TestThreadWaiveCheck` tests**

In `tests/test_thread_mcp.py`, replace the full `TestThreadWaiveCheck` class body (starting around line 2176) with the rewritten version — every call drops `project_path=tmp_path` and adds `can_waive=frozenset({...})`:

```python
class TestThreadWaiveCheck:
    @pytest.mark.asyncio
    async def test_waive_by_check_failure_id(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        result = await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"check_failure:required"}),
            args={
                "check_failure_id": fid,
                "justification": "flaky on CI, tracked in follow-up",
            },
        )
        waiver = await threads.get(result["waiver_id"])
        assert isinstance(waiver, Waiver)
        assert waiver.check_failure_id == fid
        assert waiver.objection_id is None
        assert waiver.author == "po"
        assert (
            waiver.justification == "flaky on CI, tracked in follow-up"
        )
        ev = await threads.get(fid)
        assert isinstance(ev, SystemEvent)
        assert ev.waived is True
        assert result["check_name"] == "unit"
        assert result["waived_by"] == "po"
        assert result["check_failure_id"] == fid

    @pytest.mark.asyncio
    async def test_waive_by_ticket_and_check_name(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        old = await _seed_check_failure(
            threads, ticket_id=ticket_id, check_name="unit"
        )
        new = await _seed_check_failure(
            threads, ticket_id=ticket_id, check_name="unit"
        )
        assert old != new
        result = await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"check_failure:required"}),
            args={
                "ticket_id": ticket_id,
                "check_name": "unit",
                "justification": "will fix next sprint",
            },
        )
        assert result["check_failure_id"] == new
        ev_new = await threads.get(new)
        ev_old = await threads.get(old)
        assert isinstance(ev_new, SystemEvent)
        assert isinstance(ev_old, SystemEvent)
        assert ev_new.waived is True
        assert ev_old.waived is False

    @pytest.mark.asyncio
    async def test_unauthorized_sender_refused(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        with pytest.raises(
            ThreadError,
            match=r"cannot waive check failures.*'check_failure:required'",
        ):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                can_waive=frozenset({"check_failure:warning"}),
                args={
                    "check_failure_id": fid,
                    "justification": "I promise it's fine",
                },
            )
        ev = await threads.get(fid)
        assert isinstance(ev, SystemEvent)
        assert ev.waived is False

    @pytest.mark.asyncio
    async def test_severity_routed_into_token(
        self, tmp_path: Path
    ) -> None:
        """Role with only warning-tier waiver authority is denied a
        required-severity waive and allowed a warning-severity one."""
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        required_fid = await _seed_check_failure(
            threads, ticket_id=ticket_id, severity="required"
        )
        warning_fid = await _seed_check_failure(
            threads,
            ticket_id=ticket_id,
            check_name="lint",
            severity="warning",
        )
        with pytest.raises(
            ThreadError,
            match=r"'check_failure:required'",
        ):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                can_waive=frozenset({"check_failure:warning"}),
                args={
                    "check_failure_id": required_fid,
                    "justification": "no authority for required",
                },
            )
        # Warning-tier waive by same role succeeds.
        result = await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            can_waive=frozenset({"check_failure:warning"}),
            args={
                "check_failure_id": warning_fid,
                "justification": "advisory, deferring",
            },
        )
        ev = await threads.get(warning_fid)
        assert isinstance(ev, SystemEvent)
        assert ev.waived is True
        assert result["waived_by"] == "dev"

    @pytest.mark.asyncio
    async def test_empty_justification_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        with pytest.raises(ValueError, match="justification"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"check_failure:required"}),
                args={
                    "check_failure_id": fid,
                    "justification": "   ",
                },
            )

    @pytest.mark.asyncio
    async def test_already_waived_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"check_failure:required"}),
            args={
                "check_failure_id": fid,
                "justification": "first waive",
            },
        )
        with pytest.raises(ThreadError, match="already waived"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"check_failure:required"}),
                args={
                    "check_failure_id": fid,
                    "justification": "second waive",
                },
            )

    @pytest.mark.asyncio
    async def test_non_check_failure_entry_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        note_id = await threads.post(
            Note(ticket_id=ticket_id, author="dev", text="hi")
        )
        with pytest.raises(ThreadError, match="not a check_failure"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"check_failure:required"}),
                args={
                    "check_failure_id": note_id,
                    "justification": "wrong target",
                },
            )

    @pytest.mark.asyncio
    async def test_name_lookup_with_no_match_raises(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(
            ThreadError, match="no unwaived check_failure"
        ):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"check_failure:required"}),
                args={
                    "ticket_id": ticket_id,
                    "check_name": "unit",
                    "justification": "nothing to waive yet",
                },
            )

    @pytest.mark.asyncio
    async def test_missing_args_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(
            ValueError, match="check_failure_id or"
        ):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                can_waive=frozenset({"check_failure:required"}),
                args={"justification": "no target"},
            )

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"check_failure:required"}),
            args={
                "check_failure_id": fid,
                "justification": "known flake",
            },
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        match = [
            m for m in msgs
            if m.payload.get("kind") == "thread_check_failure_waived"
        ]
        assert len(match) == 1
        payload = match[0].payload
        assert payload["check_failure_id"] == fid
        assert payload["check_name"] == "unit"
        assert payload["waived_by"] == "po"
        assert payload["ticket_id"] == ticket_id

    @pytest.mark.asyncio
    async def test_gate_respects_waived_flag(self, tmp_path: Path) -> None:
        """Integration: once a check_failure is waived, the handoff
        gate sees it as non-blocking on the next run."""
        from datetime import datetime, timedelta, timezone

        from jig.check_gate import evaluate_handoff_gate
        from jig.check_results import CheckResult
        from jig.checks import CheckCatalog, CheckSeverity, ScriptedCheck
        from jig.store.check_results import CheckResultsStore

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)

        results = CheckResultsStore(tmp_path / "check_results.jsonl")
        await results.load()
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sha = "deadbeef" * 5
        await results.post(
            CheckResult(
                ticket_id=ticket_id,
                phase="dev",
                check_name="unit",
                check_type="scripted",
                verdict="fail",
                severity=CheckSeverity.REQUIRED,
                started_at=base,
                finished_at=base + timedelta(seconds=1),
                output="",
                commit_sha=sha,
            )
        )
        catalog = CheckCatalog.model_validate(
            {
                "unit": ScriptedCheck(
                    type="scripted",
                    command="true",
                    severity=CheckSeverity.REQUIRED,
                ).model_dump()
            }
        )

        v1 = await evaluate_handoff_gate(
            catalog=catalog,
            results=results,
            threads=threads,
            ticket_id=ticket_id,
            phase="dev",
            required_check_names=["unit"],
        )
        assert v1.passing is False
        assert len(v1.posted_events) == 1
        fid = v1.posted_events[0]

        await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            can_waive=frozenset({"check_failure:required"}),
            args={
                "check_failure_id": fid,
                "justification": "will fix in follow-up",
            },
        )

        v2 = await evaluate_handoff_gate(
            catalog=catalog,
            results=results,
            threads=threads,
            ticket_id=ticket_id,
            phase="dev",
            required_check_names=["unit"],
        )
        assert v2.passing is True
        assert v2.posted_events == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_thread_mcp.py::TestThreadWaiveCheck -v`
Expected: FAIL — handler still takes `project_path`; severity-routed token logic doesn't exist yet.

- [ ] **Step 3: Update `handle_thread_waive_check` in `jig/thread_mcp.py`**

Replace the full function body (lines ~570-677) with the capability-gated, order-inverted version. The critical change: lookup the event FIRST, then build the severity-aware token, then authorize:

```python
async def handle_thread_waive_check(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    can_waive: frozenset[str],
    args: dict[str, Any],
) -> dict[str, Any]:
    """Waive a failing check_failure with justification.

    Scoped to a specific ``check_failure`` SystemEvent — either by the
    entry id (``check_failure_id``) or by (``ticket_id``, ``check_name``)
    which resolves to the most recent unwaived failure for that name.

    Posts a ``Waiver`` entry with ``check_failure_id`` and flips the
    target SystemEvent's ``waived`` flag to True so the check gate
    (``jig.check_gate.evaluate_handoff_gate``) stops treating the
    failure as blocking.

    Authorization: ``sender``'s compiled ``can_waive`` set must include
    the severity-qualified token ``"check_failure:<severity>"``. The
    event must be looked up before the auth check because severity
    drives the token — an unauthorized caller passing a bogus id gets
    a KeyError ("not found") rather than a ThreadError. This is
    acceptable because any caller with thread-read access can confirm
    existence via other tools. Defensive fail-closed: if the
    SystemEvent's ``check_severity`` is ``None``, the constructed
    token is ``"check_failure:None"``, which is not in
    :data:`jig.capabilities.WAIVE_TOKENS` and matches nothing.

    Required args: ``justification`` plus one of
    (``check_failure_id``) or (``ticket_id``, ``check_name``).
    """
    justification = args["justification"]
    if not justification.strip():
        raise ValueError("justification is required")

    check_failure_id = args.get("check_failure_id")
    ticket_id = args.get("ticket_id")
    check_name = args.get("check_name")

    if check_failure_id:
        ev = await threads.get(check_failure_id)
        if ev is None:
            raise KeyError(
                f"check_failure {check_failure_id!r} not found"
            )
        if not isinstance(ev, SystemEvent) or ev.event_type != "check_failure":
            raise ThreadError(
                f"entry {check_failure_id!r} is not a check_failure event"
            )
    elif ticket_id and check_name:
        if await tickets.get(ticket_id) is None:
            raise KeyError(f"ticket {ticket_id} not found")
        ev = await _latest_check_failure_for_name(
            threads=threads,
            ticket_id=ticket_id,
            check_name=check_name,
        )
        if ev is None:
            raise ThreadError(
                f"no unwaived check_failure for check {check_name!r} "
                f"on ticket {ticket_id!r}"
            )
        check_failure_id = ev.id
    else:
        raise ValueError(
            "supply either check_failure_id or (ticket_id, check_name)"
        )

    if ev.waived:
        raise ThreadError(
            f"check_failure {check_failure_id!r} is already waived"
        )

    # Severity drives the token — fail closed if ``check_severity`` is
    # None (type permits it though practice populates it).
    token = f"check_failure:{ev.check_severity}"
    _require_waive_token(
        token,
        role=sender,
        can_waive=can_waive,
        subject=f"check failures of severity {ev.check_severity!r}",
    )

    w = Waiver(
        ticket_id=ev.ticket_id,
        author=sender,
        check_failure_id=check_failure_id,
        justification=justification,
    )
    wid = await threads.post(w)
    await threads.update(check_failure_id, {"waived": True})

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_check_failure_waived",
                "ticket_id": ev.ticket_id,
                "check_failure_id": check_failure_id,
                "check_name": ev.check_name,
                "waiver_id": wid,
                "waived_by": sender,
                "justification": justification,
            },
            topic=f"tickets.{ev.ticket_id}",
        )
    )
    return {
        "waiver_id": wid,
        "check_failure_id": check_failure_id,
        "check_name": ev.check_name,
        "waived_by": sender,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_thread_mcp.py::TestThreadWaiveCheck -v`
Expected: PASS — all tests in the class green.

- [ ] **Step 5: Remove unused `load_config` import from `jig/thread_mcp.py`**

Confirm `load_config` is no longer used:

```bash
grep -n "load_config" jig/thread_mcp.py
```

Expected output: only the import at line 33. Delete that line:

```python
# DELETE this line from jig/thread_mcp.py:
from jig.config import load_config
```

- [ ] **Step 6: Run full thread_mcp test suite to verify nothing else broke**

Run: `uv run pytest tests/test_thread_mcp.py -v`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add jig/thread_mcp.py tests/test_thread_mcp.py
git commit -m "feat(thread): gate thread_waive_check on severity-qualified token"
```

---

## Task 8: Delete `Config.waiver_authority` field + update thread.py comment

**Files:**
- Modify: `jig/config.py` (lines 114-120)
- Modify: `jig/thread.py` (line ~178 comment)

- [ ] **Step 1: Delete the field from `Config`**

Edit `jig/config.py` — remove lines 114-120 (the `waiver_authority` field + its comment):

```python
# In class Config(BaseModel), DELETE these lines:
    # Phase 4D: roles authorized to waive Objections per doc 08. Full
    # capability-policy enforcement lands with Phase 5 (doc 16); for
    # now, a flat role-name list is enough — the default mirrors the
    # ownership defaults and matches "humans + senior roles override".
    waiver_authority: list[str] = Field(
        default_factory=lambda: ["po", "sa", "user"]
    )
```

Leaving the class ending at `self_approval: SelfApprovalPolicy = "warn"`.

- [ ] **Step 2: Update the `Waiver` docstring in `jig/thread.py`**

Find the comment around line 178:

```python
    Authorization is enforced at post-time: today against
    ``config.waiver_authority``; Phase 5 Task H flips this to the
    capability layer. The Waiver itself and its target both stay in
    the thread — the audit trail is the point (doc 08 §Waivers leave
```

Replace with:

```python
    Authorization is enforced at post-time against the author's
    compiled ``capabilities.waivers.can_waive`` set (doc 16 §Capability
    policy). The Waiver itself and its target both stay in the thread
    — the audit trail is the point (doc 08 §Waivers leave
```

- [ ] **Step 3: Run full test suite to catch any remaining references**

Run: `uv run pytest tests/ -x -q`
Expected: PASS. If a test fails on `AttributeError: 'Config' object has no attribute 'waiver_authority'`, it's a test that still writes a stale `_write_config(waiver_authority=...)` — delete/update that test's stale scaffolding. All such scaffolding was removed in Task 6, but re-running is a safety net.

Also do a plain grep:

```bash
grep -rn "waiver_authority" jig/ tests/
```

Expected: no matches (beyond code being explicitly deleted by this plan; the spec and implementation-plan.md will be updated in Task 14).

- [ ] **Step 4: Commit**

```bash
git add jig/config.py jig/thread.py
git commit -m "refactor(config): delete Config.waiver_authority (retired for capability)"
```

---

## Task 9: Catalog validation — delete old block, add unknown-token check

**Files:**
- Modify: `jig/catalog.py`
- Modify: `tests/test_catalog_validation.py`

- [ ] **Step 1: Delete `TestWaiverAuthorityReferences` from the test file**

In `tests/test_catalog_validation.py`, delete the entire class (lines 497-529):

```python
# DELETE this whole class:
class TestWaiverAuthorityReferences:
    """Phase 4 Task H: ``config.waiver_authority`` must name known roles."""

    def test_unknown_waiver_role_fails(self, initialized_project: Path) -> None:
        ...
    # plus the other three methods
```

- [ ] **Step 2: Write failing tests for the new unknown-token check**

Append to `tests/test_catalog_validation.py`:

```python
class TestWaiverCapabilityValidation:
    """Phase 5 Task H: ``capabilities.waivers.can_waive`` entries must
    be recognised tokens. Unknown tokens fail validation outright."""

    def test_unknown_token_on_role_fails(
        self, initialized_project: Path
    ) -> None:
        from jig.capabilities import CapabilityDeclaration, CapabilityWaivers
        from jig.persistence import save_role
        from jig.models import RoleConfig

        save_role(
            initialized_project,
            RoleConfig(
                role="dev",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    waivers=CapabilityWaivers(
                        can_waive=["objection", "typo:bogus"]
                    )
                ),
            ),
        )
        with pytest.raises(CatalogError, match="typo:bogus"):
            validate_catalog(initialized_project)

    def test_known_tokens_on_role_pass(
        self, initialized_project: Path
    ) -> None:
        from jig.capabilities import (
            CapabilityDeclaration,
            CapabilityWaivers,
            WAIVE_TOKENS,
        )
        from jig.persistence import save_role
        from jig.models import RoleConfig

        save_role(
            initialized_project,
            RoleConfig(
                role="dev",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    waivers=CapabilityWaivers(
                        can_waive=sorted(WAIVE_TOKENS)
                    )
                ),
            ),
        )
        validate_catalog(initialized_project)

    def test_unknown_token_on_phase_override_fails(
        self, initialized_project: Path
    ) -> None:
        from jig.capabilities import CapabilityDeclaration, CapabilityWaivers
        from jig.persistence import save_workflow
        from jig.models import PhaseConfig, WorkflowConfig

        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="broken",
                phases=[
                    PhaseConfig(
                        name="p",
                        role="dev",
                        capability_overrides=CapabilityDeclaration(
                            waivers=CapabilityWaivers(
                                can_waive=["not-a-real-token"]
                            )
                        ),
                    )
                ],
            ),
        )
        with pytest.raises(CatalogError, match="not-a-real-token"):
            validate_catalog(initialized_project)
```

Also: remove the now-unused `_write_config` helper at the top of `tests/test_catalog_validation.py` IF it was the only caller; confirm with grep first:

```bash
grep -n "_write_config" tests/test_catalog_validation.py
```

If `_write_config` remains referenced elsewhere in the file (other classes), leave it alone. If the deleted `TestWaiverAuthorityReferences` was the sole caller, delete the helper + its stale `Config` / `save_config` / `Project` imports.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_catalog_validation.py::TestWaiverCapabilityValidation -v`
Expected: the `unknown_token` tests FAIL (validation doesn't check waiver tokens yet). The `known_tokens_pass` test may pass depending on other validation — we're about to tighten catalog validation.

- [ ] **Step 4: Delete the old `waiver_authority` block and extend `_validate_capabilities`**

In `jig/catalog.py`, first delete the old block (lines 231-248):

```python
# DELETE this block:
    # Phase 4H: config.waiver_authority must reference known roles.
    # ``"user"`` is a sentinel (the operating human) and is always
    # allowed; so are the project-level role names declared under
    # ``config.roles`` (po, sa, or extras the project may add).
    if config is not None:
        project_level_roles = set(config.roles.model_dump().keys())
        for role_name in config.waiver_authority:
            if role_name == "user":
                continue
            if role_name in project_level_roles:
                continue
            if role_name not in known_roles:
                fail(
                    f"config.waiver_authority references unknown role "
                    f"{role_name!r} (known project-level roles: "
                    f"{sorted(project_level_roles)}; known agent roles: "
                    f"{sorted(known_roles)})"
                )
```

Then update the import at the top of `jig/catalog.py` (line 40) to include the new symbol:

```python
from jig.capabilities import (
    CapabilityDeclaration,
    WAIVE_TOKENS,
    is_known_tool,
    is_known_waive_token,
)
```

Finally, extend `_validate_capabilities` (around line 375) to check `waivers.can_waive`. Add a new block after the `paths.*` validation and before the `return errors`:

```python
    # waivers.can_waive (Phase 5 Task H). Unknown tokens silently
    # deauthorize at the MCP handler, so fail loud at load time.
    if decl.waivers is not None:
        for token in decl.waivers.can_waive:
            if not is_known_waive_token(token):
                errors.append(
                    f"waivers.can_waive: unknown token {token!r} "
                    f"(known: {sorted(WAIVE_TOKENS)})"
                )

    return errors
```

- [ ] **Step 5: Run full catalog-validation tests**

Run: `uv run pytest tests/test_catalog_validation.py -v`
Expected: PASS. The new `TestWaiverCapabilityValidation` class is green; the deleted `TestWaiverAuthorityReferences` is gone.

- [ ] **Step 6: Commit**

```bash
git add jig/catalog.py tests/test_catalog_validation.py
git commit -m "feat(catalog): validate can_waive tokens, retire waiver_authority check"
```

---

## Task 10: MCP server — plumb `can_waive` through `create_agent_mcp_server`

**Files:**
- Modify: `jig/mcp_server.py` (function signature + two handler-forwarding `@tool` blocks)

- [ ] **Step 1: Write a failing test asserting `can_waive` threads through**

Append to `tests/test_capabilities.py` (or choose the nearest existing file — `tests/test_agent_streaming.py::TestMaterializeCapabilityPolicy` is the home for integration-level plumbing; we'll handle it there in Task 11. For this unit-level test, target `mcp_server` directly):

```python
class TestMcpServerWaiverPlumbing:
    """``create_agent_mcp_server`` accepts ``can_waive`` and forwards
    it into the two waiver handlers. The server object itself doesn't
    expose the frozenset — we assert the kwarg is accepted without
    errors and that an end-to-end call path uses it (deferred to the
    streaming integration test in Task 11)."""

    def test_create_agent_mcp_server_accepts_can_waive(
        self, tmp_path: Path
    ) -> None:
        import asyncio

        from jig.store import MessageBus
        from jig.store.checkpoints import CheckpointStore
        from jig.store.memory import MemoryStore
        from jig.store.threads import ThreadStore
        from jig.store.tickets import TicketStore
        from jig.mcp_server import create_agent_mcp_server
        from jig.models import RoleConfig

        async def build() -> None:
            tickets = TicketStore(tmp_path / "tickets.jsonl")
            threads = ThreadStore(tmp_path / "threads.jsonl")
            memory = MemoryStore(tmp_path / "memory.jsonl")
            checkpoints = CheckpointStore(tmp_path / "checkpoints.jsonl")
            bus = MessageBus()
            await tickets.load()
            await threads.load()
            await memory.load()
            await checkpoints.load()

            server = create_agent_mcp_server(
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                agent_role="dev",
                agent_cfg=RoleConfig(role="dev", phase_prompt="x"),
                worktree_path=tmp_path,
                project_path=tmp_path,
                can_waive=frozenset({"objection"}),
            )
            assert server is not None

        asyncio.run(build())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_capabilities.py::TestMcpServerWaiverPlumbing -v`
Expected: FAIL — `TypeError: create_agent_mcp_server() got an unexpected keyword argument 'can_waive'`.

- [ ] **Step 3: Update `create_agent_mcp_server` to accept + forward `can_waive`**

In `jig/mcp_server.py`, modify the signature (around line 19):

```python
def create_agent_mcp_server(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    agent_role: str,
    agent_cfg: RoleConfig,
    worktree_path: Path,
    project_path: Path,
    valid_roles: frozenset[str] = frozenset(),
    package_manager: str = "",
    checkpoints: CheckpointStore | None = None,
    phase_name: str = "",
    can_waive: frozenset[str] = frozenset(),
):
```

Update the `thread_waive` tool block (around lines 282-298):

```python
    @tool(
        "thread_waive",
        "Override an objection with explicit justification. Authorization is "
        "enforced against your role's compiled "
        "capabilities.waivers.can_waive — if the token 'objection' is not "
        "present, this fails. The waiver and the original objection both "
        "stay in the thread as audit trail.",
        {"objection_id": str, "justification": str},
    )
    async def thread_waive(args):
        result = await thread_mcp.handle_thread_waive(
            threads=threads,
            bus=bus,
            sender=agent_role,
            can_waive=can_waive,
            args=args,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
```

Update the `thread_waive_check` tool block (around lines 300-339):

```python
    @tool(
        "thread_waive_check",
        "Waive a failing check with justification. Pass either "
        "check_failure_id (targets a specific check_failure SystemEvent) "
        "or ticket_id+check_name (resolves to the most recent unwaived "
        "failure for that check). Authorization is enforced against your "
        "role's compiled capabilities.waivers.can_waive — the required "
        "token is 'check_failure:<severity>' where severity comes from "
        "the failure event. The waiver and the underlying check_failure "
        "event both remain in the thread; the gate stops treating the "
        "failure as blocking.",
        {
            "justification": str,
            "check_failure_id": str,
            "ticket_id": str,
            "check_name": str,
        },
    )
    async def thread_waive_check(args):
        forwarded = {
            k: v for k, v in args.items()
            if k in {
                "justification",
                "check_failure_id",
                "ticket_id",
                "check_name",
            }
            and v
        }
        result = await thread_mcp.handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender=agent_role,
            can_waive=can_waive,
            args=forwarded,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_capabilities.py::TestMcpServerWaiverPlumbing -v`
Expected: PASS.

Also run the broader test to confirm mcp_server + thread_mcp still behave: `uv run pytest tests/test_mcp_server.py tests/test_thread_mcp.py -v` (if `test_mcp_server.py` exists; otherwise just `tests/test_thread_mcp.py -v`).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_server.py tests/test_capabilities.py
git commit -m "feat(mcp): thread can_waive capability through create_agent_mcp_server"
```

---

## Task 11: Agent — thread compiled `can_waive` from policy into MCP server

**Files:**
- Modify: `jig/agent.py` (`_materialize_capability_policy` return shape + `run_agent` call)
- Test: `tests/test_agent_streaming.py`

- [ ] **Step 1: Write a failing integration test**

Append to `tests/test_agent_streaming.py` (inside `TestMaterializeCapabilityPolicy` if the class exists at line ~169 per the earlier inspection; otherwise create a new test class at the bottom of the file):

```python
class TestCompiledWaiverPlumbing:
    """Phase 5 Task H: the compiled ``can_waive`` set from role +
    phase capabilities is visible to the orchestrator-side waiver
    MCP handlers. This test exercises the compile-at-spawn path
    without standing up Claude Code."""

    def test_compiled_can_waive_from_role_base(self) -> None:
        from jig.capabilities import (
            CapabilityDeclaration,
            CapabilityWaivers,
        )
        from jig.capability_compiler import compile as compile_capabilities

        role_decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        rules = compile_capabilities(role_decl, None)
        assert frozenset(rules.waivers.can_waive) == frozenset({"objection"})

    def test_compiled_can_waive_unions_phase_override(self) -> None:
        from jig.capabilities import (
            CapabilityDeclaration,
            CapabilityWaivers,
        )
        from jig.capability_compiler import compile as compile_capabilities

        role_decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        phase_decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        rules = compile_capabilities(role_decl, phase_decl)
        assert frozenset(rules.waivers.can_waive) == frozenset(
            {"objection", "check_failure:warning"}
        )

    def test_compiled_can_waive_empty_when_undeclared(self) -> None:
        from jig.capability_compiler import compile as compile_capabilities

        rules = compile_capabilities(None, None)
        assert rules.waivers.can_waive == []
```

Additionally, to prove the compiled set actually flows into the MCP server, add:

```python
    def test_can_waive_forwarded_to_mcp_server(
        self, tmp_path: Path
    ) -> None:
        """Whole-path check: compile returns a can_waive list; the MCP
        server factory accepts that list as a frozenset."""
        import asyncio

        from jig.capabilities import (
            CapabilityDeclaration,
            CapabilityWaivers,
        )
        from jig.capability_compiler import compile as compile_capabilities
        from jig.mcp_server import create_agent_mcp_server
        from jig.models import RoleConfig
        from jig.store import MessageBus
        from jig.store.checkpoints import CheckpointStore
        from jig.store.memory import MemoryStore
        from jig.store.threads import ThreadStore
        from jig.store.tickets import TicketStore

        role_decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        rules = compile_capabilities(role_decl, None)
        can_waive = frozenset(rules.waivers.can_waive)

        async def build() -> None:
            tickets = TicketStore(tmp_path / "tickets.jsonl")
            threads = ThreadStore(tmp_path / "threads.jsonl")
            memory = MemoryStore(tmp_path / "memory.jsonl")
            checkpoints = CheckpointStore(tmp_path / "checkpoints.jsonl")
            bus = MessageBus()
            await tickets.load()
            await threads.load()
            await memory.load()
            await checkpoints.load()

            server = create_agent_mcp_server(
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                agent_role="dev",
                agent_cfg=RoleConfig(
                    role="dev",
                    phase_prompt="x",
                    capabilities=role_decl,
                ),
                worktree_path=tmp_path,
                project_path=tmp_path,
                can_waive=can_waive,
            )
            assert server is not None

        asyncio.run(build())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_streaming.py::TestCompiledWaiverPlumbing -v`
Expected: PASS on the three pure-compile tests (Task 3 already shipped the compiler support) and PASS on the fourth (Task 10 shipped the MCP server kwarg).

If they PASS already (they should, given Tasks 3 + 10 did the underlying work), **this task graduates to a smoke test** — no agent.py changes needed at this layer because the compiled waivers are already in `CompiledRules`, and the call path from `run_agent` to `create_agent_mcp_server` already flows via `ctx` and compiled rules.

BUT: `run_agent` itself doesn't currently thread `can_waive` into `create_agent_mcp_server`. That's the actual missing wire. Confirm with:

```bash
grep -n "create_agent_mcp_server" jig/agent.py
```

You should see the call site (around line 331). Inspect: does it pass `can_waive`? No — so Step 3 wires it.

- [ ] **Step 3: Thread compiled `can_waive` into `run_agent`'s MCP construction**

In `jig/agent.py`, `_materialize_capability_policy` currently returns `Path | None` (the policy directory). It needs to also surface the compiled `can_waive` frozenset even when `sandbox_available()` is False (because orchestrator-side handler enforcement happens regardless of sandbox). Restructure.

First, change the return type of `_materialize_capability_policy` to a small dataclass so it can return both pieces of information. Add a helper type near the other module-level helpers (below the top-level imports):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _CapabilityMaterialization:
    """Return value of :func:`_materialize_capability_policy`. Splits
    out the sandbox policy dir (bind-mounted for hook scripts) from
    the orchestrator-side waiver set (consulted by thread_mcp handlers
    regardless of sandbox availability)."""

    policy_dir: Path | None
    can_waive: frozenset[str]
```

Then rewrite `_materialize_capability_policy` to return this struct. Replace the function body (lines ~153-239) with:

```python
def _materialize_capability_policy(
    ctx: AgentSpawnContext,
) -> _CapabilityMaterialization:
    """Compile capability policy for a spawn and (when the sandbox is
    available) write the hook enforcement artefacts.

    Returns both the policy directory (for bind-mount — may be ``None``
    when no declaration exists or sandbox is unavailable) and the
    compiled ``can_waive`` frozenset. Waiver authorization runs
    orchestrator-side (in :mod:`jig.thread_mcp`), so the frozenset is
    surfaced independently of whether sandbox hooks were materialised.

    Location of the hook artefacts (when emitted):

    * ``rules.json`` → ``<project>/.jig/runtime/<ticket_id>/policy/``
    * ``.claude/settings.json`` → ``<worktree>/.claude/settings.json``

    Materialisation failures log + return ``policy_dir=None`` but keep
    the compiled ``can_waive`` — a filesystem glitch shouldn't
    deauthorize the agent's waiver tokens, which aren't enforced via
    the hooks anyway.
    """
    role_caps = ctx.role_cfg.capabilities
    phase_caps = ctx.phase.capability_overrides if ctx.phase else None

    if role_caps is None and phase_caps is None:
        return _CapabilityMaterialization(
            policy_dir=None, can_waive=frozenset()
        )

    rules = compile_capabilities(role_caps, phase_caps)
    can_waive = frozenset(rules.waivers.can_waive)

    if not sandbox_available():
        _logger.debug(
            "skipping capability materialisation for %s on %s: "
            "no sandbox available (hook paths are container-absolute)",
            ctx.role,
            ctx.ticket.id,
        )
        return _CapabilityMaterialization(
            policy_dir=None, can_waive=can_waive
        )

    try:
        policy_dir = (
            ctx.project.path_or_default()
            / ".jig"
            / "runtime"
            / ctx.ticket.id
            / "policy"
        )
        rules_path, settings_path = materialize_capabilities(
            rules,
            worktree_path=ctx.worktree_path,
            policy_dir=policy_dir,
        )
        _logger.info(
            "capability policy materialised for %s on %s: rules=%s settings=%s",
            ctx.role,
            ctx.ticket.id,
            rules_path,
            settings_path,
        )
        return _CapabilityMaterialization(
            policy_dir=policy_dir, can_waive=can_waive
        )
    except OSError:
        _logger.exception(
            "capability materialisation failed for %s on %s",
            ctx.role,
            ctx.ticket.id,
        )
        return _CapabilityMaterialization(
            policy_dir=None, can_waive=can_waive
        )
```

Update the call site in `run_agent` (around lines 331-344 + 358). The call currently looks like:

```python
    mcp_server = create_agent_mcp_server(
        tickets=ctx.tickets,
        threads=ctx.threads,
        memory=ctx.memory,
        bus=ctx.bus,
        agent_role=ctx.role,
        agent_cfg=ctx.role_cfg,
        worktree_path=ctx.worktree_path,
        project_path=ctx.project.path_or_default(),
        valid_roles=frozenset(r.role for r in all_roles),
        package_manager=ctx.project.package_manager,
        checkpoints=ctx.checkpoints,
        phase_name=ctx.phase.name if ctx.phase else "",
    )
    ...
    policy_dir = _materialize_capability_policy(ctx)
```

Move the `_materialize_capability_policy` call **before** `create_agent_mcp_server` so its result is available:

```python
    cap = _materialize_capability_policy(ctx)

    all_roles = list_roles(ctx.project.path_or_default())
    mcp_server = create_agent_mcp_server(
        tickets=ctx.tickets,
        threads=ctx.threads,
        memory=ctx.memory,
        bus=ctx.bus,
        agent_role=ctx.role,
        agent_cfg=ctx.role_cfg,
        worktree_path=ctx.worktree_path,
        project_path=ctx.project.path_or_default(),
        valid_roles=frozenset(r.role for r in all_roles),
        package_manager=ctx.project.package_manager,
        checkpoints=ctx.checkpoints,
        phase_name=ctx.phase.name if ctx.phase else "",
        can_waive=cap.can_waive,
    )
```

Then find any remaining `policy_dir = _materialize_capability_policy(ctx)` statement and replace it, plus any subsequent usage of `policy_dir` — route through `cap.policy_dir` instead:

```bash
grep -n "policy_dir\|_materialize_capability_policy" jig/agent.py
```

Replace all `policy_dir` references that come from this function with `cap.policy_dir`, and delete the second `_materialize_capability_policy(ctx)` call since the rewrite hoisted it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_streaming.py -v`
Expected: PASS — the new plumbing tests and all existing streaming tests green.

Also run the whole suite once to catch anything indirect: `uv run pytest tests/ -x -q`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/agent.py tests/test_agent_streaming.py
git commit -m "feat(agent): thread compiled can_waive into MCP server at spawn"
```

---

## Task 12: Lint, format, and full-suite green check

**Files:** (none directly — verification only)

- [ ] **Step 1: Run ruff format**

Run: `uv run ruff format jig/ tests/`
Expected: possibly reformats a few lines; commit those separately.

- [ ] **Step 2: Run ruff check**

Run: `uv run ruff check jig/ tests/`
Expected: clean (no findings). If any imports are unused (e.g. residual `load_config`, `save_config`, `Project` in tests), ruff will flag them — delete as indicated.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS — every test green. Any lingering failure is a pre-existing unrelated issue; if it touches waivers or capabilities, fix inline before committing.

- [ ] **Step 4: Commit any format / lint adjustments**

```bash
git add -u
git diff --cached --stat
git commit -m "style: ruff format + remove dead imports after waiver retirement"
```

(If nothing to commit, skip this step.)

---

## Task 13: Documentation updates

**Files:**
- Modify: `docs/08-threads.md`
- Modify: `docs/16-policy-and-enforcement.md`
- Modify: `docs/v2.0/implementation-plan.md`

- [ ] **Step 1: Update `docs/08-threads.md`**

Find the section describing `thread_waive` (search for `thread_waive` or `waiver_authority`) and update the authorization sentence to reference capabilities instead. For example:

Before:
> Authorization: `sender` must be in `config.waiver_authority`.

After:
> Authorization: `sender`'s role must declare `capabilities.waivers.can_waive` including `"objection"` (for `thread_waive`) or `"check_failure:<severity>"` (for `thread_waive_check`). Declared on the role template or broadened via phase `capability_overrides`.

Search and update with:

```bash
grep -n "waiver_authority\|config\.waiver" docs/08-threads.md
```

Replace each occurrence per the above pattern.

- [ ] **Step 2: Update `docs/16-policy-and-enforcement.md`**

Find the existing `Capability policy` section (or an analogous heading). Add a new subsection after the `paths` policy description:

```markdown
### Waiver authority

`capabilities.waivers.can_waive` is a flat list of string tokens that
authorize the role to post Waivers against specific thread entries.
Recognised tokens:

- `"objection"` — any `Objection` entry.
- `"check_failure:required"` — `SystemEvent(event_type="check_failure")`
  with `check_severity == "required"`.
- `"check_failure:warning"` — same, severity `"warning"`.

Merge semantics: phase `capability_overrides` union with the role
template's base, same as the other list fields — phase overrides can
broaden waiver authority but never narrow it. Unknown tokens fail
`jig validate` at load time.

The MCP tools `thread_waive` and `thread_waive_check` enforce these
tokens at call time. Enforcement runs in the orchestrator process,
not in the sandbox — the hook scripts ignore the `waivers` block in
`rules.json` (it's present for completeness of the compiled
artefact, not for sandbox-side gating).
```

- [ ] **Step 3: Update `docs/v2.0/implementation-plan.md`**

Run: `grep -n "Task H\|waiver_authority\|waiver-capability" docs/v2.0/implementation-plan.md`

Find the Task H section (around lines 1521-1538) and:

1. Check the four completion boxes (`[ ]` → `[x]`).
2. Strike through the migration line. For the migration item (line ~1534-1536), replace with a note that there was no migration — no legacy users at retirement time:

Before:
```markdown
- [ ] Migration: `config.yaml` `waiver_authority` continues
      to load for a cycle but emits a deprecation warning
      pointing at the new capability key.
```

After:
```markdown
- [x] ~~Migration path for legacy `config.yaml`~~ — retired
      outright; project has no legacy users at the time of
      this change, so no deprecation cycle needed.
```

Also find and update the standalone prose references noted in the earlier grep output. For each:

- Line ~959 (Phase 4 description): add a trailing sentence `Retired in Phase 5 Task H.`
- Line ~1066 (Phase 4 validation): strike the `waiver_authority` reference and point at the new `_validate_capabilities` check.
- Line ~1073 (Phase 4 tests): retain — describes the Phase 4 behavior before retirement.
- Line ~1100 (Phase 4 acceptance criteria): strike the `waiver_authority` reference.
- Lines ~1149-1151 (Deferred-decisions list): strike through as completed.
- Lines ~1208-1210 (Phase 5 carry-over): strike through as completed.
- Lines ~1431-1434 (`thread_check_failure_waived` description): replace `config.waiver_authority` with `capabilities.waivers.can_waive`.
- Lines ~1744-1745 (Phase 5 summary): replace `waiver_authority reads from capability policy; flat list in config.yaml still loads with a deprecation warning.` with `waivers are declared under capabilities.waivers.can_waive on role templates and phase overrides; the Phase 4 flat list is retired.`

Exact replacements for each are: find the old prose, apply the strikethrough or replacement as described. Use:

```bash
grep -n "waiver_authority\|waive.*capability\|waiver authority" docs/v2.0/implementation-plan.md
```

to locate each one.

- [ ] **Step 4: Commit docs**

```bash
git add docs/08-threads.md docs/16-policy-and-enforcement.md docs/v2.0/implementation-plan.md
git commit -m "docs(phase5h): retire waiver_authority, document waivers capability"
```

---

## Task 14: Push and open PR

- [ ] **Step 1: Final verification**

Run in parallel:

```bash
uv run ruff check jig/ tests/
uv run ruff format --check jig/ tests/
uv run pytest tests/ -q
```

Expected: all three commands exit 0.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 3: Open the PR**

(User-confirmation required — ask before running `gh pr create` since this is a shared-infra action per CLAUDE.md.)

```bash
gh pr create --title "feat(phase5h): waiver authority as capability" --body "$(cat <<'EOF'
## Summary
- Retires `config.waiver_authority` in favor of `capabilities.waivers.can_waive` on role templates, with phase-level overrides that union the same way other capability lists do.
- `thread_waive` gates on `"objection"`; `thread_waive_check` gates on `"check_failure:<severity>"` — severity routed into the token from the failure event, enforcing warning-vs-required-tier waiver authority.
- Bumps `CompiledRules.schema_version` to 2; ships `jig/defaults/roles/user.yaml` for future user-driven waive flows.

## Test plan
- [ ] `uv run pytest tests/ -v` — full suite green
- [ ] `uv run ruff check jig/ tests/` — no findings
- [ ] `uv run ruff format --check jig/ tests/` — no changes
- [ ] Sanity: inspect a materialised `rules.json` and confirm `schema_version: 2` and the `waivers` block.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes on task dependencies

- **Tasks 1-3** are additive and order-dependent: `CapabilityWaivers` must exist before `merge_declarations` references it; `merge_declarations` must union waivers before `compile()` can propagate them.
- **Task 4** (relax `phase_prompt`) enables **Task 5** (`user.yaml` has empty prompt).
- **Tasks 6 and 7** both change handler signatures; they deliberately update tests + implementation together since signature changes always break fixtures in lockstep.
- **Task 8** (delete `Config.waiver_authority`) must come AFTER 6 and 7 because the field is still read by the handlers before those tasks land.
- **Task 9** (catalog validation) must come AFTER 8 because the field deletion removes the block this task is replacing.
- **Task 10** (MCP server plumbing) depends on 6+7 (handler signatures are what the server forwards into).
- **Task 11** (agent.py plumbing) depends on 3 (compiler surfaces `waivers`) and 10 (MCP server accepts the kwarg).
- **Task 12** is verification-only.
- **Task 13** documents the delivered state.
- **Task 14** ships.

If stepping through sequentially: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14.
