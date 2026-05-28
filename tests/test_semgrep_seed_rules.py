"""Real-semgrep tests for the shipped seed rules.

Semgrep is a project dependency (like radon/ruff); these run it for real — no
mocks. The whole point of the rules is to fire on real code, so the tests do too.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

RULES_DIR = (
    Path(__file__).resolve().parent.parent
    / "jig"
    / "defaults"
    / "project_templates"
    / "python"
    / ".jig"
    / "rules"
    / "semgrep"
)


def _semgrep(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["semgrep", "--metrics", "off", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_seed_rules_validate() -> None:
    proc = _semgrep("--validate", "--config", str(RULES_DIR), cwd=RULES_DIR)
    assert proc.returncode == 0, proc.stderr


def test_eq_none_rule_autofixes(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("def f(x):\n    return x == None\n")
    proc = _semgrep(
        "--config", str(RULES_DIR / "style.yml"), "--autofix", str(target), cwd=tmp_path
    )
    # semgrep exits 1 when findings are present, 0 when none, 2 on fatal error.
    # The meaningful assertion is that the autofix was actually applied.
    assert proc.returncode in (0, 1), proc.stderr
    assert "is None" in target.read_text()
    assert "== None" not in target.read_text()


def test_bare_except_pass_is_detected(tmp_path: Path) -> None:
    target = tmp_path / "e.py"
    target.write_text("def f():\n    try:\n        g()\n    except:\n        pass\n")
    proc = _semgrep(
        "--config",
        str(RULES_DIR / "error-handling.yml"),
        "--json",
        str(target),
        cwd=tmp_path,
    )
    assert proc.returncode in (0, 1), proc.stderr  # 2 = fatal error
    findings = json.loads(proc.stdout)["results"]
    assert any(f["check_id"].endswith("no-swallowed-exception") for f in findings)
