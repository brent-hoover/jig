"""Spike 2 (#202) — derive the actual dependency graph from code via grimp.

``derive_actual_graph(code_path)`` runs grimp over the package at ``code_path``
and returns the reconciliation ``DependencyGraph`` (module nodes + intra-package
import edges). Tested on a synthetic package fixture (deterministic) plus a smoke
test over Jig's own code (the dogfood target).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jig.engines.reconciliation import DependencyGraph, derive_actual_graph


def _write_package(root: Path, name: str, modules: dict[str, str]) -> Path:
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for mod, body in modules.items():
        (pkg / f"{mod}.py").write_text(body)
    return pkg


def test_derive_returns_a_dependency_graph(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "demo", {"a": "x = 1\n"})
    assert isinstance(derive_actual_graph(pkg), DependencyGraph)


def test_derive_captures_intra_package_import_edges(tmp_path: Path) -> None:
    pkg = _write_package(
        tmp_path,
        "demo",
        {
            "a": "from demo import b\n",  # a imports module b
            "b": "x = 1\n",
        },
    )
    graph = derive_actual_graph(pkg)

    assert "demo.a" in graph.nodes
    assert "demo.b" in graph.nodes
    assert ("demo.a", "demo.b") in graph.edges


def test_derive_resolves_from_package_import_module_correctly(tmp_path: Path) -> None:
    # The ambiguity a naive ast walk gets wrong: `from demo.sub import leaf`
    # imports the MODULE demo.sub.leaf, not a name.
    pkg = _write_package(tmp_path, "demo", {"a": "from demo.sub import leaf\n"})
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "leaf.py").write_text("y = 2\n")

    graph = derive_actual_graph(pkg)

    assert ("demo.a", "demo.sub.leaf") in graph.edges


def test_derive_excludes_external_imports(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "demo", {"a": "import os\nimport sys\n"})
    graph = derive_actual_graph(pkg)
    # Only intra-package nodes; stdlib/external are not nodes.
    assert all(n.startswith("demo") for n in graph.nodes)


def test_derive_honors_explicit_path_over_a_shadowing_sys_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two packages both named "demo". The shadow (no edges) sits on sys.path
    # BEFORE the target's parent. derive must analyze the package at the path it
    # was given, not the shadowed one.
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    _write_package(shadow, "demo", {"a": "x = 1\n"})

    target = tmp_path / "target"
    target.mkdir()
    _write_package(target, "demo", {"a": "from demo import b\n", "b": "y = 2\n"})

    monkeypatch.syspath_prepend(str(target))  # target's parent already on path...
    monkeypatch.syspath_prepend(str(shadow))  # ...but shadow is earlier

    graph = derive_actual_graph(target / "demo")

    assert ("demo.a", "demo.b") in graph.edges  # the explicit target, not shadow


def test_derive_restores_sys_path_exactly(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "demo", {"a": "x = 1\n"})
    before = list(sys.path)
    derive_actual_graph(pkg)
    assert sys.path == before


def test_derive_on_jig_itself_is_the_dogfood_target() -> None:
    # Smoke test over Jig's own code (the Reconciliation dogfood).
    jig_pkg = Path(__file__).resolve().parents[3] / "jig"
    graph = derive_actual_graph(jig_pkg)

    assert "jig.orchestrator" in graph.nodes
    # jig.config is imported broadly and stable across the migration (unlike
    # jig.agent, which Epic 3 MVP routes behind the runtime seam).
    assert ("jig.orchestrator", "jig.config") in graph.edges
    assert len(graph.nodes) > 100  # the real package is large
