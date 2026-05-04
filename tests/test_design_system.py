"""Tests for the VD design-system schemas + loaders (Track D MVP)."""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.schemas.design_system import (
    DEFAULT_BRAND,
    DEFAULT_COMPONENTS,
    DEFAULT_DESIGN_SYSTEM,
    DEFAULT_TOKENS,
    Brand,
    Component,
    ComponentLibrary,
    ComponentVariant,
    DesignSystem,
    DesignToken,
    Tokens,
)
from jig.spec_loader import (
    brand_path,
    components_path,
    load_brand,
    load_components,
    load_design_system,
    load_tokens,
    save_brand,
    save_components,
    save_tokens,
    system_dir,
    tokens_path,
)


class TestTokenSchema:
    def test_design_token_kinds_constrained(self) -> None:
        t = DesignToken(id="color-primary", kind="color", value="#888")
        assert t.kind == "color"

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(Exception):  # pydantic ValidationError
            DesignToken(id="x", kind="motion", value="ease-out")

    def test_round_trip(self) -> None:
        tokens = Tokens(
            source="operator_supplied",
            tokens=[
                DesignToken(id="color-primary", kind="color", value="#0a66c2"),
                DesignToken(id="space-4", kind="spacing", value="16px"),
            ],
        )
        rebuilt = Tokens.model_validate(tokens.model_dump(mode="json"))
        assert len(rebuilt.tokens) == 2


class TestComponentSchema:
    def test_component_with_variants(self) -> None:
        c = Component(
            id="button",
            name="Button",
            variants=[ComponentVariant(id="primary"), ComponentVariant(id="ghost")],
        )
        assert {v.id for v in c.variants} == {"primary", "ghost"}

    def test_component_library_round_trip(self) -> None:
        lib = ComponentLibrary(
            components=[Component(id="card", name="Card")]
        )
        rebuilt = ComponentLibrary.model_validate(lib.model_dump(mode="json"))
        assert rebuilt.components[0].name == "Card"


class TestBrandSchema:
    def test_default_brand_has_voice_and_tone(self) -> None:
        b = Brand()
        assert b.voice
        assert b.tone


class TestShippedDefaults:
    """Per design.md the defaults are a permanent valid state, not a placeholder."""

    def test_default_tokens_cover_canonical_kinds(self) -> None:
        kinds = {t.kind for t in DEFAULT_TOKENS.tokens}
        assert {"color", "spacing", "type", "shadow", "border-radius"}.issubset(kinds)

    def test_default_components_include_the_starter_four(self) -> None:
        ids = {c.id for c in DEFAULT_COMPONENTS.components}
        # Per the deliverable spec — "button, input, card, layout-grid".
        assert {"button", "input", "card", "layout-grid"}.issubset(ids)

    def test_default_brand_is_default_source(self) -> None:
        assert DEFAULT_BRAND.source == "default"

    def test_default_design_system_aggregates(self) -> None:
        ds = DEFAULT_DESIGN_SYSTEM
        assert ds.tokens is DEFAULT_TOKENS
        assert ds.components is DEFAULT_COMPONENTS
        assert ds.brand is DEFAULT_BRAND


class TestLoadersFallBackToDefaults:
    """`default` is a permanent valid state — loaders return it when absent."""

    def test_path_helpers_match_design(self, tmp_path: Path) -> None:
        assert system_dir(tmp_path) == tmp_path / ".jig" / "spec" / "system"
        assert tokens_path(tmp_path).name == "tokens.yaml"
        assert components_path(tmp_path).name == "components.yaml"
        assert brand_path(tmp_path).name == "brand.yaml"

    def test_load_tokens_absent_returns_default(self, tmp_path: Path) -> None:
        loaded = load_tokens(tmp_path)
        assert loaded.source == "default"
        assert len(loaded.tokens) == len(DEFAULT_TOKENS.tokens)

    def test_load_components_absent_returns_default(self, tmp_path: Path) -> None:
        loaded = load_components(tmp_path)
        assert loaded.source == "default"

    def test_load_brand_absent_returns_default(self, tmp_path: Path) -> None:
        loaded = load_brand(tmp_path)
        assert loaded.source == "default"

    def test_load_design_system_absent_returns_full_default(
        self, tmp_path: Path
    ) -> None:
        ds = load_design_system(tmp_path)
        assert ds.tokens.source == "default"
        assert ds.components.source == "default"
        assert ds.brand.source == "default"


class TestLoadersReadFromDisk:
    def test_save_then_load_tokens_round_trip(self, tmp_path: Path) -> None:
        tokens = Tokens(
            source="operator_supplied",
            tokens=[
                DesignToken(id="color-primary", kind="color", value="#0a66c2"),
            ],
        )
        save_tokens(tmp_path, tokens)
        loaded = load_tokens(tmp_path)
        assert loaded.source == "operator_supplied"
        assert loaded.tokens[0].value == "#0a66c2"

    def test_save_then_load_components_round_trip(self, tmp_path: Path) -> None:
        lib = ComponentLibrary(
            source="operator_supplied",
            components=[Component(id="modal", name="Modal")],
        )
        save_components(tmp_path, lib)
        loaded = load_components(tmp_path)
        assert loaded.source == "operator_supplied"
        assert loaded.components[0].id == "modal"

    def test_save_then_load_brand_round_trip(self, tmp_path: Path) -> None:
        b = Brand(source="operator_supplied", voice="bold and direct")
        save_brand(tmp_path, b)
        loaded = load_brand(tmp_path)
        assert loaded.voice == "bold and direct"

    def test_partial_disk_state_mixes_defaults(self, tmp_path: Path) -> None:
        """Operator authored tokens but not components / brand — loader fills with defaults."""
        save_tokens(
            tmp_path,
            Tokens(
                source="operator_supplied",
                tokens=[DesignToken(id="color-primary", kind="color", value="#000")],
            ),
        )
        ds = load_design_system(tmp_path)
        assert ds.tokens.source == "operator_supplied"
        assert ds.components.source == "default"
        assert ds.brand.source == "default"


class TestDesignSystemAggregate:
    def test_design_system_round_trip(self) -> None:
        ds = DesignSystem(
            tokens=DEFAULT_TOKENS,
            components=DEFAULT_COMPONENTS,
            brand=DEFAULT_BRAND,
        )
        rebuilt = DesignSystem.model_validate(ds.model_dump(mode="json"))
        assert rebuilt.tokens.source == "default"
