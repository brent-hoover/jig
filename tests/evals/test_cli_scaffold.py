"""Tests for the CLI surface.

Originally a step-1 scaffold; now the scaffold has filled in. Still verifies
the three subcommands are discoverable; ``rescore`` is still a stub at this
point (plan step 13).
"""

from click.testing import CliRunner

from evals.prompt_style_eval.cli import cli


def test_help_lists_all_three_subcommands() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for subcommand in ("run", "report", "rescore"):
        assert subcommand in result.output, f"{subcommand} missing from --help"


def test_run_help_lists_required_task_option() -> None:
    result = CliRunner().invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--task" in result.output
    assert "--prompt" in result.output
    assert "--seeds" in result.output


def test_report_help_lists_format_option() -> None:
    result = CliRunner().invoke(cli, ["report", "--help"])
    assert result.exit_code == 0
    assert "--format" in result.output


def test_rescore_still_stubbed() -> None:
    result = CliRunner().invoke(cli, ["rescore"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output
