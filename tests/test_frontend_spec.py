"""Tests for the VD frontend.yaml schema + load/save helpers (Track D MVP)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.intent import ComplicationsConsidered, Intent
from jig.schemas.frontend import FrontendSpec, FrontendStack
from jig.spec_loader import (
    frontend_spec_path,
    load_frontend_spec,
    save_frontend_spec,
)


def _intent() -> Intent:
    return Intent(
        problem="Project needs a declared frontend stack so dev agents know what to author against.",
        simplest_solution="Take the default minimal stack — HTMX + Alpine + custom utility CSS.",
        complications_considered=ComplicationsConsidered(),
    )


class TestFrontendStackDefaults:
    def test_defaults_are_minimal_stack(self) -> None:
        """Per design.md the default stack is HTMX + Alpine + custom utility CSS, no bundler."""
        stack = FrontendStack()
        assert stack.framework == "htmx_alpine"
        assert stack.language == "python_jinja"
        assert stack.bundler == "none"
        assert stack.css == "custom_utility"
        assert stack.notes is None

    def test_react_override_validates(self) -> None:
        stack = FrontendStack(
            framework="react",
            language="typescript",
            bundler="vite",
            css="tailwind",
            notes="Existing team has 5 years React expertise.",
        )
        assert stack.framework == "react"
        assert stack.bundler == "vite"

    def test_unknown_framework_raises(self) -> None:
        with pytest.raises(Exception):  # pydantic ValidationError
            FrontendStack(framework="solid")


class TestFrontendSpec:
    def test_intent_is_required(self) -> None:
        with pytest.raises(Exception):
            FrontendSpec()

    def test_minimal_spec_takes_default_stack(self) -> None:
        spec = FrontendSpec(intent=_intent())
        assert spec.spec_version == 1
        assert spec.stack.framework == "htmx_alpine"
        assert spec.allowed_dependencies == []
        assert spec.intent.problem.startswith("Project needs")

    def test_round_trip_via_yaml_dict(self) -> None:
        spec = FrontendSpec(
            intent=_intent(),
            allowed_dependencies=["htmx", "alpinejs"],
            stack=FrontendStack(
                framework="htmx_alpine",
                language="python_jinja",
                bundler="none",
                css="custom_utility",
            ),
        )
        dumped = spec.model_dump(mode="json")
        rebuilt = FrontendSpec.model_validate(dumped)
        assert rebuilt.allowed_dependencies == ["htmx", "alpinejs"]
        assert rebuilt.stack.framework == spec.stack.framework


class TestSpecLoaderHelpers:
    def test_path_helper_points_at_jig_spec(self, tmp_path: Path) -> None:
        assert frontend_spec_path(tmp_path) == (
            tmp_path / ".jig" / "spec" / "frontend.yaml"
        )

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        spec = FrontendSpec(
            intent=_intent(),
            allowed_dependencies=["alpinejs"],
        )
        save_frontend_spec(tmp_path, spec)
        loaded = load_frontend_spec(tmp_path)
        assert loaded.allowed_dependencies == ["alpinejs"]
        assert loaded.stack.framework == "htmx_alpine"

    def test_load_missing_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_frontend_spec(tmp_path)
