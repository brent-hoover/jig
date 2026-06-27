"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."

Enforced as **default-deny** two complementary ways:

- ``test_edge_imports_only_edge_appropriate_modules`` — a runtime ``sys.modules``
  check after importing the whole package (module-level + *transitive* imports).
- ``test_edge_has_no_forbidden_import_statements`` — a static AST scan of every
  ``jig/edge/*.py`` import statement, including ``from jig import orchestrator``
  forms and *lazy* (function-local) imports a runtime check would miss.

Only edge-appropriate ``jig`` modules are allowed; everything else (every engine,
store, MCP handler, workflow, agent, domain type) is a violation by default. See
``architecture/edge-audit.md``.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import jig.edge

# The ONLY jig modules the edge may import (architecture/edge-audit.md "keep"
# list). MVP expands this deliberately as it routes CLI/TUI through the daemon
# API. Everything else under jig.* is an engine internal.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "jig.edge",
    "jig.daemon",
    "jig.ws_server",
    "jig.config",
    "jig.events",
    "jig.logging_setup",
    "jig.safe_path",
    "jig.container",
    "jig.dev_env",
    "jig.story",
    "jig.tui",
    "jig.issues.cli",
    "jig.sim.cli",
)


def _allowed_parents(prefixes: tuple[str, ...]) -> frozenset[str]:
    """Parent packages of allowlisted leaves (e.g. ``jig.issues`` for
    ``jig.issues.cli``). Used by the RUNTIME check only: importing a leaf loads
    its parent package as a side effect, but a sibling (``jig.issues.mcp``) stays
    forbidden. The static scan does NOT permit these — a written ``import
    jig.issues`` must be an explicit leaf."""
    parents: set[str] = set()
    for prefix in prefixes:
        parts = prefix.split(".")
        for i in range(2, len(parts)):  # skip bare "jig"
            parents.add(".".join(parts[:i]))
    return frozenset(parents)


# Exact-match parents permitted (package init only, not their other contents).
_ALLOWED_PARENTS = _allowed_parents(_ALLOWED_PREFIXES)


def _under_allowed_prefix(module: str) -> bool:
    """A statically-written import must target an explicit allowlisted module
    (or the edge subtree) — NOT a bare parent like ``jig.issues``, whose package
    init could pull in forbidden internals."""
    return any(module == a or module.startswith(a + ".") for a in _ALLOWED_PREFIXES)


def test_edge_imports_only_edge_appropriate_modules() -> None:
    """Runtime check — module-level + transitive imports."""
    code = (
        "import importlib, pkgutil, sys, jig.edge\n"
        "for m in pkgutil.walk_packages(jig.edge.__path__, prefix='jig.edge.'):\n"
        "    importlib.import_module(m.name)\n"
        f"allowed = {_ALLOWED_PREFIXES!r}\n"
        f"parents = {set(_ALLOWED_PARENTS)!r}\n"
        "def ok(m):\n"
        "    return m in parents or any(m == a or m.startswith(a + '.') for a in allowed)\n"
        "leaked = sorted(m for m in sys.modules if m.startswith('jig.') and not ok(m))\n"
        "print(leaked)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", (
        f"jig.edge imported non-edge-appropriate modules: {out.stdout}"
    )


def test_edge_has_no_forbidden_import_statements() -> None:
    """Static check — every import statement: absolute, ``from jig import X``,
    relative (``from ..orchestrator import X``), and lazy/function-local."""
    pkg_dir = pathlib.Path(jig.edge.__file__).parent
    jig_root = pkg_dir.parent  # the jig/ directory
    offenders: list[str] = []

    for py in sorted(pkg_dir.rglob("*.py")):
        # The package containing this module (drop the module name / __init__).
        module_parts = ("jig", *py.relative_to(jig_root).with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(py.read_text(), filename=str(py))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base = node.module or ""
                else:
                    # Resolve relative imports against this module's package.
                    kept = package_parts[: len(package_parts) - (node.level - 1)]
                    base = ".".join(kept)
                    if node.module:
                        base = f"{base}.{node.module}" if base else node.module
                # Expand `from base import a, b` -> base.a, base.b so that
                # `from jig import orchestrator` is caught, not just `base`.
                candidates = [base] if base else []
                candidates += [
                    f"{base}.{alias.name}" if base else alias.name
                    for alias in node.names
                ]
            else:
                continue

            for module in candidates:
                if module.startswith("jig.") and not _under_allowed_prefix(module):
                    offenders.append(f"{py.name}:{node.lineno} -> {module}")

    assert offenders == [], f"forbidden imports in jig/edge/: {offenders}"
