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
    # Whether this check is eligible to run from the project-wide git
    # ``pre-commit`` hook. Defaults to ``False`` so the shipped catalog
    # (designed for slow handoff-phase gates like full pytest / mypy)
    # doesn't accidentally fire on every commit. Workflow-phase gates
    # read from ``automated_checks`` and ignore this flag; only the
    # repo-wide pre-commit hook in ``jig.hooks.run_pre_commit`` filters
    # on it.
    hook_eligible: bool = False


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


def _defaults_checks_file() -> Path:
    """Path to the shipped catalog inside the ``jig.defaults`` package.

    Mirrors the ``_role_path_shipped`` / ``_profile_path_shipped``
    pattern: the file lives next to the loader's parent package so it
    travels with the wheel.
    """
    # ``jig/checks.py`` → ``jig/defaults/checks.yaml``.
    return Path(__file__).resolve().parent / "defaults" / "checks.yaml"


def load_check_catalog(project_path: Path) -> CheckCatalog:
    """Load the check catalog, preferring project-local over shipped defaults.

    Resolution order:

    1. ``.jig/checks.yaml`` (project-local) when present and non-empty.
    2. ``jig/defaults/checks.yaml`` (shipped) when project-local is
       missing or empty.
    3. Empty catalog when neither is found.

    Project-local override wins outright — there's no key-by-key merge.
    Matches the existing role / workflow / profile precedence semantics
    so operators have one mental model.

    Any unknown check ``type`` or missing required field surfaces as a
    pydantic ``ValidationError``.
    """

    def _read(path: Path) -> dict | None:
        if not path.is_file():
            return None
        raw = yaml.safe_load(path.read_text()) or {}
        return raw.get("checks") or {}

    project_block = _read(_checks_file(project_path))
    if project_block:
        return CheckCatalog.model_validate(project_block)
    shipped_block = _read(_defaults_checks_file())
    if shipped_block is not None:
        return CheckCatalog.model_validate(shipped_block)
    return CheckCatalog({})


__all__ = [
    "BlackBoxAgentCheck",
    "Check",
    "CheckCatalog",
    "CheckSeverity",
    "ImplementationAwareAgentCheck",
    "ScriptedCheck",
    "load_check_catalog",
]
