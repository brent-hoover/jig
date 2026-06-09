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


def _deny_patterns(package: str) -> dict:
    """A semgrep ``pattern-either`` block matching any import of ``package``
    (or a submodule of it). Handles dotted packages' parent-import form."""
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
    """A complete semgrep rule banning imports of ``package`` within the module
    package at ``package_dir`` (e.g. ``src/ats/job_posting/``).

    The ``paths.include`` glob is ``<package_dir>/**`` — verified against
    semgrep 1.164.0 to scope to exactly that package subtree (a bare prefix or
    a leaf-only glob either matches nothing or over-matches a same-named dir
    under a different package; see ``tests/test_boundary_rules.py``)."""
    include = package_dir.rstrip("/") + "/**"
    return {
        "id": rule_id,
        "languages": ["python"],
        "severity": "ERROR",
        "message": message,
        "paths": {"include": [include]},
        "patterns": [_deny_patterns(package)],
    }
