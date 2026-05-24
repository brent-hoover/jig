"""Template smoke test: the typer app's --help renders and the
placeholder ``hello`` command executes. Catches accidental removal of
either piece during agent edits to the scaffolded project.

Uses ``typer.testing.CliRunner`` so the test passes without an install
step (no ``uv sync`` needed for pytest itself to run — the template-
smoke runner in jig does sync before pytest invocation, but this test
deliberately avoids depending on the script wrapper existing yet).
"""

from typer.testing import CliRunner

from myproject.cli import app


def test_help_runs_cleanly() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_hello_command_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--name", "scaffold-smoke"])
    assert result.exit_code == 0, result.output
    assert "Hello, scaffold-smoke!" in result.output
