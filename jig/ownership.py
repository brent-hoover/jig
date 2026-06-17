"""Owner resolution — where does a proposal route? (Phase 3 Task F)

Per doc 04 every durable artifact has a declared owner role, and
proposals against that artifact route through the owner. Phase 3
lands the resolution and routing record; spawning helper agents
and enforcing joint acceptance on split-ownership artifacts waits
for Phase 5.

Target URIs this resolver handles today:

* ``ticket://spec`` — whole-spec owner (config fallback).
* ``ticket://spec.<field>`` — per-field spec owner. Project config
  overrides the work-type schema's default ownership map.
* ``project://<key>`` — project-level artifact (architecture,
  roadmap, etc.) per ``config.ownership.<key>``.

Returns an :class:`OwnerRouting` describing the role, the staffed
assignee (if any), and the assignment style so downstream callers
can decide whether to notify a human, spawn a helper agent, or
treat the proposal as agent-acceptable.

Unknown targets raise :class:`OwnershipError` — this is a catalog
error at load time and a hard route failure at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jig.config import Config, RoleAssignment
from jig.work_types import WorkTypeSchema

Assignment = Literal["human", "human_with_helper", "agent", "unstaffed"]


class OwnershipError(ValueError):
    """Raised when a proposal target has no resolvable owner."""


@dataclass(frozen=True)
class OwnerRouting:
    role: str  # "po" / "sa" / any declared role alias
    assignee: str | None  # resolved from config.roles.<role>.human
    helper_template: str | None  # resolved from config.roles.<role>.helper_template
    assignment: Assignment


# ---- helpers --------------------------------------------------------------


def _spec_field_owner(
    config: Config, schema: WorkTypeSchema | None, field: str
) -> str | None:
    """Config override wins; work-type schema ownership is the fallback."""
    # config.ownership.spec is a SpecOwnership(extra="allow") — extras
    # live in __pydantic_extra__; named fields live as regular attrs.
    spec_map = config.ownership.spec.model_dump()
    if field in spec_map and spec_map[field]:
        return str(spec_map[field])
    if schema is not None and field in schema.ownership:
        return schema.ownership[field]
    return None


def _project_owner(config: Config, key: str) -> str | None:
    """Project-level artifact owner from ``config.ownership.<key>``."""
    # OwnershipSection is extra="allow"; spec is nested.
    data = config.ownership.model_dump()
    value = data.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _route_for_role(config: Config, role: str) -> OwnerRouting:
    """Compute the staffing/assignment tuple for a role alias.

    Reads ``config.roles.<role>`` for the assignment style + staffed
    identity. Unstaffed roles are recorded rather than raising — per
    doc 04 orphaned ownership is visible, not fatal.
    """
    roles_map = config.roles.model_dump()
    raw = roles_map.get(role)
    if not isinstance(raw, dict):
        return OwnerRouting(
            role=role,
            assignee=None,
            helper_template=None,
            assignment="unstaffed",
        )
    # Normalize via RoleAssignment so we tolerate partial dicts.
    staff = RoleAssignment.model_validate(raw)
    if staff.assignment == "human":
        return OwnerRouting(
            role=role,
            assignee=staff.human or None,
            helper_template=None,
            assignment="human",
        )
    if staff.assignment == "human_with_helper":
        return OwnerRouting(
            role=role,
            assignee=staff.human or None,
            helper_template=staff.helper_template or None,
            assignment="human_with_helper",
        )
    if staff.assignment == "agent":
        return OwnerRouting(
            role=role,
            assignee=None,
            helper_template=staff.helper_template or None,
            assignment="agent",
        )
    # Assignment type blank / unknown → treat as unstaffed so the
    # routing result is still informative.
    return OwnerRouting(
        role=role,
        assignee=staff.human or None,
        helper_template=staff.helper_template or None,
        assignment="unstaffed",
    )


# ---- public API -----------------------------------------------------------


def resolve_owner(
    config: Config,
    target: str,
    *,
    work_type_schema: WorkTypeSchema | None = None,
    whole_spec_owner_default: str = "po",
) -> OwnerRouting:
    """Route a proposal target to an owner role + staffing decision.

    ``target`` is a URI:

    * ``ticket://spec`` / ``ticket://spec.<field>``
    * ``project://<key>``

    ``work_type_schema`` is required for ``ticket://spec.*`` targets
    so the schema's ownership map can serve as the fallback when the
    project config doesn't pin the field.
    """
    if "://" not in target:
        raise OwnershipError(
            f"target missing scheme (expected project:// or ticket://): {target}"
        )
    scheme, _, body = target.partition("://")

    if scheme == "ticket":
        if body == "spec":
            # Whole-spec owner — config alone; schemas don't declare
            # a default for "all fields", so fall back to PO per
            # doc 04 §Jointly owned artifacts.
            default_role = whole_spec_owner_default
            return _route_for_role(config, default_role)
        if body.startswith("spec."):
            field = body[len("spec.") :]
            field_role = _spec_field_owner(config, work_type_schema, field)
            if field_role is None:
                raise OwnershipError(
                    f"no owner declared for {target!r} "
                    f"(field={field!r}); check config.ownership.spec "
                    "or the work-type schema's ownership map"
                )
            return _route_for_role(config, field_role)
        raise OwnershipError(f"unsupported ticket:// target for ownership: {target}")

    if scheme == "project":
        project_role = _project_owner(config, body)
        if project_role is None:
            raise OwnershipError(
                f"no owner declared for {target!r}; add config.ownership.{body}"
            )
        return _route_for_role(config, project_role)

    raise OwnershipError(f"unknown target scheme for ownership: {target}")


__all__ = [
    "Assignment",
    "OwnerRouting",
    "OwnershipError",
    "resolve_owner",
]
