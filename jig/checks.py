"""Check catalog — ``.jig/checks.yaml``.

Phase 2 Task E: shape-only. We parse the catalog, validate per-check
shape, and surface the set of check names for cross-reference
validation (Phase 2F). We do NOT execute checks — that's Phase 5 per
docs/10-verification.md.

Three flavors per doc 10:

* ``scripted`` — runs a command; verdict from exit code.
* ``implementation_aware_agent`` — agent with full code visibility.
* ``black_box_agent`` — agent with the implementation hidden.

All three share ``severity`` (required / warning / info) and
``timeout_s``; the agent flavors add ``template``, ``context``, and
``max_tokens``. ``black_box_agent`` additionally carries ``excluded``
paths that the context resolver must refuse to serve.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, Field, RootModel


class CheckSeverity(str, Enum):
    REQUIRED = "required"
    WARNING = "warning"
    INFO = "info"


class _CheckBase(BaseModel):
    severity: CheckSeverity = CheckSeverity.REQUIRED
    timeout_s: int = 600


class ScriptedCheck(_CheckBase):
    type: Literal["scripted"]
    command: str
    working_dir: str = "."


class ImplementationAwareAgentCheck(_CheckBase):
    type: Literal["implementation_aware_agent"]
    template: str
    context: list[str] = Field(default_factory=list)
    max_tokens: int = 50_000


class BlackBoxAgentCheck(_CheckBase):
    type: Literal["black_box_agent"]
    template: str
    context: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    max_tokens: int = 80_000


Check = Annotated[
    Union[ScriptedCheck, ImplementationAwareAgentCheck, BlackBoxAgentCheck],
    Field(discriminator="type"),
]


class CheckCatalog(RootModel[dict[str, Check]]):
    """Top-level ``checks:`` mapping from ``.jig/checks.yaml``."""

    def names(self) -> list[str]:
        return sorted(self.root)

    def get(self, name: str) -> Check | None:
        return self.root.get(name)


def _checks_file(project_path: Path) -> Path:
    return project_path / ".jig" / "checks.yaml"


def load_check_catalog(project_path: Path) -> CheckCatalog:
    """Load ``.jig/checks.yaml`` and return a validated catalog.

    Missing file → empty catalog. Present but empty / ``{checks: null}``
    / ``{checks: {}}`` all normalize to an empty catalog. Any unknown
    check ``type`` or missing required field surfaces as a pydantic
    ``ValidationError`` for Phase 2F to catch at load.
    """
    path = _checks_file(project_path)
    if not path.is_file():
        return CheckCatalog({})
    raw = yaml.safe_load(path.read_text()) or {}
    checks_block = raw.get("checks") or {}
    return CheckCatalog.model_validate(checks_block)


__all__ = [
    "BlackBoxAgentCheck",
    "Check",
    "CheckCatalog",
    "CheckSeverity",
    "ImplementationAwareAgentCheck",
    "ScriptedCheck",
    "load_check_catalog",
]
