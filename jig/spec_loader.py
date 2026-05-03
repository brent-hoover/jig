"""Load the structured project spec from disk.

Thin helper used by the URI dispatcher and any other caller that needs the
spec but doesn't already have it loaded.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from jig.spec_schema import StructuredSpec

_SPEC_RELATIVE = Path(".jig") / "spec" / "project.structured.yaml"


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
