"""Capability compilation + pre-spawn materialization (Phase 5 Task F,
doc 16 §Hook compilation at spawn time).

``compile`` takes a role's base declaration + a phase override and
returns a ``CompiledRules`` — the enforcement-side shape the hook
scripts will read. ``materialize`` writes two files:

* ``rules.json`` — the compiled ruleset keyed under a stable filename
  the Task G hook scripts look for.
* ``.claude/settings.json`` — Claude Code's hook registration, pointing
  each relevant tool event at the matching ``/jig/bin/check-*`` script.

Only hooks whose rules are actually populated get registered — a spawn
with no ``Bash.deny_patterns`` doesn't register ``check-bash``, which
keeps Claude Code's hook set minimal and makes denials easier to
attribute to a specific rule.

The module is pure Python with no jig imports beyond
``jig.capabilities``. That keeps it testable in isolation and lets the
orchestrator call it from anywhere in the spawn path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.capabilities import (
    BashToolParams,
    CapabilityDeclaration,
    CapabilityPaths,
    CapabilityTools,
    merge_declarations,
)

# Compiled-rules schema version. Bump on breaking changes to
# ``CompiledRules`` so stale hook scripts can fail loud rather than
# silently misinterpret fields.
SCHEMA_VERSION: int = 2

# Sandbox-absolute path where ``rules.json`` is bind-mounted for the
# hook scripts to read. Matches doc 16 §Hook compilation at spawn time.
SANDBOX_RULES_PATH: str = "/jig/policy/rules.json"

# Bind-mount base for the hook scripts themselves (Task G ships them).
SANDBOX_HOOK_BIN: str = "/jig/bin"


class CompiledToolRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: list[str] = []


class CompiledBashRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deny_patterns: list[str] = []


class CompiledPathRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    writable: list[str] = []
    readable: list[str] = []
    denied: list[str] = []


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


def compile(
    role_capabilities: CapabilityDeclaration | None,
    phase_override: CapabilityDeclaration | None,
) -> CompiledRules:
    """Merge the role's declaration with the phase override and return
    the enforcement-ready ``CompiledRules``.

    Pure function: no I/O, no store access. Callers supply the
    declarations they've already loaded. The merge rules live in
    ``jig.capabilities.merge_declarations``.

    Missing declarations compile to the empty rules, not a skip. When
    ``materialize()`` is invoked with those empty rules it still writes
    a ``rules.json`` so a hook that fires on an unconstrained spawn
    doesn't crash on file-not-found. Callers that skip materialisation
    entirely (e.g. agents with no declared capabilities) bypass that
    contract — see ``agent._materialize_capability_policy``."""

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


# ---- Claude Code settings.json ---------------------------------------------

_HookEvent = Literal["PreToolUse"]


class _ClaudeHookCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["command"] = "command"
    command: str


class _ClaudeHookMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matcher: str  # tool-name or regex-alternation like "Write|Edit"
    hooks: list[_ClaudeHookCommand]


class _ClaudeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hooks: dict[_HookEvent, list[_ClaudeHookMatcher]] = Field(default_factory=dict)


def _build_claude_settings(rules: CompiledRules) -> _ClaudeSettings:
    """Compose ``.claude/settings.json`` from the compiled rules.

    Only registers hooks whose rules are non-empty. Registering a hook
    that has nothing to check wastes a process spawn per tool call and
    muddies denial attribution when something does fire."""

    matchers: list[_ClaudeHookMatcher] = []

    if rules.bash.deny_patterns:
        matchers.append(
            _ClaudeHookMatcher(
                matcher="Bash",
                hooks=[_ClaudeHookCommand(command=f"{SANDBOX_HOOK_BIN}/check-bash")],
            )
        )

    # Write-family tools: the hook checks ``paths.writable`` (permit
    # list) plus ``paths.denied`` (deny list). We register it when
    # either permit or deny is declared — a spawn that declared only
    # readable paths doesn't need the write hook.
    has_write_rules = bool(rules.paths.writable or rules.paths.denied)
    if has_write_rules:
        matchers.append(
            _ClaudeHookMatcher(
                matcher="Write|Edit|MultiEdit|NotebookEdit",
                hooks=[_ClaudeHookCommand(command=f"{SANDBOX_HOOK_BIN}/check-write")],
            )
        )

    # Read-family tools: the hook checks ``paths.readable`` +
    # ``paths.denied`` (deny wins). Register only when readable or
    # denied is declared — a spawn with only writable paths doesn't
    # restrict reads.
    has_read_rules = bool(rules.paths.readable or rules.paths.denied)
    if has_read_rules:
        matchers.append(
            _ClaudeHookMatcher(
                matcher="Read|Glob|Grep",
                hooks=[_ClaudeHookCommand(command=f"{SANDBOX_HOOK_BIN}/check-path")],
            )
        )

    hooks: dict[_HookEvent, list[_ClaudeHookMatcher]] = {}
    if matchers:
        hooks["PreToolUse"] = matchers

    return _ClaudeSettings(hooks=hooks)


# ---- materialisation -------------------------------------------------------


def write_rules_json(rules: CompiledRules, path: Path) -> None:
    """Serialise ``rules`` to ``path`` as pretty-printed JSON.

    Parent directory is created if missing. Overwrites any existing
    file — every spawn gets a fresh materialisation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules.model_dump(), indent=2, sort_keys=True) + "\n")


def write_claude_settings(rules: CompiledRules, path: Path) -> None:
    """Serialise ``.claude/settings.json`` to ``path``.

    Same conventions as :func:`write_rules_json`. Files always end with
    a trailing newline so git doesn't complain and ``cat``-ing them
    prints cleanly."""

    settings = _build_claude_settings(rules)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            settings.model_dump(exclude_defaults=False),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def materialize(
    rules: CompiledRules,
    *,
    worktree_path: Path,
    policy_dir: Path,
) -> tuple[Path, Path]:
    """Write both artefacts and return their paths.

    * ``rules.json`` → ``policy_dir / "rules.json"``
    * ``.claude/settings.json`` → ``worktree_path / ".claude" / "settings.json"``

    The split is deliberate: ``rules.json`` lives outside the worktree
    so the bind-mount at ``/jig/policy/`` doesn't contaminate the git
    tree the agent operates on. ``.claude/settings.json`` must live in
    the worktree because Claude Code discovers it relative to the
    agent's cwd."""

    rules_path = policy_dir / "rules.json"
    settings_path = worktree_path / ".claude" / "settings.json"
    write_rules_json(rules, rules_path)
    write_claude_settings(rules, settings_path)
    return rules_path, settings_path
