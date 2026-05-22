"""Project-profile YAML schema.

A profile bundles the two decisions that govern a project's
orchestration depth: which SA role runs at init, and which workflow
YAML handles each ticket size. Profiles live at
``jig/defaults/profiles/<name>.yaml`` (defaults) and
``.jig/profiles/<name>.yaml`` (project-local overrides).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProfileWorkflows(BaseModel):
    """Workflow routing inside a profile.

    ``default_by_size`` maps ticket size (``xs``, ``s``, ``m``, ``l``,
    ``xl``) to the workflow YAML name. ``available`` is the allowlist
    of workflow names the profile permits — empty means no
    restriction (any workflow can be referenced explicitly per ticket).
    """

    model_config = ConfigDict(extra="forbid")

    default_by_size: dict[str, str] = Field(default_factory=dict)
    available: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    """Loaded profile YAML.

    The four fields are:

    * ``name`` — must match the filename stem (no enforcement here;
      the loader checks).
    * ``description`` — human-readable summary surfaced in the PM's
      ``needs_info`` profile-selection question.
    * ``sa_role`` — role id used by ``run_sa_conversation`` at init.
      Typical values: ``"sa"`` (small, no contracts) or ``"sa_mvp"``
      (medium, full discovery loop with per-module contracts).
    * ``workflows`` — the routing table.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    sa_role: str
    workflows: ProfileWorkflows
