"""Tests for the prompt-style-eval CLI scaffold (plan step 1).

The scaffold only needs to: (a) expose three subcommands so ``--help`` is
discoverable, and (b) fail loudly when those subcommands are invoked, since
none of them are implemented yet.
"""

from click.testing import CliRunner

from evals.prompt_style_eval.cli import cli


def test_help_lists_all_three_subcommands() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for subcommand in ("run", "report", "rescore"):
        assert subcommand in result.output, f"{subcommand} missing from --help"


def test_run_fails_with_not_yet_implemented() -> None:
    result = CliRunner().invoke(cli, ["run"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output


def test_report_fails_with_not_yet_implemented() -> None:
    result = CliRunner().invoke(cli, ["report"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output


def test_rescore_fails_with_not_yet_implemented() -> None:
    result = CliRunner().invoke(cli, ["rescore"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output
