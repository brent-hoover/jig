"""Ticket specs — structured YAML per ticket at ``.jig/specs/<id>.yaml``.

Phase 3 Task C. A ticket spec is the structured artifact whose shape is
dictated by the ticket's work-type schema (``jig.work_types``). We store
one YAML file per ticket rather than a JSONL store because:

* humans diff specs in review — YAML-per-file is readable;
* closing a ticket later archives a directory of spec files cleanly
  (doc 17);
* edits land through the Proposal flow (Task G), which touches one
  spec at a time.

The model is a loose envelope — ``fields`` is ``dict[str, Any]`` —
because different work types carry different field sets. Shape
validation against the schema happens in ``save_ticket_spec`` at write
time, not at ``TicketSpec(...)`` construction, so tools loading an
already-persisted file don't crash if the schema has since been edited
(we'd rather surface that as a load-time catalog error in Phase 3H).

Phase 3 treats the spec as the source of structured truth. Later
phases wire asymmetric validation (doc 10) and section locking (doc
16) on top of it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jig.spec_schema import Capability
from jig.ticket import Size, WorkType
from jig.work_types import WorkTypeSchema, load_work_type_schema


class SpecValidationError(ValueError):
    """Raised when a ticket spec doesn't match its work-type schema."""


class TicketSpec(BaseModel):
    """Structured ticket spec, keyed by ``ticket_id``.

    ``work_type`` and ``size`` are **snapshots** taken at spec creation
    per doc 03 §Immutability — re-sizing a ticket doesn't rewrite its
    spec.

    ``version`` bumps on every write; proposals reference the version
    they target so a mid-flight accept against a newer spec can be
    detected (Task G).
    """

    ticket_id: str
    work_type: WorkType
    size: Size
    fields: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Keep the JSON <-> YAML roundtrip stable: don't emit extra aliases,
    # do allow construction with either enum or str.
    model_config = ConfigDict(use_enum_values=False)


# ---- file layout ----------------------------------------------------------


def _specs_dir(project_path: Path) -> Path:
    return project_path / ".jig" / "specs"


def _spec_path(project_path: Path, ticket_id: str) -> Path:
    return _specs_dir(project_path) / f"{ticket_id}.yaml"


# ---- schema check --------------------------------------------------------


def _validate_against_schema(
    spec: TicketSpec,
    schema: WorkTypeSchema,
    *,
    enforce_required_fields: bool = True,
) -> None:
    """Validate ``spec.fields`` against the work-type schema.

    Two checks per doc 03:

    1. Every field mandated by the schema for ``spec.size`` must be
       present (and non-empty — empty-string or empty-list doesn't
       satisfy "required"). Skipped when ``enforce_required_fields``
       is ``False`` (the capability-materialiser path — see
       :func:`save_ticket_spec`).
    2. No field name outside ``required ∪ optional`` may appear.
       Projects that want a field not in the schema edit the schema
       (it's version-controlled). Always runs.
    """
    allowed = schema.allowed_fields()
    unknown = [name for name in spec.fields if name not in allowed]

    errors: list[str] = []
    if enforce_required_fields:
        required = schema.required_fields_for_size(spec.size)
        missing = [name for name in required if not _present(spec.fields.get(name))]
        if missing:
            errors.append(
                f"missing required fields for size {spec.size.value}: "
                f"{sorted(missing)}"
            )
    if unknown:
        errors.append(f"unknown fields not declared in schema: {sorted(unknown)}")
    if errors:
        raise SpecValidationError(
            f"spec for ticket {spec.ticket_id!r} (work_type="
            f"{spec.work_type.value}): " + "; ".join(errors)
        )


def _present(value: Any) -> bool:
    """True when a field has meaningful content.

    None / empty string / empty list / empty dict all read as missing.
    ``0`` and ``False`` count as present — a boolean flag being False
    is still a real answer.
    """
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


# ---- public API -----------------------------------------------------------


def materialize_ticket_spec_from_capability(
    *,
    ticket_id: str,
    work_type: WorkType,
    size: Size,
    capability: Capability,
) -> TicketSpec:
    """Project a Capability's AC into a TicketSpec.

    Pure function — does not touch disk. Maps the capability's structured
    fields onto the feature work-type field names:

    * ``summary`` ← capability summary
    * ``behaviors`` ← capability behaviors, each serialised as a dict
    * ``acceptance_criteria`` ← capability-level AC if populated; otherwise
      a flattened concatenation of every ``behaviors[*].acceptance_criteria``
      so the work-type schema's "AC must be present" invariant holds
      even when the spec author put all AC inside behaviors.
    * ``out_of_scope`` ← capability ``excluded`` items

    Returns a ``TicketSpec`` ready for ``save_ticket_spec``, which is
    where schema validation against ``work_type`` happens. Callers that
    catch ``SpecValidationError`` get best-effort semantics — a spec is
    written only when it satisfies the work-type schema.
    """
    acceptance_criteria: list[str] = list(capability.acceptance_criteria)
    if not acceptance_criteria:
        for behavior in capability.behaviors:
            acceptance_criteria.extend(behavior.acceptance_criteria)
    fields: dict[str, Any] = {
        "summary": capability.summary,
        "behaviors": [b.model_dump(mode="json") for b in capability.behaviors],
        "acceptance_criteria": acceptance_criteria,
        "out_of_scope": list(capability.excluded),
    }
    return TicketSpec(
        ticket_id=ticket_id,
        work_type=work_type,
        size=size,
        fields=fields,
    )


def load_ticket_spec(project_path: Path, ticket_id: str) -> TicketSpec | None:
    """Load a ticket spec if one exists; otherwise None."""
    path = _spec_path(project_path, ticket_id)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return TicketSpec.model_validate(data)


def save_ticket_spec(
    project_path: Path,
    spec: TicketSpec,
    *,
    bump_version: bool = True,
    enforce_required_fields: bool = True,
) -> TicketSpec:
    """Validate against the work-type schema, then write.

    Schema is resolved via ``load_work_type_schema`` so project-layer
    overrides win. ``bump_version=False`` is used when the on-disk
    version is already authoritative (e.g., proposal-accept paths that
    computed the new version themselves).

    ``enforce_required_fields=False`` skips only the size-based
    "missing required field" check. The unknown-fields check still
    runs, so a misuse that tries to write fields the work-type schema
    doesn't declare will still fail loudly. The only intended caller
    is the capability materialiser, which writes whatever fields the
    project-spec capability carried — L/XL feature tickets would
    otherwise be rejected for missing ``design`` / ``technical_risks``
    (the capability never carries those). Proposal-accept and
    operator-edit paths always enforce required fields.
    """
    schema = load_work_type_schema(project_path, spec.work_type.value)
    _validate_against_schema(
        spec, schema, enforce_required_fields=enforce_required_fields
    )

    if bump_version:
        existing = load_ticket_spec(project_path, spec.ticket_id)
        if existing is not None:
            spec = spec.model_copy(update={"version": existing.version + 1})
    spec = spec.model_copy(update={"updated_at": datetime.now(timezone.utc)})

    _specs_dir(project_path).mkdir(parents=True, exist_ok=True)
    _spec_path(project_path, spec.ticket_id).write_text(
        yaml.safe_dump(
            spec.model_dump(mode="json"),
            default_flow_style=False,
            sort_keys=False,
        )
    )
    return spec


def delete_ticket_spec(project_path: Path, ticket_id: str) -> bool:
    """Remove a ticket spec; return True if something was removed."""
    path = _spec_path(project_path, ticket_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_ticket_specs(project_path: Path) -> list[str]:
    """Ticket ids with a spec file on disk, sorted."""
    d = _specs_dir(project_path)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


__all__ = [
    "SpecValidationError",
    "TicketSpec",
    "delete_ticket_spec",
    "list_ticket_specs",
    "load_ticket_spec",
    "materialize_ticket_spec_from_capability",
    "save_ticket_spec",
]
