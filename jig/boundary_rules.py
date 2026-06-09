"""Compile module-boundary declarations into semgrep deny rules.

Step 2a pins the import-matching pattern set; the generator (step 2b) builds
on it. The patterns below were verified against semgrep 1.164.0 (see
``tests/test_boundary_rules.py``) to match every Python import form of a
forbidden package:

- ``import pkg`` / ``import pkg.sub`` / ``import pkg as p``
- ``from pkg import x`` / ``from pkg.sub import x``
- and, for a *dotted* package, the parent form ``from parent import leaf``
  (e.g. ``from ats import billing`` for forbidden ``ats.billing``).

semgrep matches patterns, not their absence, so enforcement is deny-list
based; allow-lists are compiled into concrete deny targets by the generator.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from jig.atomic import atomic_write_text
from jig.config import load_config
from jig.schemas.arch import BoundariesFile
from jig.spec_loader import load_architecture


def _to_snake(name: str) -> str:
    """kebab/space → snake, lowercased — the scaffolder's package-name
    derivation (init_workflow.py:1535)."""
    return name.replace("-", "_").replace(" ", "_").lower()


def _sanitize(token: str) -> str:
    """Make a rule-id-safe token from a package/module name."""
    return re.sub(r"[^a-z0-9]+", "-", token.lower()).strip("-")


def _deny_patterns(package: str) -> dict:
    """A semgrep ``pattern-either`` block matching any *absolute* import of
    ``package`` (or a submodule of it): plain / nested / aliased /
    ``from … import``, plus a dotted package's parent-import form."""
    regex = rf"^{re.escape(package)}(\..*)?$"
    alternatives: list[dict] = [
        {
            "patterns": [
                {
                    "pattern-either": [
                        {"pattern": "import $X"},
                        {"pattern": "import $X as $Z"},
                        {"pattern": "from $X import $Y"},
                    ]
                },
                {"metavariable-regex": {"metavariable": "$X", "regex": regex}},
            ]
        }
    ]
    if "." in package:
        parent, leaf = package.rsplit(".", 1)
        alternatives.append({"pattern": f"from {parent} import {leaf}"})
        alternatives.append({"pattern": f"from {parent} import {leaf} as $Z"})
    return {"pattern-either": alternatives}


def build_deny_rule(
    *, rule_id: str, message: str, package: str, package_dir: str
) -> dict:
    """A complete semgrep rule banning *absolute* imports of ``package`` within
    the module package at ``package_dir`` (e.g. ``src/ats/job_posting/``).

    The ``paths.include`` glob is ``<package_dir>/**`` — verified against semgrep
    1.164.0 to scope to exactly that package subtree (a bare prefix or a
    leaf-only glob either matches nothing or over-matches a same-named dir under
    a different package; see ``tests/test_boundary_rules.py``)."""
    include = package_dir.rstrip("/") + "/**"
    return {
        "id": rule_id,
        "languages": ["python"],
        "severity": "ERROR",
        "message": message,
        "paths": {"include": [include]},
        "patterns": [_deny_patterns(package)],
    }


def build_relative_deny_rule(
    *, rule_id: str, message: str, leaf: str, package_dir: str
) -> dict:
    """A semgrep rule banning the *relative* import of sibling module ``leaf``
    (``from ..billing import x`` / ``from .. import billing``) from a module's
    **root** files only.

    Scoped to ``<package_dir>*.py`` (depth 0), NOT the whole subtree: a ``..``
    from a nested file resolves *inside* the module (e.g.
    ``ats.job_posting.billing``), not to the sibling, so a subtree-wide relative
    rule would false-positive on intra-module subpackages. Deeper-than-root
    relative sibling imports are a residual gap (design Risks)."""
    include = package_dir.rstrip("/") + "/*.py"
    regex = rf"^{re.escape(leaf)}(\..*)?$"
    return {
        "id": rule_id,
        "languages": ["python"],
        "severity": "ERROR",
        "message": message,
        "paths": {"include": [include]},
        "patterns": [
            {
                "pattern-either": [
                    # ``from ..billing import x`` and ``from ..billing.invoices
                    # import y`` — metavariable-regex covers the sibling and its
                    # submodules (the relative analogue of the absolute nested form).
                    {
                        "patterns": [
                            {"pattern": "from ..$X import $Y"},
                            {
                                "metavariable-regex": {
                                    "metavariable": "$X",
                                    "regex": regex,
                                }
                            },
                        ]
                    },
                    {"pattern": f"from .. import {leaf}"},
                    {"pattern": f"from .. import {leaf} as $Z"},
                ]
            }
        ],
    }


def _compile_module_rules(
    bf: BoundariesFile, top_pkg: str, all_module_ids: set[str], package_dir: str
) -> list[dict]:
    """Compile one module's boundaries into semgrep deny rules.

    Internal: every ``forbidden_modules`` id, plus (when ``allowed_modules`` is
    set) every other module not allowed — allow-list compiled to concrete deny
    targets using the full module set. External: ``forbidden`` packages only
    (``allowed`` is advisory). Raises on any referenced module id not in the
    authored set.
    """
    referenced = set(bf.internal.allowed_modules) | set(bf.internal.forbidden_modules)
    unknown = referenced - all_module_ids
    if unknown:
        raise ValueError(
            f"boundaries for module {bf.module!r}: references unknown module "
            f"id(s) {sorted(unknown)} (authored modules: {sorted(all_module_ids)})"
        )

    forbidden_modules = set(bf.internal.forbidden_modules)
    if bf.internal.allowed_modules:
        forbidden_modules |= (
            all_module_ids - set(bf.internal.allowed_modules) - {bf.module}
        )

    rules: list[dict] = []
    for target in sorted(forbidden_modules):
        msg = (
            f"module '{bf.module}' may not import '{target}' "
            "(forbidden cross-module dependency)"
        )
        rules.append(
            build_deny_rule(
                rule_id=f"boundary-{bf.module}-no-internal-{_sanitize(target)}",
                message=msg,
                package=f"{top_pkg}.{_to_snake(target)}",
                package_dir=package_dir,
            )
        )
        # Separate relative-import rule, scoped to the module root only.
        rules.append(
            build_relative_deny_rule(
                rule_id=f"boundary-{bf.module}-no-internal-{_sanitize(target)}-rel",
                message=msg,
                leaf=_to_snake(target),
                package_dir=package_dir,
            )
        )
    for pkg in sorted(set(bf.external.forbidden)):
        rules.append(
            build_deny_rule(
                rule_id=f"boundary-{bf.module}-no-external-{_sanitize(pkg)}",
                message=(
                    f"module '{bf.module}' may not import '{pkg}' "
                    "(forbidden external dependency)"
                ),
                package=pkg,
                package_dir=package_dir,
            )
        )
    return rules


def generate_boundary_rules(project_path: Path) -> list[Path]:
    """Compile every module's ``boundaries.yaml`` into per-module semgrep rule
    files under ``.jig/rules/semgrep/boundaries/``. Idempotent: the directory is
    cleared and fully regenerated, so identical inputs yield identical files.

    Validates and compiles every module before mutating the output dir, so a
    bad input never leaves the project unenforced. Hard-errors on an unknown
    referenced module id or a boundaries file whose ``module`` field doesn't
    match its directory. Returns the rule files written (sorted).
    """
    modules_dir = project_path / ".jig" / "spec" / "modules"
    out_dir = project_path / ".jig" / "rules" / "semgrep" / "boundaries"

    # The authoritative module set for allow-list compilation + reference
    # validation is architecture.yaml's declared modules (set via
    # arch_set_module, which does NOT create a per-module dir), unioned with any
    # module dirs (to catch a boundaries-only module not yet in architecture.yaml).
    # NOT _collect_authored_module_ids — it keys on contracts.yaml, so a
    # boundaries-only or dir-less module would be invisible.
    all_module_ids: set[str] = set()
    try:
        all_module_ids |= {m.id for m in load_architecture(project_path).modules}
    except FileNotFoundError:
        pass  # no architecture.yaml yet (e.g. boundaries authored standalone)
    boundaries_files: list[Path] = []
    if modules_dir.is_dir():
        for child in sorted(modules_dir.iterdir()):
            if not child.is_dir():
                continue
            all_module_ids.add(child.name)
            if (child / "boundaries.yaml").is_file():
                boundaries_files.append(child / "boundaries.yaml")

    # Validate + compile EVERYTHING before mutating the output dir, so a bad
    # input never leaves the project with deleted/partial enforcement rules.
    top_pkg = (
        _to_snake(load_config(project_path).project.name) if boundaries_files else ""
    )
    compiled: list[tuple[str, list[dict]]] = []  # (filename, rules)
    for bf_path in boundaries_files:
        bf = BoundariesFile.model_validate(yaml.safe_load(bf_path.read_text()) or {})
        # The declared module must match its containing dir — otherwise a
        # mis-filed boundaries.yaml would generate rules for the wrong module,
        # scope them to the wrong package, and collide on the output filename.
        if bf.module != bf_path.parent.name:
            raise ValueError(
                f"boundaries file at modules/{bf_path.parent.name}/ declares "
                f"module {bf.module!r}; the module field must match its directory"
            )
        # NB: the module's package dir (src/<top_pkg>/<m_snake>/) need NOT
        # exist yet. Generation runs at arch_finalize (init), before dev agents
        # scaffold per-module code. A rule scoped to a not-yet-existing dir
        # simply matches nothing until the code lands — that's correct (no code,
        # nothing to enforce), not silent-green. The only true silent-green is
        # code authored at a path that doesn't match the convention, which is a
        # documented layout limitation (design Risks).
        m_snake = _to_snake(bf.module)
        rules = _compile_module_rules(
            bf, top_pkg, all_module_ids, f"src/{top_pkg}/{m_snake}/"
        )
        compiled.append((f"{bf.module}.yml", rules))

    # Mutation phase: clear prior output, then write the compiled set. Clearing
    # also handles the "boundaries removed" case (compiled empty → dir emptied).
    if out_dir.is_dir():
        for stale in out_dir.glob("*.yml"):
            stale.unlink()
    written: list[Path] = []
    if compiled:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, rules in compiled:
            out_file = out_dir / name
            atomic_write_text(
                out_file, yaml.safe_dump({"rules": rules}, sort_keys=False)
            )
            written.append(out_file)
    return sorted(written)
