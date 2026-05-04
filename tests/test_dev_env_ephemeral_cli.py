"""Tests for ``jig dev ephemeral list/inspect/drop`` CLI (Track E Final)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from jig.dev_env.ephemeral import (
    SqliteEphemeralProvisioner,
    ephemeral_root,
)
from jig.schemas.dev_env import ManifestService


def _seed_sqlite_ephemeral(
    tmp_path: Path,
    *,
    service_id: str = "ephem",
    ticket_id: str = "t-001",
    rows: int = 0,
) -> Path:
    """Create one ephemeral SQLite file under the project's tree.

    Returns the file path so tests can assert against it directly.
    Optional ``rows`` populates a tiny ``items`` table so the inspect
    command has something to count.
    """
    import asyncio

    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id=service_id,
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="agent_{ticket_id}",
    )
    asyncio.run(p.provision(svc, agent_id="dev", ticket_id=ticket_id))
    db_path = (
        ephemeral_root(tmp_path) / service_id / f"agent_{ticket_id.replace('-', '_')}.db"
    )
    if rows:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
            for i in range(rows):
                conn.execute("INSERT INTO items (id) VALUES (?)", (i + 1,))
            conn.commit()
        finally:
            conn.close()
    return db_path


# ---------------------------------------------------------------------------
# jig dev ephemeral list
# ---------------------------------------------------------------------------


def test_cli_dev_ephemeral_list_empty(tmp_path: Path) -> None:
    """Empty ephemeral root → friendly message."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "ephemeral", "list", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "(no ephemeral instances)" in result.output


def test_cli_dev_ephemeral_list_shows_files(tmp_path: Path) -> None:
    _seed_sqlite_ephemeral(tmp_path, service_id="ephem", ticket_id="t-001")
    _seed_sqlite_ephemeral(tmp_path, service_id="ephem", ticket_id="t-002")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "ephemeral", "list", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "ephem" in result.output
    assert "agent_t_001.db" in result.output
    assert "agent_t_002.db" in result.output
    # File sizes appear (bytes) — at least one digit.
    assert "bytes" in result.output


# ---------------------------------------------------------------------------
# jig dev ephemeral inspect
# ---------------------------------------------------------------------------


def test_cli_dev_ephemeral_inspect_unknown_id_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "ephemeral", "inspect", "ephem:nope", "--path", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_dev_ephemeral_inspect_sqlite_shows_table_counts(
    tmp_path: Path,
) -> None:
    _seed_sqlite_ephemeral(
        tmp_path, service_id="ephem", ticket_id="t-001", rows=3
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "ephemeral",
            "inspect",
            "ephem:agent_t_001",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Table name + row count appear in the output.
    assert "items" in result.output
    assert "3" in result.output


def test_cli_dev_ephemeral_inspect_empty_sqlite(tmp_path: Path) -> None:
    """A freshly-touched but empty SQLite file inspects without error."""
    _seed_sqlite_ephemeral(tmp_path, service_id="ephem", ticket_id="t-001")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "ephemeral",
            "inspect",
            "ephem:agent_t_001",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Empty DB has no tables — output should still be non-empty
    # (e.g. a header line + "(no tables)").
    assert "no tables" in result.output.lower() or result.output.strip()


# ---------------------------------------------------------------------------
# jig dev ephemeral drop
# ---------------------------------------------------------------------------


def test_cli_dev_ephemeral_drop_removes_file(tmp_path: Path) -> None:
    db_path = _seed_sqlite_ephemeral(
        tmp_path, service_id="ephem", ticket_id="t-001"
    )
    assert db_path.is_file()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "ephemeral",
            "drop",
            "ephem:agent_t_001",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dropped" in result.output
    assert not db_path.is_file()


def test_cli_dev_ephemeral_drop_unknown_id_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "ephemeral", "drop", "ephem:nope", "--path", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "not found" in result.output
