"""VD design-system schemas — tokens / components / brand.

Per ``docs/v2.0/visual-design/design.md`` §"Design system integration": the
design system is **always present** — VD applies a minimal default at
discovery start so wireframes have something to render against. The
``default`` source is a permanent valid state, not a placeholder; a
project can ship through Final on default tokens if the operator never
customizes.

Three artifacts under ``.jig/spec/system/``:

- ``tokens.yaml`` — design tokens (color, spacing, type, shadow,
  radius). Operator-editable.
- ``components.yaml`` — component library + per-component variants.
- ``brand.yaml`` — voice + tone + logo refs.

The schemas here are **simple field shapes**: id + name + value /
description / variants. The full design.md decision matrix (typography
scales, accessibility-required attributes, spacing units) lives in
operator-edited YAML — the schema validates structure, not policy.

The default token / component set lives below the schema (``DEFAULT_*``
constants) so ``load_design_system`` returns the defaults when the
on-disk artifacts are absent. This matches the design's "default is a
permanent valid state" rule.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jig.schemas._validators import validate_kebab_id

__all__ = [
    "Brand",
    "Component",
    "ComponentLibrary",
    "ComponentVariant",
    "DEFAULT_BRAND",
    "DEFAULT_COMPONENTS",
    "DEFAULT_DESIGN_SYSTEM",
    "DEFAULT_TOKENS",
    "DesignSystem",
    "DesignToken",
    "Tokens",
]


class DesignToken(BaseModel):
    """One design-token entry — color / spacing / type / shadow / radius.

    ``kind`` constrains the token to one of the five canonical
    categories the wireframe linter knows about. ``value`` is a free-form
    string because color (``#888``), spacing (``16px``), and type
    (``ui-monospace, monospace``) all have different value shapes; the
    wireframe.css generator interprets per-kind.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case token id")
    kind: Literal["color", "spacing", "type", "shadow", "border-radius"]
    value: str = Field(..., min_length=1)
    description: str | None = None

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "DesignToken.id")


class Tokens(BaseModel):
    """``.jig/spec/system/tokens.yaml`` — the design-token list."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    source: Literal["default", "operator_supplied", "claude_design"] = "default"
    tokens: list[DesignToken] = Field(default_factory=list)


class ComponentVariant(BaseModel):
    """One variant of a component (e.g. button.primary, button.ghost).

    ``description`` is operator-readable prose explaining when to reach
    for this variant; the dev agent + visual_compliance reviewer use it
    for "is this the right variant for this context?" judgment in MVP+.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case variant id")
    description: str | None = None

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "ComponentVariant.id")


class Component(BaseModel):
    """One component spec — one entry in ``components.yaml``.

    The MVP shape is intentionally tiny: id + name + variants. The full
    design.md component spec covers states (default / hover / active /
    disabled / loading), accessibility-required attributes, and sizes —
    those land as the visual reviewer matures past MVP.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case component id")
    name: str = Field(..., min_length=1)
    variants: list[ComponentVariant] = Field(default_factory=list)
    description: str | None = None

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "Component.id")


class ComponentLibrary(BaseModel):
    """``.jig/spec/system/components.yaml`` — the component spec list."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    source: Literal["default", "operator_supplied", "claude_design"] = "default"
    components: list[Component] = Field(default_factory=list)


class Brand(BaseModel):
    """``.jig/spec/system/brand.yaml`` — brand voice + tone + logo.

    All fields default to operator-friendly baselines so a brand-less
    project still validates. ``voice`` / ``tone`` are free-form prose
    (the agent reads them; the schema doesn't constrain word choice).
    ``logo_refs`` carries operator-supplied paths or URIs into the
    references dir.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    source: Literal["default", "operator_supplied", "claude_design"] = "default"
    voice: str = Field(
        default="neutral, plain-spoken, no jargon",
        description="Brand voice — how the product talks.",
    )
    tone: str = Field(
        default="friendly but professional",
        description="Brand tone — emotional register.",
    )
    logo_refs: list[str] = Field(default_factory=list)


class DesignSystem(BaseModel):
    """Aggregate view of the three system artifacts.

    Convenience type for callers that want to load the whole system in
    one read (the per-ticket reference resolver, the visual_compliance
    reviewer). Each sub-artifact stays its own file on disk so the
    operator can edit one without rewriting the others.
    """

    model_config = ConfigDict(extra="forbid")

    tokens: Tokens
    components: ComponentLibrary
    brand: Brand


# ---- shipped defaults ----------------------------------------------------
#
# Per design.md: "default is a permanent valid state, not a placeholder.
# A project can ship through Final on default tokens if the operator
# never customizes." The defaults here are the tiny opinionated greyscale
# + system-ui starter the design specifies. The wireframe.css greyscale
# values mirror the color tokens so the wireframe-mode rendering stays
# consistent with whatever the design system declares.


DEFAULT_TOKENS: Tokens = Tokens(
    source="default",
    tokens=[
        # Color — greyscale baseline. Matches the wireframe.css
        # variables so wireframe mode reads against the same values.
        DesignToken(id="color-bg", kind="color", value="#f5f5f5"),
        DesignToken(id="color-surface", kind="color", value="#ffffff"),
        DesignToken(id="color-primary", kind="color", value="#888888"),
        DesignToken(id="color-on-primary", kind="color", value="#ffffff"),
        DesignToken(id="color-text", kind="color", value="#2a2a2a"),
        DesignToken(id="color-muted", kind="color", value="#888888"),
        DesignToken(id="color-border", kind="color", value="#888888"),
        # Spacing — 4px scale, the eight values the utility classes
        # generate from. Names mirror Tailwind conventions.
        DesignToken(id="space-1", kind="spacing", value="4px"),
        DesignToken(id="space-2", kind="spacing", value="8px"),
        DesignToken(id="space-3", kind="spacing", value="12px"),
        DesignToken(id="space-4", kind="spacing", value="16px"),
        DesignToken(id="space-6", kind="spacing", value="24px"),
        DesignToken(id="space-8", kind="spacing", value="32px"),
        # Type — system stack so a freshly-initialized project on any
        # OS gets readable text without a webfont download.
        DesignToken(
            id="type-sans",
            kind="type",
            value="system-ui, -apple-system, 'Segoe UI', sans-serif",
        ),
        DesignToken(
            id="type-mono",
            kind="type",
            value="ui-monospace, 'SF Mono', Menlo, monospace",
        ),
        # Shadow — single elevation for the default. Operators add
        # more as the design system matures.
        DesignToken(id="shadow-default", kind="shadow", value="none"),
        # Border-radius — flat by default; overrideable per-component.
        DesignToken(id="radius-default", kind="border-radius", value="0"),
    ],
)


DEFAULT_COMPONENTS: ComponentLibrary = ComponentLibrary(
    source="default",
    components=[
        Component(
            id="button",
            name="Button",
            description="Clickable action affordance.",
            variants=[
                ComponentVariant(id="primary", description="The dominant action on a screen."),
                ComponentVariant(id="secondary", description="Subordinate actions."),
                ComponentVariant(id="ghost", description="Tertiary actions; minimal visual weight."),
            ],
        ),
        Component(
            id="input",
            name="Input",
            description="Single-line text entry.",
            variants=[
                ComponentVariant(id="text"),
                ComponentVariant(id="email"),
                ComponentVariant(id="password"),
            ],
        ),
        Component(
            id="card",
            name="Card",
            description="Bounded content container with consistent inner padding.",
            variants=[
                ComponentVariant(id="default"),
                ComponentVariant(id="bordered"),
            ],
        ),
        Component(
            id="layout-grid",
            name="Layout grid",
            description="Top-level page layout container.",
            variants=[
                ComponentVariant(id="single-column"),
                ComponentVariant(id="sidebar-main"),
            ],
        ),
    ],
)


DEFAULT_BRAND: Brand = Brand(source="default")


DEFAULT_DESIGN_SYSTEM: DesignSystem = DesignSystem(
    tokens=DEFAULT_TOKENS,
    components=DEFAULT_COMPONENTS,
    brand=DEFAULT_BRAND,
)
