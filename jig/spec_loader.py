"""Load structured spec artifacts from disk.

Top-level (v1 monolithic):
- ``.jig/spec/project.structured.yaml`` — ``StructuredSpec``

L2 / L3 (v2 multi-level):
- ``.jig/spec/suites.yaml`` — ``SuitesIndex`` (L2 PO authors; L3 PO
  reads to scope its brief)
- ``.jig/spec/suites/<id>/brief.md`` — L3 markdown brief
- ``.jig/spec/suites/<id>/spec.structured.yaml`` — L3 structured spec
  (same shape as v1 ``StructuredSpec``, scoped to one suite)

Thin helpers — no I/O beyond read+parse. Writers live with their authoring
modules (``po_l0_mcp.py``, ``po_l3_mcp.py``).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from jig.schemas.po import SuitesIndex
from jig.spec_schema import StructuredSpec

_SPEC_RELATIVE = Path(".jig") / "spec" / "project.structured.yaml"
_SUITES_INDEX_RELATIVE = Path(".jig") / "spec" / "suites.yaml"


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
