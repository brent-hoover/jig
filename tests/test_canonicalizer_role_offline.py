"""Guard: the canonicalizer runbook must invoke semgrep with metrics off so it
never attempts a network call inside the sandbox."""

from pathlib import Path

import yaml

ROLE = (
    Path(__file__).resolve().parent.parent
    / "jig"
    / "defaults"
    / "roles"
    / "canonicalizer.yaml"
)


def test_runbook_runs_semgrep_offline() -> None:
    prompt = yaml.safe_load(ROLE.read_text())["phase_prompt"]
    # Every semgrep invocation in the runbook carries --metrics off. Match
    # "semgrep" and "--config" independently — after the fix the line reads
    # `semgrep --metrics off --config ...`, so "semgrep --config" is no longer
    # a substring and a combined check would go vacuous.
    semgrep_lines = [
        line for line in prompt.splitlines() if "semgrep" in line and "--config" in line
    ]
    assert semgrep_lines, "expected at least one semgrep --config invocation in the runbook"
    for line in semgrep_lines:
        assert "--metrics off" in line, f"semgrep call missing --metrics off: {line}"
