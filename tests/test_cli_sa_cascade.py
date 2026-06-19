"""``jig sa cascade`` CLI viewer (Track C Final, Deliverable 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cascade_viewer import (
    filter_audit,
    list_cascades,
    show_cascade,
)
from jig.cli import cli
from jig.sa_incremental_mcp import (
    handle_arch_complete_spike,
    handle_arch_propose_spike,
    handle_arch_reject_cascade,
    handle_arch_set_risk,
)
from jig.sa_mcp import SA_TICKET_ID
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def project_with_cascade(tmp_path: Path):
    """Set up a project + emit one confirmed_impossible cascade."""
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    await tickets.create(
        Ticket(
            id=SA_TICKET_ID,
            work_type=WorkType.BRIEF,
            title="SA",
            created_by="cli",
        )
    )
    await handle_arch_set_risk(
        project_path=tmp_path,
        risk={
            "id": "r-test",
            "text": "Test risk",
            "impact": "medium",
            "likelihood": "medium",
            "status": "open",
        },
    )
    spike_id = await handle_arch_propose_spike(
        tickets=tickets,
        threads=threads,
        bus=bus,
        project_path=tmp_path,
        risk_id="r-test",
        summary="Spike summary",
        dependent_contracts=[
            "project://arch/modules/m/contracts#a/x",
            "project://arch/modules/m/contracts#a/y",
        ],
        author="sa-mvp",
    )
    await handle_arch_complete_spike(
        tickets=tickets,
        threads=threads,
        bus=bus,
        project_path=tmp_path,
        spike_ticket_id=spike_id,
        finding="impossible.",
        status="confirmed_impossible",
        author="sa-mvp",
    )
    return tmp_path


# ---- list --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_cascades_returns_one_row(project_with_cascade):
    rows = list_cascades(project_with_cascade)
    assert len(rows) == 1
    row = rows[0]
    assert row.risk_id == "r-test"
    assert row.contract_count == 2
    assert row.state == "pending"


@pytest.mark.asyncio
async def test_list_cascades_returns_empty_when_dir_missing(tmp_path: Path):
    """No cascades dir → empty list (not an error)."""
    rows = list_cascades(tmp_path)
    assert rows == []


@pytest.mark.asyncio
async def test_cli_list_cascade_command_renders_table(project_with_cascade):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sa", "cascade", "list", "--path", str(project_with_cascade)],
    )
    assert result.exit_code == 0, result.output
    assert "CASCADE_ID" in result.output
    assert "r-test" in result.output
    assert "pending" in result.output


@pytest.mark.asyncio
async def test_cli_list_empty_cascade_dir(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["sa", "cascade", "list", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "(no cascades)" in result.output


# ---- show --------------------------------------------------------


@pytest.mark.asyncio
async def test_show_cascade_returns_proposal_and_audit(project_with_cascade):
    rows = list_cascades(project_with_cascade)
    cascade_id = rows[0].cascade_id
    detail = show_cascade(project_with_cascade, cascade_id)
    assert detail.proposal.cascade_id == cascade_id
    assert len(detail.audit_entries) >= 1
    # The seed flow always produces a "proposed" audit entry.
    assert detail.audit_entries[0].action == "proposed"


@pytest.mark.asyncio
async def test_show_cascade_unknown_id_raises_keyerror(project_with_cascade):
    with pytest.raises(KeyError):
        show_cascade(project_with_cascade, "nonexistent-cascade-id")


@pytest.mark.asyncio
async def test_cli_show_cascade_renders_finding_and_audit(project_with_cascade):
    rows = list_cascades(project_with_cascade)
    cascade_id = rows[0].cascade_id
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sa", "cascade", "show", cascade_id, "--path", str(project_with_cascade)],
    )
    assert result.exit_code == 0, result.output
    assert "# Cascade " in result.output
    assert "## Finding" in result.output
    assert "impossible" in result.output
    assert "## Audit log" in result.output
    assert "proposed" in result.output


@pytest.mark.asyncio
async def test_cli_show_unknown_cascade_exits_with_clickexception(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["sa", "cascade", "show", "missing-1", "--path", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "no cascade" in result.output.lower()


# ---- audit -------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_audit_filters_by_actor(project_with_cascade):
    """Reject the cascade as a different actor; verify --operator filters it."""
    rows = list_cascades(project_with_cascade)
    cascade_id = rows[0].cascade_id
    await handle_arch_reject_cascade(
        project_path=project_with_cascade,
        cascade_id=cascade_id,
        reason="not_relevant",
        actor="brent",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "sa",
            "cascade",
            "audit",
            "--operator",
            "brent",
            "--path",
            str(project_with_cascade),
        ],
    )
    assert result.exit_code == 0, result.output
    # Only brent's row should appear; sa-mvp's "proposed" row is filtered out.
    assert "brent" in result.output
    assert "sa-mvp" not in result.output


@pytest.mark.asyncio
async def test_cli_audit_filters_by_since(project_with_cascade):
    """--since with future date → no rows (every entry is in the past)."""
    runner = CliRunner()
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    result = runner.invoke(
        cli,
        [
            "sa",
            "cascade",
            "audit",
            "--since",
            future,
            "--path",
            str(project_with_cascade),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(no audit entries)" in result.output


@pytest.mark.asyncio
async def test_cli_audit_renders_markdown_table_when_entries(project_with_cascade):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["sa", "cascade", "audit", "--path", str(project_with_cascade)]
    )
    assert result.exit_code == 0, result.output
    assert "| timestamp |" in result.output
    assert "| proposed |" in result.output


@pytest.mark.asyncio
async def test_filter_audit_no_filters_returns_all(project_with_cascade):
    entries = filter_audit(project_with_cascade)
    assert len(entries) >= 1
