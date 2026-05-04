"""VD output schemas — ``.jig/spec/frontend.yaml`` + supporting types.

Per ``docs/visual-design/design.md`` §"Frontend architecture (VD owns
this)": the frontend stack is part of VD's deliverable. The default
minimal stack is HTMX + Alpine + custom utility CSS with no JS bundler;
operators override per-project for React / Vue / Svelte / etc.

The schema is deliberately **flat** — the design.md operator-facing YAML
includes nested ``stack: { rendering, templating, interactivity, ...}``
+ ``components: { pattern, organization, ...}``  + ``accessibility:
{ target, enforced_at }`` blocks. For MVP we collapse the most-load-
bearing fields onto one ``FrontendStack`` model so the agent has fewer
nested structures to author and the linter has fewer paths to walk. The
remaining design.md fields surface as free-form ``notes`` until the
checklist enforcer needs them.

``FrontendSpec`` carries the project's intent layer at the top level so
the same intent-reviewer that gates PO / SA artifacts also gates VD's
top-level spec without per-track special-casing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jig.intent import Intent
from jig.schemas._validators import validate_tz_aware

__all__ = [
    "FrontendSpec",
    "FrontendStack",
]


class FrontendStack(BaseModel):
    """The frontend technology choices for one project.

    Five literal-restricted fields cover the design.md decision matrix:

    - ``framework`` — top-level UI library or its absence. Default
      ``htmx_alpine`` matches the design's no-build-step minimal stack;
      ``react`` / ``vue`` / ``svelte`` cover the operator-override
      branches; ``custom`` opts into a fully bespoke stack the linter
      doesn't know about (operator owns).
    - ``language`` — what the templates / components are written in.
      Default ``python_jinja`` because the minimal HTMX stack assumes
      server-rendered Python templates; React / Vue / Svelte default
      operators flip to ``typescript``.
    - ``bundler`` — the build pipeline. Default ``none`` for the no-
      build-step minimal stack; framework overrides commonly pick
      ``vite``.
    - ``css`` — how styles are authored. Default ``custom_utility``
      matches the hand-written ``wireframe.css`` utility layer; opt
      into ``tailwind`` for the build-step trade-off (per design.md
      §"When to opt into real Tailwind"); ``vanilla`` covers operator-
      authored hand-rolled CSS without the utility set.
    - ``notes`` — free-form prose for operator-supplied rationale or
      stack details the literal fields don't capture (state management
      library, package manager, accessibility target). MVP keeps this
      free-form; checklist enforcement of specific sub-fields lands as
      reviewers mature.
    """

    model_config = ConfigDict(extra="forbid")

    framework: Literal[
        "htmx_alpine", "react", "vue", "svelte", "custom"
    ] = "htmx_alpine"
    language: Literal[
        "typescript", "javascript", "python_jinja"
    ] = "python_jinja"
    bundler: Literal[
        "none", "vite", "esbuild", "webpack"
    ] = "none"
    css: Literal[
        "custom_utility", "tailwind", "vanilla"
    ] = "custom_utility"
    notes: str | None = None


class FrontendSpec(BaseModel):
    """Top-level VD frontend declaration — ``.jig/spec/frontend.yaml``.

    Per design.md §"Frontend architecture (VD owns this)": one file per
    project, set once at VD discovery start, modified rarely. Carries:

    - ``stack`` — the FrontendStack choices (defaults to the minimal
      HTMX + Alpine stack so a freshly-initialized project has a valid
      spec without operator interaction).
    - ``allowed_dependencies`` — npm / pip packages the operator has
      pre-approved for the project. Empty by default; populated as the
      operator confirms additions during the VD walk. Reviewers (post-
      MVP) can gate ``package.json`` / ``pyproject.toml`` additions on
      this allowlist.
    - ``intent`` — the disciplined intent sequence. Required (every v2
      artifact past the early-capture state carries an intent layer per
      ``docs/agent-leverage/problem.md`` §1).
    - ``generated_at`` — UTC timestamp; refreshed by the writer on
      every save so ``jig story`` can surface "spec last touched at X".
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    stack: FrontendStack = Field(default_factory=FrontendStack)
    allowed_dependencies: list[str] = Field(default_factory=list)
    intent: Intent
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("generated_at")
    @classmethod
    def _tz_generated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "FrontendSpec.generated_at")
