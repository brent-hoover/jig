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

from jig.boundary_rules import build_deny_rule

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
        ["semgrep", "--metrics", "off", "--quiet", "--json", "--config",
         str(rule_file), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1), f"semgrep errored: {proc.stderr}"
    return {r["path"] for r in json.loads(proc.stdout)["results"]}
