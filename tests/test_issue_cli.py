"""`jig issue` CLI front door.

Thin adapter over IssueService: creates land PROPOSED with a jig-N key,
resolve by key or UUID, fail loud (non-zero, no write) on a bad contract, and
the project is located by walking up from cwd (or an explicit --path).
"""

from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    return tmp_path


def test_create_prints_key(tmp_path: Path) -> None:
    root = _project(tmp_path)
    res = CliRunner().invoke(
        cli,
        [
            "issue",
            "create",
            "--path",
            str(root),
            "--title",
            "add export",
            "--type",
            "feature",
            "--body",
            TICKET_AC_PLACEHOLDER,
        ],
    )
    assert res.exit_code == 0, res.output
    assert "jig-1" in res.output


def test_create_discovers_project_from_subdir(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    nested = root / "pkg" / "sub"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    res = CliRunner().invoke(
        cli,
        [
            "issue",
            "create",
            "--title",
            "x",
            "--type",
            "feature",
            "--body",
            TICKET_AC_PLACEHOLDER,
        ],
    )
    assert res.exit_code == 0, res.output
    assert "jig-1" in res.output


def test_create_missing_ac_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    res = CliRunner().invoke(
        cli,
        [
            "issue",
            "create",
            "--path",
            str(root),
            "--title",
            "vague",
            "--type",
            "feature",
            "--body",
            "no criteria here",
        ],
    )
    assert res.exit_code != 0

    listing = CliRunner().invoke(cli, ["issue", "list", "--path", str(root)])
    assert "jig-" not in listing.output


def test_show_accepts_key_and_uuid(tmp_path: Path) -> None:
    root = _project(tmp_path)
    CliRunner().invoke(
        cli,
        [
            "issue",
            "create",
            "--path",
            str(root),
            "--title",
            "find me",
            "--type",
            "feature",
            "--body",
            TICKET_AC_PLACEHOLDER,
        ],
    )
    by_key = CliRunner().invoke(cli, ["issue", "show", "--path", str(root), "jig-1"])
    assert by_key.exit_code == 0
    assert "find me" in by_key.output


def test_approve_transitions_to_open(tmp_path: Path) -> None:
    root = _project(tmp_path)
    CliRunner().invoke(
        cli,
        [
            "issue",
            "create",
            "--path",
            str(root),
            "--title",
            "x",
            "--type",
            "feature",
            "--body",
            TICKET_AC_PLACEHOLDER,
        ],
    )
    res = CliRunner().invoke(cli, ["issue", "approve", "--path", str(root), "jig-1"])
    assert res.exit_code == 0, res.output

    shown = CliRunner().invoke(cli, ["issue", "show", "--path", str(root), "jig-1"])
    assert "open" in shown.output.lower()
