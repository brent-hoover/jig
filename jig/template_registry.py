"""Template metadata registry.

Each template directory under ``jig/defaults/project_templates/``
carries a ``template.yaml`` describing its language, framework, and
deploy target. This metadata populates ``architecture.yaml`` on both
the SA and direct paths.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

_TEMPLATES_DIR = Path(__file__).resolve().parent / "defaults" / "project_templates"


class TemplateMetadata(BaseModel):
    name: str
    description: str = ""
    language: str
    framework: str | None = None
    deploy_target: str | None = None
    package_manager: str = ""


def _templates_root() -> Path:
    return _TEMPLATES_DIR


def list_templates() -> list[str]:
    """Return the sorted names of installed templates."""
    root = _templates_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def load_template_metadata(name: str) -> TemplateMetadata:
    """Load the metadata for a template. Raises KeyError if the
    template does not exist; ValueError if it has no template.yaml.
    """
    root = _templates_root()
    tpl_dir = root / name
    if not tpl_dir.is_dir():
        raise KeyError(f"unknown template: {name!r}")
    meta_path = tpl_dir / "template.yaml"
    if not meta_path.is_file():
        raise ValueError(
            f"template {name!r} missing template.yaml at {meta_path}"
        )
    data = yaml.safe_load(meta_path.read_text()) or {}
    data.setdefault("name", name)
    return TemplateMetadata.model_validate(data)
