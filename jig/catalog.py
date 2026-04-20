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

from pathlib import Path

import yaml
from pydantic import ValidationError

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
                fail(
                    f"role {role.role!r} required_context {uri!r}: {msg}"
                )

    # Phase 3H: work-type schemas
    work_type_schemas, wt_yaml_errors = _load_all_work_type_schemas(
        project_path
    )
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
            errors.append(
                f"work_type schema {name!r} YAML parse failed: {exc}"
            )
        except ValidationError as exc:
            errors.append(
                f"work_type schema {name!r} validation failed: {exc}"
            )
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


# Exposed so tests can exercise the loader helpers directly.
__all__ = [
    "CatalogError",
    "validate_catalog",
]


# Defensive: hint that we intentionally pulled in `_defaults_dir` for
# symmetry even though we don't use it directly — having it in scope
# lets future validations cross-reference shipped defaults explicitly.
_ = _defaults_dir
