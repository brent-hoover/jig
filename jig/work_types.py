"""Work-type schemas — ``jig/defaults/work_types/*.yaml`` (shipped) with
project overrides at ``.jig/work_types/*.yaml``.

Phase 3 Task A: shape-only loader. We parse the per-work-type schema
YAML and validate the envelope. We do **not** execute validation of
actual ticket specs against the schema here — ``jig.specs`` (Task C)
uses ``required_fields_for_size(schema, size)`` to enforce at write
time.

Per doc 03 §Schemas as first-class project config a schema declares:

* ``work_type`` — the key this schema applies to (one of the enum
  values in ``jig.ticket.WorkType`` for Phase 3; projects may add new
  enum values in a later phase).
* ``required`` — fields that must always be present.
* ``optional`` — fields that may be present.
* ``required_by_size`` — mapping ``size → list[field]``, raising the
  bar for larger tickets. A size not listed inherits ``required``.
* ``ownership`` — ``field → owner role alias`` (``po`` / ``sa`` / any
  declared role). Used by ``jig.ownership`` (Task F).
* ``section_locks`` — ``field → locked_after_phase``. Parsed here; not
  enforced until Phase 5.

Resolution mirrors Phase 2C: project file at
``<project>/.jig/work_types/<name>.yaml`` wins if present, else the
shipped default at ``jig/defaults/work_types/<name>.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from jig.ticket import Size, WorkType


class WorkTypeSchema(BaseModel):
    """Envelope for a work-type spec schema.

    ``fields`` shape is intentionally loose — callers merge
    ``required`` ∪ ``optional`` to get the allowed field set. We keep
    ``required`` separate because ``required_by_size`` layers on top
    and shouldn't be collapsed.
    """

    work_type: WorkType
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    required_by_size: dict[Size, list[str]] = Field(default_factory=dict)
    ownership: dict[str, str] = Field(default_factory=dict)
    # Value is a dict like ``{"locked_after_phase": "spec-approval"}``
    # so future phases can add new lock types without changing the
    # schema shape.
    section_locks: dict[str, dict[str, str]] = Field(default_factory=dict)

    def allowed_fields(self) -> set[str]:
        """Every field name the schema declares (required ∪ optional)."""
        return set(self.required) | set(self.optional)

    def required_fields_for_size(self, size: Size) -> list[str]:
        """Required field list for a given size.

        If ``required_by_size`` has an entry for ``size`` it wins
        outright (doc 03 §Size scales field rigor — size can shrink
        the required set for XS tickets too). Otherwise fall back to
        ``required``.
        """
        if size in self.required_by_size:
            return list(self.required_by_size[size])
        return list(self.required)


# ---- file layout ----------------------------------------------------------


def _project_dir(project_path: Path) -> Path:
    return project_path / ".jig" / "work_types"


def _shipped_dir() -> Path:
    return Path(__file__).resolve().parent / "defaults" / "work_types"


def _project_path(project_path: Path, name: str) -> Path:
    return _project_dir(project_path) / f"{name}.yaml"


def _shipped_path(name: str) -> Path:
    return _shipped_dir() / f"{name}.yaml"


# ---- loader / list --------------------------------------------------------


def load_work_type_schema(
    project_path: Path, name: str
) -> WorkTypeSchema:
    """Load a work-type schema by name.

    Project override beats shipped default. Missing in both layers
    raises ``FileNotFoundError`` naming both paths (Phase 2C
    convention).
    """
    project_file = _project_path(project_path, name)
    if project_file.is_file():
        data = yaml.safe_load(project_file.read_text()) or {}
        return WorkTypeSchema.model_validate(data)
    shipped_file = _shipped_path(name)
    if shipped_file.is_file():
        data = yaml.safe_load(shipped_file.read_text()) or {}
        return WorkTypeSchema.model_validate(data)
    raise FileNotFoundError(
        f"work-type schema {name!r} not found "
        f"(looked in {project_file} and {shipped_file})"
    )


def list_work_type_names(project_path: Path) -> list[str]:
    """Every work-type schema name resolvable by this project."""
    names: set[str] = set()
    project_dir = _project_dir(project_path)
    if project_dir.is_dir():
        names.update(p.stem for p in project_dir.glob("*.yaml"))
    shipped = _shipped_dir()
    if shipped.is_dir():
        names.update(p.stem for p in shipped.glob("*.yaml"))
    return sorted(names)


def list_work_type_schemas(project_path: Path) -> list[WorkTypeSchema]:
    """All schemas, project layer shadowing shipped defaults."""
    seen: dict[str, WorkTypeSchema] = {}
    project_dir = _project_dir(project_path)
    if project_dir.is_dir():
        for f in sorted(project_dir.glob("*.yaml")):
            data = yaml.safe_load(f.read_text()) or {}
            schema = WorkTypeSchema.model_validate(data)
            seen[schema.work_type.value] = schema
    shipped = _shipped_dir()
    if shipped.is_dir():
        for f in sorted(shipped.glob("*.yaml")):
            data = yaml.safe_load(f.read_text()) or {}
            schema = WorkTypeSchema.model_validate(data)
            seen.setdefault(schema.work_type.value, schema)
    return [seen[k] for k in sorted(seen)]


__all__ = [
    "WorkTypeSchema",
    "list_work_type_names",
    "list_work_type_schemas",
    "load_work_type_schema",
]
