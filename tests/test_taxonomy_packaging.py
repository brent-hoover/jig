"""Guard: ``jig/code_quality/taxonomy.yaml`` must ship in the installed wheel,
otherwise ``load_taxonomy()`` fails at runtime in environments that pip-install
jig (CI, Docker image, any sandbox)."""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path


def test_taxonomy_yaml_is_a_packaged_resource() -> None:
    # Works for both source-tree and installed wheel — verifies the file is
    # actually reachable as a package resource (not only because it happens to
    # be adjacent on disk).
    p = resources.files("jig.code_quality").joinpath("taxonomy.yaml")
    assert p.is_file(), f"taxonomy.yaml not packaged: {p}"


def test_pyproject_declares_taxonomy_package_data() -> None:
    # Belt-and-braces: regression-guard the pyproject entry itself, so a
    # future cleanup doesn't silently drop the YAML from the wheel.
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    pkg_data = data["tool"]["setuptools"]["package-data"]
    assert "jig.code_quality" in pkg_data, (
        "pyproject.toml [tool.setuptools.package-data] must list 'jig.code_quality'"
    )
    assert "*.yaml" in pkg_data["jig.code_quality"], (
        "'jig.code_quality' package-data must include '*.yaml'"
    )
