"""EDGE bones — the edge boundary invariant (Epic 9 verify).

"No engine module is imported by ``jig/edge/`` except via the API contract."

Bones holds the strictest honest form of this: ``jig.edge`` imports **only
itself** (the contract is plain DTOs). So the allowlist is just ``jig.edge`` — any
other ``jig.*`` module pulled in or written is a violation. Two complementary
checks:

- ``test_edge_imports_only_itself`` — runtime ``sys.modules`` after importing the
  whole package (module-level + *transitive* imports).
- ``test_edge_has_no_forbidden_import_statements`` — static AST scan of every
  ``jig/edge/*.py`` import (absolute, ``from jig import X``, relative, lazy).

MVP expands the allowlist deliberately, one reviewed edge-appropriate dependency
at a time, as it routes CLI/TUI through the daemon API (see
``architecture/edge-audit.md`` for the surface it must retire). Keeping the
allowlist minimal now means the test can never bless a front-door module that is
itself still impure.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import jig.edge

# The only jig package the edge may touch today: itself. (MVP grows this.)
_ALLOWED_PREFIXES: tuple[str, ...] = ("jig.edge",)


def _is_allowed(module: str) -> bool:
    return any(module == a or module.startswith(a + ".") for a in _ALLOWED_PREFIXES)


def test_edge_imports_only_itself() -> None:
    """Runtime check — module-level + transitive imports."""
    code = (
        "import importlib, pkgutil, sys, jig.edge\n"
        "for m in pkgutil.walk_packages(jig.edge.__path__, prefix='jig.edge.'):\n"
        "    importlib.import_module(m.name)\n"
        f"allowed = {_ALLOWED_PREFIXES!r}\n"
        "def ok(m):\n"
        "    return any(m == a or m.startswith(a + '.') for a in allowed)\n"
        "leaked = sorted(m for m in sys.modules if m.startswith('jig.') and not ok(m))\n"
        "print(leaked)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", (
        f"jig.edge imported non-edge modules: {out.stdout}"
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
                if module.startswith("jig.") and not _is_allowed(module):
                    offenders.append(f"{py.name}:{node.lineno} -> {module}")

    assert offenders == [], f"forbidden imports in jig/edge/: {offenders}"
