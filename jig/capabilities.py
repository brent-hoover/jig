"""Capability policy declaration and compilation (Phase 5 Task F, doc 16).

Capabilities declare what an agent can do: which tools it may invoke,
what parameter patterns are denied on those tools, and which paths it
may read, write, or must not touch. Declarations live on role templates
(``RoleConfig.capabilities``) with per-phase overrides on
``PhaseConfig.capability_overrides``.

This module defines the *source* shape — the dataclasses the YAML
parses into. Compilation into the enforcement artefact
(``rules.json``) and the Claude Code hook registration
(``.claude/settings.json``) lives in ``jig/capability_compiler.py``.

Merge semantics (per implementation plan Phase 5 F):

* List fields union. ``tools.allowed``, ``paths.writable``,
  ``paths.readable``, ``paths.denied``, ``tool_params.Bash.deny_patterns``
  all add together. A phase override *adds* to the role's base rather
  than replacing — dropping a permit happens at the deny layer, not by
  omission.
* Scalars replace. (No scalars in the shape yet; reserved for future
  fields like ``timeouts``.)

The resulting union is then compiled: permits collected, denies
collected, deny-wins applied during enforcement (Task G hooks).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# Canonical Claude Code built-in tools as of the agent SDK we target.
# MCP tools follow the ``mcp__<server>__<tool>`` naming convention and
# are validated structurally rather than by membership (any MCP the
# role declares is in-scope).
BUILTIN_TOOLS: frozenset[str] = frozenset(
    {
        "Agent",
        "Bash",
        "Edit",
        "ExitPlanMode",
        "Glob",
        "Grep",
        "LS",
        "MultiEdit",
        "NotebookEdit",
        "Read",
        "Task",
        "TodoWrite",
        "ToolSearch",
        "WebFetch",
        "WebSearch",
        "Write",
    }
)


def is_known_tool(name: str) -> bool:
    """True if ``name`` is a Claude Code builtin or matches the MCP
    naming pattern. Used by ``jig validate`` to catch typos in
    ``capabilities.tools.allowed`` at load time."""

    if name in BUILTIN_TOOLS:
        return True
    # MCP tool pattern: mcp__<server>__<tool>. The server and tool
    # segments are both non-empty; anything looser matches too many
    # typos to be useful.
    if name.startswith("mcp__"):
        parts = name.split("__")
        return len(parts) >= 3 and all(parts[1:])
    return False


class BashToolParams(BaseModel):
    """Parameter constraints for the ``Bash`` tool.

    ``deny_patterns`` are Python regex strings matched against the
    ``command`` arg of a Bash invocation. A match denies the call.
    Patterns are merged across base + override, with duplicates removed
    while preserving first-seen order.
    """

    model_config = ConfigDict(extra="forbid")
    deny_patterns: list[str] = []


class CapabilityToolParams(BaseModel):
    """Per-tool parameter constraints. Keyed by Claude Code tool name
    (case-sensitive). Extra keys are rejected at load — a typo like
    ``bash:`` (lowercase) must fail loud rather than silently producing
    no constraints.

    Adding a new constrained tool: add a typed field here and teach the
    compiler how to emit the matching hook registration."""

    model_config = ConfigDict(extra="forbid")
    Bash: BashToolParams | None = None


class CapabilityTools(BaseModel):
    """Tool allow-list. Entries must be known Claude Code tool names or
    match the ``mcp__<server>__<tool>`` pattern; ``jig validate``
    enforces that. Empty / missing means "no explicit allow-list" —
    Claude Code's default behaviour applies (equivalent to "all tools
    available to this agent")."""

    model_config = ConfigDict(extra="forbid")
    allowed: list[str] = []


class CapabilityPaths(BaseModel):
    """Path access declaration. Entries are globs rooted in URIs
    (``ticket://worktree/**``, ``repo://**``, etc.) or sandbox-absolute
    literals. ``jig validate`` checks that patterns don't contain ``..``
    segments that would escape the sandbox root.

    The three categories don't overlap at the source level — denied
    beats writable beats readable at enforcement, and the compiler
    emits all three sets to the hook so the deny-wins decision stays
    local to the enforcement moment."""

    model_config = ConfigDict(extra="forbid")
    writable: list[str] = []
    readable: list[str] = []
    denied: list[str] = []


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
    kind field). ``jig/catalog.py::_validate_capabilities`` will fail
    loud on unknown tokens at load time (wired up in Phase 5
    Task H)."""

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


def _merge_str_lists(*sources: list[str]) -> list[str]:
    """Union lists preserving first-seen order. The order is stable for
    readability in the compiled rules — a deterministic output makes
    diffs between spawn attempts easy to eyeball."""

    seen: set[str] = set()
    out: list[str] = []
    for src in sources:
        for item in src:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def merge_declarations(
    base: CapabilityDeclaration | None,
    override: CapabilityDeclaration | None,
) -> CapabilityDeclaration:
    """Merge a role's base declaration with a phase override.

    Either side may be ``None`` (missing declaration). The result is
    always a fully-constructed ``CapabilityDeclaration`` with
    non-``None`` sub-fields when either side provides values for that
    category — simplifies the compiler's downstream logic (no
    ``if tools is None: ...`` branches).

    Per the merge rules, all list fields union. If neither side
    declares a sub-field, that sub-field stays ``None``."""

    base = base or CapabilityDeclaration()
    override = override or CapabilityDeclaration()

    merged_tools: CapabilityTools | None = None
    if base.tools is not None or override.tools is not None:
        merged_tools = CapabilityTools(
            allowed=_merge_str_lists(
                (base.tools.allowed if base.tools else []),
                (override.tools.allowed if override.tools else []),
            ),
        )

    merged_params: CapabilityToolParams | None = None
    if base.tool_params is not None or override.tool_params is not None:
        base_bash = base.tool_params.Bash if base.tool_params else None
        over_bash = override.tool_params.Bash if override.tool_params else None
        merged_bash: BashToolParams | None = None
        if base_bash is not None or over_bash is not None:
            merged_bash = BashToolParams(
                deny_patterns=_merge_str_lists(
                    (base_bash.deny_patterns if base_bash else []),
                    (over_bash.deny_patterns if over_bash else []),
                ),
            )
        merged_params = CapabilityToolParams(Bash=merged_bash)

    merged_paths: CapabilityPaths | None = None
    if base.paths is not None or override.paths is not None:
        b = base.paths or CapabilityPaths()
        o = override.paths or CapabilityPaths()
        merged_paths = CapabilityPaths(
            writable=_merge_str_lists(b.writable, o.writable),
            readable=_merge_str_lists(b.readable, o.readable),
            denied=_merge_str_lists(b.denied, o.denied),
        )

    return CapabilityDeclaration(
        tools=merged_tools,
        tool_params=merged_params,
        paths=merged_paths,
    )
