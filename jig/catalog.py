"""Load-time catalog validation per doc 17 §Validation at load.

Phase 2 Task F: ``jig start`` and ``jig validate`` both call
:func:`validate_catalog` before doing anything else. The validator walks
the project's roles, workflows, config, and check catalog and fails
fast on anything that would cause a runtime surprise.

Checks performed:

* **YAML shape.** Every YAML file under ``.jig/roles`` / ``.jig/workflows``
  and the check catalog parses cleanly.
* **Role references.** Every workflow phase's ``role`` is resolvable
  through the catalog resolution order (project override → shipped
  default).
* **Workflow references.** ``config.workflows.default_by_size`` /
  ``available`` / ``by_type.*.default_by_size`` / ``by_type.*.available``
  all name known workflows.
* **Check references.** Phase ``automated_checks`` entries name known
  checks in ``.jig/checks.yaml``.
* **Required context URIs.** Any ``project://`` or ``role://`` URI in
  a role's ``required_context`` points at an extant file. ``ticket://``
  and ``repo://`` are per-spawn and skipped here.
* **Circular references.** Not checked — full-override catalog
  resolution makes cycles impossible until extends/merge lands
  (doc 17 §Deliberately deferred).

The default mode raises on the first failure so the caller fails fast.
Passing ``collect=True`` returns a list of every failure — useful for
``jig validate`` which wants to print them all.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from jig.bin._hooklib import _split_segments, resolve_uri_glob
from jig.capabilities import (
    WAIVE_TOKENS,
    CapabilityDeclaration,
    is_known_tool,
    is_known_waive_token,
)
from jig.checks import CheckCatalog, load_check_catalog
from jig.config import Config, load_config
from jig.models import RoleConfig, WorkflowConfig
from jig.persistence import (
    _defaults_dir,
    _jig_dir,
    list_role_names,
    list_workflow_names,
    load_role,
    load_workflow,
)
from jig.work_types import (
    WorkTypeSchema,
    list_work_type_names,
    load_work_type_schema,
)


class CatalogError(Exception):
    """Raised when the project catalog is inconsistent."""


def validate_catalog(
    project_path: Path,
    *,
    collect: bool = False,
) -> list[str] | None:
    """Walk the project catalog and report inconsistencies.

    With ``collect=False`` (default): raise :class:`CatalogError` on the
    first problem. Returns ``None`` on success.

    With ``collect=True``: return a list of every problem found.
    ``[]`` means the catalog is clean.
    """
    errors: list[str] = []

    def fail(msg: str) -> None:
        if collect:
            errors.append(msg)
        else:
            raise CatalogError(msg)

    # YAML shape for role + workflow files. Also collect shipped
    # defaults so we can validate them (though they should already be
    # clean — a shipped default that doesn't parse is a regression).
    roles, role_yaml_errors = _load_all_roles(project_path)
    for msg in role_yaml_errors:
        fail(msg)

    workflows, workflow_yaml_errors = _load_all_workflows(project_path)
    for msg in workflow_yaml_errors:
        fail(msg)

    # Check catalog
    try:
        checks = load_check_catalog(project_path)
    except (yaml.YAMLError, ValidationError) as exc:
        fail(f"checks.yaml failed to load: {exc}")
        checks = CheckCatalog({})

    # Config
    try:
        config: Config | None = load_config(project_path)
    except FileNotFoundError:
        config = None  # legacy project.json-only project; skip config checks
    except (yaml.YAMLError, ValidationError) as exc:
        fail(f"config.yaml failed to load: {exc}")
        config = None

    known_roles = set(list_role_names(project_path))
    known_workflows = set(list_workflow_names(project_path))
    known_checks = set(checks.names())

    # Roles referenced by at least one workflow phase are *dispatchable* —
    # they have to carry a real system prompt or the agent spawn would
    # run with no instructions. ``RoleConfig.phase_prompt`` defaults to
    # ``""`` so pseudo-roles (e.g. the shipped ``user`` role that only
    # carries waiver capability) can load, but any such role must not
    # appear in a workflow phase. Collect the referenced set up front so
    # the check below is a single pass.
    dispatched_role_names: set[str] = set()
    for wf in workflows:
        for phase in wf.phases:
            dispatched_role_names.add(phase.role)

    for role in roles:
        if role.role in dispatched_role_names and not role.phase_prompt.strip():
            fail(
                f"role {role.role!r}: phase_prompt is empty but the role is "
                f"dispatched by at least one workflow phase — set a "
                f"non-empty phase_prompt, or remove the workflow reference "
                f"if this is a pseudo-role"
            )
        # Reviewer file-scoping invariants. A role with non-empty
        # ``reads_glob`` operates in scoped mode: the orchestrator
        # filters its diff and reads via the new MCP tools. If the
        # role still lists ``Read`` or ``Bash(git diff*)`` in its
        # ``allowed_tools`` those serve as escape hatches around
        # the filter — reject loudly at startup rather than let a
        # misconfigured role silently leak content.
        #
        # Limitation (deliberate): the validator rejects the
        # KNOWN-BROAD escape hatches (unrestricted ``Read``;
        # ``Bash(git diff*)``). Narrower Bash entries like
        # ``Bash(find .jig/spec*)`` ARE allowed — operators
        # legitimately need spec-file enumeration. These narrow
        # forms can still enumerate (but not READ) paths outside
        # the scope; that's the operator's responsibility to keep
        # tight in role configs they author. A follow-up could
        # validate that any ``Bash(<cmd> <prefix>*)`` prefix lies
        # within ``reads_glob``, but for v1 the per-role manual
        # review of tightly-bound Bash patterns is the contract.
        if role.reads_glob:
            disallowed_when_scoped = {"Read"}
            for tool in role.allowed_tools:
                if tool in disallowed_when_scoped:
                    fail(
                        f"role {role.role!r} declares ``reads_glob`` "
                        f"but ``allowed_tools`` still includes "
                        f"{tool!r}. Drop {tool!r} and add "
                        "``reviewer_read_file`` so file reads go "
                        "through the orchestrator-side scope filter."
                    )
                if tool.startswith("Bash(") and "git diff" in tool:
                    fail(
                        f"role {role.role!r} declares ``reads_glob`` "
                        f"but ``allowed_tools`` still includes "
                        f"{tool!r}. Drop the git-diff Bash entry "
                        "and add ``reviewer_get_diff`` so diff "
                        "queries go through the scope filter."
                    )
            required_when_scoped = {
                "reviewer_get_diff",
                "reviewer_read_file",
            }
            missing = required_when_scoped - set(role.allowed_tools)
            if missing:
                fail(
                    f"role {role.role!r} declares ``reads_glob`` "
                    "but ``allowed_tools`` is missing "
                    f"{sorted(missing)!r}. These MCP tools are the "
                    "only diff / read surface a scoped reviewer has "
                    "— without them the role has no way to fetch "
                    "the content it's supposed to review."
                )

    # Phase role + check references
    for wf in workflows:
        for phase in wf.phases:
            if phase.role not in known_roles:
                fail(
                    f"workflow {wf.name!r} phase {phase.name!r} references "
                    f"unknown role {phase.role!r} (known: {sorted(known_roles)})"
                )
            for check_name in phase.automated_checks:
                if check_name not in known_checks:
                    fail(
                        f"workflow {wf.name!r} phase {phase.name!r} references "
                        f"unknown check {check_name!r} "
                        f"(known: {sorted(known_checks)})"
                    )
            # Phase 4H: questions_to / escalation_targets role refs.
            # "human" is a sentinel (escalate to a person, not a role)
            # and is always allowed; everything else must be a known
            # role.
            for target in phase.questions_to:
                if target == "human":
                    continue
                if target not in known_roles:
                    fail(
                        f"workflow {wf.name!r} phase {phase.name!r} "
                        f"questions_to references unknown role {target!r} "
                        f"(known: {sorted(known_roles)})"
                    )
            for target in phase.escalation_targets:
                if target == "human":
                    continue
                if target not in known_roles:
                    fail(
                        f"workflow {wf.name!r} phase {phase.name!r} "
                        f"escalation_targets references unknown role "
                        f"{target!r} (known: {sorted(known_roles)})"
                    )

    # Config workflow references
    if config is not None:
        for size, name in config.workflows.default_by_size.items():
            if name not in known_workflows:
                fail(
                    f"config.workflows.default_by_size.{size} references "
                    f"unknown workflow {name!r} (known: {sorted(known_workflows)})"
                )
        for name in config.workflows.available:
            if name not in known_workflows:
                fail(
                    f"config.workflows.available references "
                    f"unknown workflow {name!r} (known: {sorted(known_workflows)})"
                )
        for wt, entry in config.workflows.by_type.items():
            for size, name in entry.default_by_size.items():
                if name not in known_workflows:
                    fail(
                        f"config.workflows.by_type.{wt}.default_by_size.{size} "
                        f"references unknown workflow {name!r} "
                        f"(known: {sorted(known_workflows)})"
                    )
            for name in entry.available:
                if name not in known_workflows:
                    fail(
                        f"config.workflows.by_type.{wt}.available references "
                        f"unknown workflow {name!r} "
                        f"(known: {sorted(known_workflows)})"
                    )

    # Required context URIs — only project:// and role:// are checkable
    # without live ticket state. ticket:// and repo:// resolve per-spawn.
    for role in roles:
        for uri in role.required_context:
            msg = _validate_static_uri(uri, project_path)
            if msg is not None:
                fail(f"role {role.role!r} required_context {uri!r}: {msg}")

    # Phase 5 Task F: capability-policy schema checks for both the
    # role's base ``capabilities`` and each phase's
    # ``capability_overrides``. The merge itself happens at spawn time
    # — we only check each declaration can compile on its own.
    for role in roles:
        for msg in _validate_capabilities(role.capabilities):
            fail(f"role {role.role!r} capabilities: {msg}")
    for wf in workflows:
        for phase in wf.phases:
            for msg in _validate_capabilities(phase.capability_overrides):
                fail(
                    f"workflow {wf.name!r} phase {phase.name!r} "
                    f"capability_overrides: {msg}"
                )

    # Phase 3H: work-type schemas
    work_type_schemas, wt_yaml_errors = _load_all_work_type_schemas(project_path)
    for msg in wt_yaml_errors:
        fail(msg)

    # Config.ownership.spec.* fields must reference a field the schema
    # declares somewhere. We don't know which work_type a given
    # ticket will use, so accept a field name that appears in any
    # shipped or overridden schema.
    if config is not None and work_type_schemas:
        all_spec_fields: set[str] = set()
        for s in work_type_schemas:
            all_spec_fields.update(s.allowed_fields())
        # SpecOwnership extras surface via model_dump().
        spec_map = config.ownership.spec.model_dump()
        for field, owner in spec_map.items():
            if not owner:
                continue
            if field not in all_spec_fields:
                fail(
                    f"config.ownership.spec.{field} references a field "
                    f"not declared in any work-type schema "
                    f"(known spec fields: {sorted(all_spec_fields)})"
                )

    # Config.roles.<role>.helper_template must name an existing role
    # whenever assignment == human_with_helper. Agent-assignment
    # templates are role names too (the helper agent runs as a role);
    # check both modes.
    if config is not None:
        roles_dump = config.roles.model_dump()
        for role_name, staffing in roles_dump.items():
            if not isinstance(staffing, dict):
                continue
            assignment = staffing.get("assignment", "")
            helper = staffing.get("helper_template", "")
            if (
                assignment in {"human_with_helper", "agent"}
                and helper
                and helper not in known_roles
            ):
                fail(
                    f"config.roles.{role_name}.helper_template "
                    f"references unknown role {helper!r} "
                    f"(known: {sorted(known_roles)})"
                )

    if collect:
        return errors
    return None


# ---- helpers --------------------------------------------------------------


def _load_all_roles(project_path: Path) -> tuple[list[RoleConfig], list[str]]:
    """Load every role resolvable by this project; return (roles, errors).

    YAML / validation errors are captured as strings instead of raising,
    so ``validate_catalog(collect=True)`` can report every bad file at
    once. Successfully-parsed roles are still returned so downstream
    reference checks can run against them.
    """
    roles: list[RoleConfig] = []
    errors: list[str] = []
    for name in list_role_names(project_path):
        try:
            roles.append(load_role(project_path, name))
        except yaml.YAMLError as exc:
            errors.append(f"role {name!r} YAML parse failed: {exc}")
        except ValidationError as exc:
            errors.append(f"role {name!r} validation failed: {exc}")
    return roles, errors


def _load_all_workflows(
    project_path: Path,
) -> tuple[list[WorkflowConfig], list[str]]:
    workflows: list[WorkflowConfig] = []
    errors: list[str] = []
    for name in list_workflow_names(project_path):
        try:
            workflows.append(load_workflow(project_path, name))
        except yaml.YAMLError as exc:
            errors.append(f"workflow {name!r} YAML parse failed: {exc}")
        except ValidationError as exc:
            errors.append(f"workflow {name!r} validation failed: {exc}")
    return workflows, errors


def _load_all_work_type_schemas(
    project_path: Path,
) -> tuple[list[WorkTypeSchema], list[str]]:
    """Load every work-type schema (project + shipped); gather errors."""
    schemas: list[WorkTypeSchema] = []
    errors: list[str] = []
    for name in list_work_type_names(project_path):
        try:
            schemas.append(load_work_type_schema(project_path, name))
        except yaml.YAMLError as exc:
            errors.append(f"work_type schema {name!r} YAML parse failed: {exc}")
        except ValidationError as exc:
            errors.append(f"work_type schema {name!r} validation failed: {exc}")
    return schemas, errors


def _validate_static_uri(uri: str, project_path: Path) -> str | None:
    """Return ``None`` if the URI is resolvable at load, else an error message.

    Only ``project://`` and ``role://`` are checkable here. Any other
    scheme is accepted — those resolve per-spawn against ticket state or
    the worktree.
    """
    if "://" not in uri:
        return "missing scheme (use e.g. project://, role://)"
    scheme, _, body = uri.partition("://")

    # issue:// is a transitional alias for ticket://; both are per-spawn.
    if scheme in {"ticket", "issue", "repo", "decision"}:
        return None

    if scheme == "project":
        if not body:
            return "missing path after project://"
        base = _jig_dir(project_path) / "context" / "project"
        return _check_context_file_exists(base, body)

    if scheme == "role":
        role_name, _, rel = body.partition("/")
        if not role_name or not rel:
            return "expected role://<role>/<path>"
        base = _jig_dir(project_path) / "context" / "roles" / role_name
        return _check_context_file_exists(base, rel)

    # Unknown scheme — accept for forward compatibility (custom resolvers
    # per doc 07). The spawn-time resolver will log a warning if it's
    # really unknown.
    return None


def _check_context_file_exists(base: Path, rel: str) -> str | None:
    as_given = base / rel
    if as_given.is_file():
        return None
    # ".md" fallback matches the resolver's behavior.
    if not as_given.suffix and as_given.with_suffix(".md").is_file():
        return None
    return f"not found under {base}"


def _validate_capabilities(
    decl: CapabilityDeclaration | None,
) -> list[str]:
    """Return a list of error messages for a single capability
    declaration; ``[]`` if it's clean.

    Checks (per implementation-plan Phase 5 F, doc 16 §Capability policy):

    * ``tools.allowed`` — every entry is a known Claude Code builtin
      or matches the ``mcp__<server>__<tool>`` pattern.
    * ``tool_params.Bash.deny_patterns`` — every entry compiles as a
      Python regex. Invalid patterns would silently skip at hook time,
      so we fail loud at load.
    * ``paths.*`` — no ``..`` segments (would escape the sandbox
      root); must carry an explicit URI scheme or be an absolute path.
      Sandbox-absolute paths (``/workspace/**``) are legal —
      enforcement happens relative to the mount, not to host layout.
    """

    errors: list[str] = []
    if decl is None:
        return errors

    # tools.allowed
    if decl.tools is not None:
        for tool in decl.tools.allowed:
            if not is_known_tool(tool):
                errors.append(
                    f"tools.allowed: unknown tool {tool!r} "
                    f"(not a Claude Code builtin, doesn't match "
                    f"mcp__<server>__<tool>)"
                )

    # tool_params.Bash.deny_patterns
    if decl.tool_params is not None and decl.tool_params.Bash is not None:
        for pattern in decl.tool_params.Bash.deny_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(
                    f"tool_params.Bash.deny_patterns: {pattern!r} is "
                    f"not a valid regex: {exc}"
                )

    # paths.*
    if decl.paths is not None:
        for category in ("writable", "readable", "denied"):
            patterns = getattr(decl.paths, category)
            for pat in patterns:
                msg = _validate_path_glob(pat)
                if msg is not None:
                    errors.append(f"paths.{category}: {pat!r}: {msg}")

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


# Schemes accepted in capability path globs. Must stay in sync with the
# context resolver's known set (``_validate_static_uri`` above and
# ``jig/context_resolver.py``). Sorted tuple so error messages list the
# expectations deterministically.
_ALLOWED_PATH_SCHEMES: tuple[str, ...] = (
    "decision",
    "issue",
    "project",
    "repo",
    "role",
    "ticket",
)


def _validate_path_glob(pattern: str) -> str | None:
    """Return ``None`` if the glob is structurally sound, else a
    reason. ``..`` anywhere is a sandbox escape and always rejected."""

    if not pattern:
        return "empty pattern"
    # `..` in any path segment is a sandbox-root escape attempt. Block
    # regardless of scheme — even a `ticket://worktree/../..` rooted
    # string would translate to an unsafe absolute path at resolution.
    for segment in pattern.split("/"):
        if segment == "..":
            return "contains '..' segment (escapes sandbox root)"
    # Must carry a supported scheme (ticket://, repo://, project://,
    # role://, decision://, issue://) or be an absolute sandbox path.
    # Typos like "tiket://..." or empty schemes / bodies would silently
    # skip at hook evaluation time, so fail loud here instead.
    if "://" in pattern:
        scheme, _, body = pattern.partition("://")
        if not scheme:
            return "empty URI scheme (expected e.g. ticket://, repo://)"
        if scheme not in _ALLOWED_PATH_SCHEMES:
            return (
                f"unsupported URI scheme {scheme!r} "
                f"(expected one of: {', '.join(_ALLOWED_PATH_SCHEMES)})"
            )
        if not body:
            return f"empty path after {scheme}://"
        return None
    if pattern.startswith("/"):
        return None
    # Relative paths with no scheme are ambiguous — reject loudly so the
    # operator picks the right root explicitly.
    return (
        "relative pattern with no URI scheme and no leading '/' "
        "(ambiguous — use ticket://, repo://, or an absolute path)"
    )


# ---------------------------------------------------------------------
# Phase 5 Task F — shadow-pattern detection (advisory).
#
# Errors block catalog load; warnings do not. We check each capability
# declaration for ``writable``/``readable`` patterns that are fully
# subsumed by a ``denied`` pattern — the permit looks effective in YAML
# but never fires at the hook boundary because deny always wins. Not a
# correctness bug (the compiled ruleset is still deterministic), but a
# silent footgun worth surfacing alongside ``jig validate``.
#
# Subsumption is conservative: we only flag the common shapes
# (``**``-trailing deny globs, literal-under-prefix, `*`-segment within
# same shape). Complex mid-wildcard patterns that technically subsume
# are left unflagged — preferring under-warning to false positives.
# ---------------------------------------------------------------------


def _segment_subsumes(p1_seg: str, p2_seg: str) -> bool:
    """Does every single-segment string matching ``p2_seg`` also
    match ``p1_seg``? Conservative: literal equality or ``*``-wildcard
    in ``p1_seg``. Other pattern-vs-pattern shapes return False."""

    if p1_seg == p2_seg:
        return True
    if p1_seg == "*":
        # ``*`` matches any single-segment string; any pattern that
        # produces single-segment strings is subsumed by ``*``.
        return True
    return False


def _segs_subsume(p1: list[str], i: int, p2: list[str], j: int) -> bool:
    """Recursive worker: does p1[i:] subsume p2[j:]?

    Returns True iff every path whose segments match ``p2[j:]`` also
    match ``p1[i:]``. A ``**`` in ``p2`` immediately fails subsumption
    unless the matching ``p1`` position is also ``**`` — otherwise
    p2's unbounded expansion can outgrow p1's specific segment count.
    """

    while i < len(p1):
        if p1[i] == "**":
            # Trailing ``**`` absorbs anything — subsumes the rest of p2.
            if i + 1 == len(p1):
                return True
            # Try consuming 0..K of p2's remaining segments and
            # recursing on the tail. If p2's next segment is ``**`` we
            # must let p1's ``**`` absorb *at least* that position —
            # the recursion handles it naturally because p2[j]==``**``
            # forces the early-return path below to False, which
            # cuts the branch.
            for k in range(j, len(p2) + 1):
                if _segs_subsume(p1, i + 1, p2, k):
                    return True
            return False
        if j >= len(p2):
            # p1 still expects a segment; p2 can't provide one.
            return False
        if p2[j] == "**":
            # p2 expands to an unbounded number of segments; p1's
            # specific segment can't match every expansion length.
            return False
        if not _segment_subsumes(p1[i], p2[j]):
            return False
        i += 1
        j += 1
    # p1 exhausted. p2 must also be exhausted — any residual segments
    # mean p2 generates longer paths that p1 doesn't accept.
    return j == len(p2)


def _pattern_subsumes(denied: str, permit: str) -> bool:
    """True if every path matching ``permit`` also matches ``denied``.

    Both patterns are resolved through ``resolve_uri_glob`` so we
    compare on the sandbox-absolute form. Patterns whose scheme can't
    be resolved (unmapped URIs) never subsume anything — the hook
    can't enforce them either.
    """

    d_abs = resolve_uri_glob(denied)
    p_abs = resolve_uri_glob(permit)
    if d_abs is None or p_abs is None:
        return False
    return _segs_subsume(_split_segments(d_abs), 0, _split_segments(p_abs), 0)


def _shadow_warnings(decl: CapabilityDeclaration | None) -> list[str]:
    """Advisory: permit patterns fully subsumed by deny patterns.

    Returns one warning per shadowed permit. Both ``writable`` and
    ``readable`` are scanned against ``denied``; bidirectional
    subsumption (e.g., a denied pattern subsumed by a permit) is not
    flagged — the deny still fires correctly, so there's no footgun.
    """

    if decl is None or decl.paths is None:
        return []

    warnings: list[str] = []
    denied = decl.paths.denied
    if not denied:
        return warnings

    for category in ("writable", "readable"):
        permits = getattr(decl.paths, category)
        for permit in permits:
            for denied_pat in denied:
                if _pattern_subsumes(denied_pat, permit):
                    warnings.append(
                        f"paths.{category} {permit!r} is shadowed by "
                        f"paths.denied {denied_pat!r} — the permit "
                        f"never fires at the hook boundary"
                    )
                    break  # one warning per permit is enough
    return warnings


def collect_policy_warnings(project_path: Path) -> list[str]:
    """Return advisory warnings about capability-policy shape.

    Currently emits one message per ``writable``/``readable`` pattern
    fully shadowed by a ``denied`` pattern in the same declaration.
    Walks role-level ``capabilities`` and each phase's
    ``capability_overrides``. Warnings are context-prefixed so the
    operator knows which role/phase to edit.

    Advisory only — never raises. Unknown roles / malformed YAML are
    caught by :func:`validate_catalog` and not re-reported here.
    """

    out: list[str] = []
    try:
        roles, _ = _load_all_roles(project_path)
    except Exception:
        return out
    for role in roles:
        for msg in _shadow_warnings(role.capabilities):
            out.append(f"role {role.role!r} capabilities: {msg}")

    try:
        workflows, _ = _load_all_workflows(project_path)
    except Exception:
        return out
    for wf in workflows:
        for phase in wf.phases:
            for msg in _shadow_warnings(phase.capability_overrides):
                out.append(
                    f"workflow {wf.name!r} phase {phase.name!r} "
                    f"capability_overrides: {msg}"
                )
    return out


# Exposed so tests can exercise the loader helpers directly.
__all__ = [
    "CatalogError",
    "collect_policy_warnings",
    "validate_catalog",
]


# Defensive: hint that we intentionally pulled in `_defaults_dir` for
# symmetry even though we don't use it directly — having it in scope
# lets future validations cross-reference shipped defaults explicitly.
_ = _defaults_dir
