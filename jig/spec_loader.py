"""Load structured spec artifacts from disk.

Top-level (v1 monolithic):
- ``.jig/spec/project.structured.yaml`` — ``StructuredSpec``

L2 / L3 (v2 multi-level):
- ``.jig/spec/suites.yaml`` — ``SuitesIndex`` (L2 PO authors; L3 PO
  reads to scope its brief)
- ``.jig/spec/suites/<id>/brief.md`` — L3 markdown brief
- ``.jig/spec/suites/<id>/spec.structured.yaml`` — L3 structured spec
  (same shape as v1 ``StructuredSpec``, scoped to one suite)

SA (v2):
- ``.jig/spec/architecture.yaml`` — project-level ``Architecture``
- ``.jig/spec/modules/<m>/contracts.yaml`` — per-module ``ContractsFile``

PM (v2):
- ``.jig/plan/build-plan.yaml`` — project-level ``BuildPlan`` (Track F1)

Thin helpers — no I/O beyond read+parse. Writers live with their authoring
modules (``po_l0_mcp.py``, ``po_l3_mcp.py``, ``sa_mcp.py``); the build-plan
writer is co-located here because Track F bones has no PM agent yet — the
synthetic operator calls ``write_build_plan`` directly.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from jig.atomic import atomic_write_text
from jig.schemas.arch import Architecture, ContractsFile
from jig.schemas.plan import BuildPlan
from jig.schemas.po import SuitesIndex
from jig.spec_schema import StructuredSpec

_SPEC_RELATIVE = Path(".jig") / "spec" / "project.structured.yaml"
_SUITES_INDEX_RELATIVE = Path(".jig") / "spec" / "suites.yaml"
_ARCHITECTURE_RELATIVE = Path(".jig") / "spec" / "architecture.yaml"
_BUILD_PLAN_RELATIVE = Path(".jig") / "plan" / "build-plan.yaml"


def spec_path(project_root: Path) -> Path:
    """Return the on-disk path to the structured spec for a project root."""
    return project_root / _SPEC_RELATIVE


def load_structured_spec(project_root: Path) -> tuple[StructuredSpec, Path]:
    """Load and validate the structured spec.

    Returns ``(spec, source_path)``. Raises ``FileNotFoundError`` if the
    spec file is missing.
    """
    src = spec_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"structured spec not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return StructuredSpec.model_validate(data), src


# ---- v2 multi-level paths -------------------------------------------------


def suites_index_path(project_root: Path) -> Path:
    """``.jig/spec/suites.yaml`` — the L2 suite index."""
    return project_root / _SUITES_INDEX_RELATIVE


def load_suites_index(project_root: Path) -> SuitesIndex:
    """Load and validate ``suites.yaml``.

    Raises ``FileNotFoundError`` if absent — the L3 PO is dependent on
    L2 having written it first, so absence is an error rather than a
    silent empty default.
    """
    src = suites_index_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"suites index not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return SuitesIndex.model_validate(data)


def suite_dir(project_root: Path, suite_id: str) -> Path:
    """``.jig/spec/suites/<suite_id>/`` — the per-suite artifact dir."""
    return project_root / ".jig" / "spec" / "suites" / suite_id


def suite_brief_path(project_root: Path, suite_id: str) -> Path:
    """``.jig/spec/suites/<suite_id>/brief.md``."""
    return suite_dir(project_root, suite_id) / "brief.md"


def suite_structured_path(project_root: Path, suite_id: str) -> Path:
    """``.jig/spec/suites/<suite_id>/spec.structured.yaml``."""
    return suite_dir(project_root, suite_id) / "spec.structured.yaml"


# ---- v2 SA paths ----------------------------------------------------------


def architecture_path(project_root: Path) -> Path:
    """``.jig/spec/architecture.yaml`` — the project-level SA artifact.

    v1 ``init_mcp.handle_arch_set_field`` writes a free-form dict to the
    same path; v2 ``Architecture`` is a strict Pydantic shape. The v2
    plan is a clean break (no migration) so the two artifacts don't
    coexist within a single project — only within the codebase.
    """
    return project_root / _ARCHITECTURE_RELATIVE


def load_architecture(project_root: Path) -> Architecture:
    """Load and validate ``architecture.yaml``.

    Raises ``FileNotFoundError`` if absent — the v2 SA writes it from
    scratch on first spawn, so callers that need to read it (reviewers,
    PM planner, etc.) treat absence as "SA hasn't run yet" rather than
    silently defaulting to an empty arch.
    """
    src = architecture_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"architecture.yaml not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return Architecture.model_validate(data)


def module_dir(project_root: Path, module_id: str) -> Path:
    """``.jig/spec/modules/<module_id>/`` — the per-module artifact dir."""
    return project_root / ".jig" / "spec" / "modules" / module_id


def module_contracts_path(project_root: Path, module_id: str) -> Path:
    """``.jig/spec/modules/<module_id>/contracts.yaml``."""
    return module_dir(project_root, module_id) / "contracts.yaml"


def load_module_contracts(project_root: Path, module_id: str) -> ContractsFile:
    """Load and validate one module's ``contracts.yaml``.

    Raises ``FileNotFoundError`` if absent. Same rationale as
    ``load_architecture``: callers that need it treat absence as
    "module not authored yet" rather than silent default.
    """
    src = module_contracts_path(project_root, module_id)
    if not src.is_file():
        raise FileNotFoundError(
            f"contracts.yaml for module {module_id!r} not found at {src}"
        )
    data = yaml.safe_load(src.read_text()) or {}
    return ContractsFile.model_validate(data)


# ---- v2 PM paths ----------------------------------------------------------


def build_plan_path(project_root: Path) -> Path:
    """``.jig/plan/build-plan.yaml`` — the PM's living build plan.

    Track F bones: the synthetic operator hand-writes this file via
    ``write_build_plan`` since the Planner agent (F2) hasn't landed yet.
    The Coordinator (F4) reads it to materialize tickets into the store.
    """
    return project_root / _BUILD_PLAN_RELATIVE


def load_build_plan(project_root: Path) -> BuildPlan:
    """Load and validate ``build-plan.yaml``.

    Raises ``FileNotFoundError`` if absent — the Coordinator treats
    absence as "no plan yet" via try/except rather than silently
    defaulting to an empty plan, mirroring ``load_architecture``.
    """
    src = build_plan_path(project_root)
    if not src.is_file():
        raise FileNotFoundError(f"build-plan.yaml not found at {src}")
    data = yaml.safe_load(src.read_text()) or {}
    return BuildPlan.model_validate(data)


def write_build_plan(project_root: Path, plan: BuildPlan) -> None:
    """Atomically write ``plan`` to ``.jig/plan/build-plan.yaml``.

    Track F1 bones: deterministic key order via ``sort_keys=False`` so
    diffs across writes stay readable for the synthetic operator
    iterating on a scenario. Bones is a one-shot writer with no
    merge/amend logic — the Planner agent (F2) gets that in MVP.
    """
    payload = yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(build_plan_path(project_root), payload)
