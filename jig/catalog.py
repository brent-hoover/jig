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
