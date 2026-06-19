"""Boundary-rule generation + the semgrep pattern spike (step 2a).

The spike test runs the *real* semgrep against a fixture covering every Python
import form, pinning the pattern set the generator depends on. Skipped (not
failed) when semgrep is absent — the dev gate degrades loudly in that case, so
a semgrep-less env is a known, non-fatal state.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from jig.boundary_rules import build_deny_rule, build_relative_deny_rule

semgrep_required = pytest.mark.skipif(
    shutil.which("semgrep") is None, reason="semgrep not installed"
)

# Every import form, with the line each appears on, split by what a rule
# banning ``ats.billing`` (dotted/internal) vs ``requests`` (top-level/external)
# must catch.
_FIXTURE = """\
import ats.billing
import ats.billing.invoices
from ats.billing import charge
from ats.billing.invoices import Invoice
import ats.billing as b
from ats import billing
import requests
import requests.sessions
from requests import get
from requests.sessions import Session
import ats.candidate
from ats.shared_types import Money
import httpx
"""
_BILLING_LINES = {1, 2, 3, 4, 5, 6}
_REQUESTS_LINES = {7, 8, 9, 10}
_ALLOWED_LINES = {11, 12, 13}


def _write_fixture(tmp_path: Path) -> Path:
    code_dir = tmp_path / "src" / "ats" / "job_posting"
    code_dir.mkdir(parents=True)
    (code_dir / "code.py").write_text(_FIXTURE)
    return tmp_path


def _run_semgrep(tmp_path: Path, rules: list[dict]) -> set[int]:
    rule_file = tmp_path / "rules.yml"
    rule_file.write_text(yaml.safe_dump({"rules": rules}))
    proc = subprocess.run(
        [
            "semgrep",
            "--metrics",
            "off",
            "--quiet",
            "--json",
            "--config",
            str(rule_file),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    # 0 = no findings, 1 = findings; anything else is a tool error.
    assert proc.returncode in (0, 1), f"semgrep errored: {proc.stderr}"
    data = json.loads(proc.stdout)
    return {r["start"]["line"] for r in data["results"]}


@semgrep_required
def test_deny_rule_matches_every_internal_import_form(tmp_path):
    _write_fixture(tmp_path)
    rule = build_deny_rule(
        rule_id="boundary-job-posting-no-internal-billing",
        message="module 'job-posting' may not import 'billing'",
        package="ats.billing",
        package_dir="src/ats/job_posting/",
    )
    matched = _run_semgrep(tmp_path, [rule])
    assert matched == _BILLING_LINES  # all six forms, nothing else


@semgrep_required
def test_deny_rule_matches_every_external_import_form(tmp_path):
    _write_fixture(tmp_path)
    rule = build_deny_rule(
        rule_id="boundary-job-posting-no-external-requests",
        message="module 'job-posting' may not import 'requests'",
        package="requests",
        package_dir="src/ats/job_posting/",
    )
    matched = _run_semgrep(tmp_path, [rule])
    assert matched == _REQUESTS_LINES


@semgrep_required
def test_deny_rules_do_not_flag_allowed_imports(tmp_path):
    _write_fixture(tmp_path)
    rules = [
        build_deny_rule(
            rule_id="boundary-job-posting-no-internal-billing",
            message="x",
            package="ats.billing",
            package_dir="src/ats/job_posting/",
        ),
        build_deny_rule(
            rule_id="boundary-job-posting-no-external-requests",
            message="x",
            package="requests",
            package_dir="src/ats/job_posting/",
        ),
    ]
    matched = _run_semgrep(tmp_path, rules)
    assert matched.isdisjoint(_ALLOWED_LINES)


@semgrep_required
def test_relative_deny_rule_catches_root_sibling_import(tmp_path):
    """A relative import of a forbidden sibling from a module-root file
    (`from ..billing import x`, `from .. import billing`) is flagged."""
    jp = tmp_path / "src" / "ats" / "job_posting"
    jp.mkdir(parents=True)
    (jp / "code.py").write_text(
        "from ..billing import charge\nfrom .. import billing\nfrom .helpers import h\n"
    )
    rule = build_relative_deny_rule(
        rule_id="boundary-job-posting-no-internal-billing-rel",
        message="x",
        leaf="billing",
        package_dir="src/ats/job_posting/",
    )
    assert any("code.py" in f for f in _semgrep_files(tmp_path, [rule]))


@semgrep_required
def test_relative_deny_rule_catches_submodule_import(tmp_path):
    """`from ..billing.invoices import Invoice` (relative submodule of the
    forbidden sibling) must be flagged; `from ..billingX import y` (a different
    module) must not."""
    jp = tmp_path / "src" / "ats" / "job_posting"
    jp.mkdir(parents=True)
    (jp / "code.py").write_text(
        "from ..billing.invoices import Invoice\nfrom ..billingx import y\n"
    )
    rule = build_relative_deny_rule(
        rule_id="boundary-job-posting-no-internal-billing-rel",
        message="x",
        leaf="billing",
        package_dir="src/ats/job_posting/",
    )
    # only the billing.invoices line; billingx is a different module
    assert _run_semgrep(tmp_path, [rule]) == {1}


@semgrep_required
def test_relative_deny_rule_does_not_flag_intra_module_subpackage(tmp_path):
    """A `from ..billing import x` in a NESTED file resolves to an
    intra-module subpackage (ats.job_posting.billing), not the sibling — the
    depth-0 scoping must not flag it (no false positive)."""
    jp = tmp_path / "src" / "ats" / "job_posting"
    (jp / "sub").mkdir(parents=True)
    (jp / "sub" / "deep.py").write_text("from ..billing import x\n")
    rule = build_relative_deny_rule(
        rule_id="boundary-job-posting-no-internal-billing-rel",
        message="x",
        leaf="billing",
        package_dir="src/ats/job_posting/",
    )
    assert not _semgrep_files(tmp_path, [rule])  # nested file not flagged


@semgrep_required
def test_deny_rule_scopes_to_include_dir(tmp_path):
    """A rule scoped to one module's dir must ignore an identical import in a
    different module's dir."""
    _write_fixture(tmp_path)
    other = tmp_path / "src" / "ats" / "candidate"
    other.mkdir(parents=True)
    (other / "code.py").write_text("import ats.billing\n")
    rule = build_deny_rule(
        rule_id="boundary-job-posting-no-internal-billing",
        message="x",
        package="ats.billing",
        package_dir="src/ats/job_posting/",
    )
    matched_files = _semgrep_files(tmp_path, [rule])
    assert any("job_posting" in f for f in matched_files)
    assert not any("candidate" in f for f in matched_files)


def _semgrep_files(tmp_path: Path, rules: list[dict]) -> set[str]:
    rule_file = tmp_path / "rules.yml"
    rule_file.write_text(yaml.safe_dump({"rules": rules}))
    proc = subprocess.run(
        [
            "semgrep",
            "--metrics",
            "off",
            "--quiet",
            "--json",
            "--config",
            str(rule_file),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1), f"semgrep errored: {proc.stderr}"
    return {r["path"] for r in json.loads(proc.stdout)["results"]}


# ---- generate_boundary_rules (step 2b) ----------------------------------

from jig.boundary_rules import generate_boundary_rules  # noqa: E402


def _make_project(tmp_path: Path, *, name: str, modules: dict) -> Path:
    """Build a minimal jig project. `modules` maps module-id → boundaries dict
    (or None for a module with no boundaries.yaml). Creates the src package dir
    for every module so the generator's dir-existence check passes."""
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "config.yaml").write_text(
        yaml.safe_dump({"project": {"name": name, "id": name, "path": str(tmp_path)}})
    )
    top_pkg = name.replace("-", "_")
    for mod_id, boundaries in modules.items():
        mdir = tmp_path / ".jig" / "spec" / "modules" / mod_id
        mdir.mkdir(parents=True)
        if boundaries is not None:
            (mdir / "boundaries.yaml").write_text(yaml.safe_dump(boundaries))
        (tmp_path / "src" / top_pkg / mod_id.replace("-", "_")).mkdir(parents=True)
    return tmp_path


def _rules_for(out_file: Path) -> list[dict]:
    return yaml.safe_load(out_file.read_text())["rules"]


def test_generate_no_boundaries_returns_empty(tmp_path):
    _make_project(tmp_path, name="my-ats", modules={"job-posting": None})
    assert generate_boundary_rules(tmp_path) == []


def test_generate_forbidden_internal_and_external(tmp_path):
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "internal": {"forbidden_modules": ["billing"]},
                "external": {"allowed": ["httpx"], "forbidden": ["requests"]},
            },
            "billing": None,
        },
    )
    written = generate_boundary_rules(tmp_path)
    assert [p.name for p in written] == ["job-posting.yml"]
    ids = {r["id"] for r in _rules_for(written[0])}
    assert ids == {
        "boundary-job-posting-no-internal-billing",
        "boundary-job-posting-no-internal-billing-rel",  # relative-import rule
        "boundary-job-posting-no-external-requests",
    }
    # external allowed (httpx) is advisory — no rule
    assert not any("httpx" in i for i in ids)
    # internal target package is top_pkg.billing
    internal = next(r for r in _rules_for(written[0]) if "internal" in r["id"])
    assert internal["paths"]["include"] == ["src/my_ats/job_posting/**"]


def test_generate_allow_list_compiles_to_deny_targets(tmp_path):
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "internal": {"allowed_modules": ["candidate"]},
            },
            "candidate": None,
            "billing": None,
            "auth": None,
        },
    )
    written = generate_boundary_rules(tmp_path)
    ids = {r["id"] for r in _rules_for(written[0])}
    # allowed=candidate → deny every other module (billing, auth), not self/candidate
    # each internal target gets an absolute + a relative rule
    assert ids == {
        "boundary-job-posting-no-internal-billing",
        "boundary-job-posting-no-internal-billing-rel",
        "boundary-job-posting-no-internal-auth",
        "boundary-job-posting-no-internal-auth-rel",
    }


def test_generate_picks_up_boundaries_only_module(tmp_path):
    """A module with boundaries.yaml but no contracts.yaml must be generated —
    _collect_authored_module_ids (contracts-keyed) would miss it."""
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "external": {"forbidden": ["requests"]},
            }
        },
    )
    written = generate_boundary_rules(tmp_path)
    assert [p.name for p in written] == ["job-posting.yml"]


def test_generate_module_dir_mismatch_raises(tmp_path):
    """A boundaries.yaml whose `module` field doesn't match its directory must
    hard-error (a mis-filed file would scope rules to the wrong module)."""
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "billing",  # wrong — dir is job-posting
                "external": {"forbidden": ["requests"]},
            }
        },
    )
    with pytest.raises(ValueError, match="must match its directory"):
        generate_boundary_rules(tmp_path)


def test_generate_recognizes_architecture_only_module(tmp_path):
    """A module declared in architecture.yaml but with no per-module dir (the
    normal state — arch_set_module doesn't create dirs) must count as a known
    module: forbidding it must NOT raise 'unknown module'."""
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "internal": {"forbidden_modules": ["billing"]},
            }
        },
    )
    # 'billing' has NO modules/ dir — only an architecture.yaml entry
    (tmp_path / ".jig" / "spec" / "architecture.yaml").write_text(
        yaml.safe_dump(
            {
                "modules": [
                    {
                        "id": "billing",
                        "title": "Billing",
                        "summary": "x",
                        "intent": {"problem": "p", "simplest_solution": "s"},
                    }
                ]
            }
        )
    )
    written = generate_boundary_rules(tmp_path)  # must not raise
    ids = {r["id"] for r in _rules_for(written[0])}
    assert "boundary-job-posting-no-internal-billing" in ids


def test_generate_unknown_module_id_raises(tmp_path):
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "internal": {"forbidden_modules": ["does-not-exist"]},
            }
        },
    )
    with pytest.raises(ValueError, match="unknown module"):
        generate_boundary_rules(tmp_path)


def test_generate_succeeds_without_package_dir_yet(tmp_path):
    """Generation runs at arch_finalize, before dev agents scaffold per-module
    code — a not-yet-existing package dir must NOT block generation. The rule
    scopes to where the code will be; it matches nothing until then."""
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "external": {"forbidden": ["requests"]},
            }
        },
    )
    shutil.rmtree(tmp_path / "src" / "my_ats" / "job_posting")  # no code yet
    written = generate_boundary_rules(tmp_path)
    assert [p.name for p in written] == ["job-posting.yml"]
    rule = _rules_for(written[0])[0]
    assert rule["paths"]["include"] == ["src/my_ats/job_posting/**"]


def test_generate_failure_preserves_existing_rules(tmp_path):
    """A validation failure must not delete the previously-generated rules —
    the project must never be left silently unenforced."""
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "external": {"forbidden": ["requests"]},
            }
        },
    )
    generate_boundary_rules(tmp_path)  # good rules exist
    rule_file = (
        tmp_path / ".jig" / "rules" / "semgrep" / "boundaries" / "job-posting.yml"
    )
    before = rule_file.read_bytes()
    # now introduce a bad boundary (unknown module id)
    (
        tmp_path / ".jig" / "spec" / "modules" / "job-posting" / "boundaries.yaml"
    ).write_text(
        yaml.safe_dump(
            {
                "module": "job-posting",
                "internal": {"forbidden_modules": ["ghost"]},
            }
        )
    )
    with pytest.raises(ValueError, match="unknown module"):
        generate_boundary_rules(tmp_path)
    assert rule_file.read_bytes() == before  # untouched on failure


def test_generate_is_idempotent(tmp_path):
    _make_project(
        tmp_path,
        name="my-ats",
        modules={
            "job-posting": {
                "module": "job-posting",
                "internal": {"forbidden_modules": ["billing"]},
            },
            "billing": None,
        },
    )
    first = generate_boundary_rules(tmp_path)
    bytes1 = first[0].read_bytes()
    second = generate_boundary_rules(tmp_path)
    assert [p.name for p in first] == [p.name for p in second]
    assert second[0].read_bytes() == bytes1  # byte-identical regeneration
