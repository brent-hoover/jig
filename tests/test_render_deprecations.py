"""`jig render deprecations` converts a deprecations manifest into a
semgrep-consumable rule set.

Closes the pre-existing gap (roborev #220) where the canonicalizer ran
`semgrep --config .jig/rules/deprecations.yml` directly — but that file is a
`deprecations:` manifest, not native semgrep config, so semgrep can't parse it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from jig.cli import cli


def _write_deprecations(project: Path) -> None:
    rules = project / ".jig" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "deprecations.yml").write_text(
        "deprecations:\n"
        "  - id: no-eq-none\n"
        "    pattern: $X == None\n"
        "    fix: $X is None\n"
        "    languages: [python]\n"
        "    rationale: 'use identity comparison'\n"
    )


def test_render_deprecations_emits_semgrep_rules(tmp_path: Path) -> None:
    _write_deprecations(tmp_path)
    res = CliRunner().invoke(cli, ["render", "deprecations", "--path", str(tmp_path)])
    assert res.exit_code == 0, res.output
    doc = yaml.safe_load(res.output)
    assert "rules" in doc
    assert any(r["id"] == "no-eq-none" for r in doc["rules"])


def test_rendered_deprecations_validate_with_real_semgrep(tmp_path: Path) -> None:
    _write_deprecations(tmp_path)
    res = CliRunner().invoke(cli, ["render", "deprecations", "--path", str(tmp_path)])
    assert res.exit_code == 0, res.output
    out = tmp_path / "dep-rules.yml"
    out.write_text(res.output)
    proc = subprocess.run(
        ["semgrep", "--metrics", "off", "--validate", "--config", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
