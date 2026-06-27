"""Spike 2 (#202) — derive the actual dependency graph from code via grimp.

``derive_actual_graph(code_path)`` runs grimp over the package at ``code_path``
and returns the reconciliation ``DependencyGraph`` (module nodes + intra-package
import edges). Tested on a synthetic package fixture (deterministic) plus a smoke
test over Jig's own code (the dogfood target).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from jig.engines.reconciliation import DependencyGraph, derive_actual_graph


def _write_package(root: Path, name: str, modules: dict[str, str]) -> Path:
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for mod, body in modules.items():
        (pkg / f"{mod}.py").write_text(textwrap.dedent(body))
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


def test_derive_on_jig_itself_is_the_dogfood_target() -> None:
    # Smoke test over Jig's own code (the Reconciliation dogfood).
    jig_pkg = Path(__file__).resolve().parents[3] / "jig"
    graph = derive_actual_graph(jig_pkg)

    assert "jig.orchestrator" in graph.nodes
    assert ("jig.orchestrator", "jig.agent") in graph.edges
    assert len(graph.nodes) > 100  # the real package is large
