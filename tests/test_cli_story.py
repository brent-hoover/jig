"""CLI tests for `jig story`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.project import Project, save_project
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import Ticket, WorkType


def _store_paths(project_path: Path) -> tuple[Path, Path]:
    store_dir = project_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "tickets.jsonl", store_dir / "comments.jsonl"


@pytest.mark.asyncio
async def test_story_command_prints_thread_entries(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    tickets_path, threads_path = _store_paths(tmp_path)

    tickets = TicketStore(tickets_path)
    await tickets.load()
    threads = ThreadStore(threads_path)
    await threads.load()

    tid = await tickets.create(
        Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
    )
    await threads.post(Note(ticket_id=tid, author="dev", text="hello story"))

    runner = CliRunner()
    result = await asyncio.to_thread(
        runner.invoke, cli, ["story", tid, "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "hello story" in result.output


@pytest.mark.asyncio
async def test_story_command_json_output(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    tickets_path, threads_path = _store_paths(tmp_path)

    tickets = TicketStore(tickets_path)
    await tickets.load()
    threads = ThreadStore(threads_path)
    await threads.load()

    tid = await tickets.create(
        Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
    )
    await threads.post(Note(ticket_id=tid, author="dev", text="hi"))

    runner = CliRunner()
    result = await asyncio.to_thread(
        runner.invoke, cli, ["story", tid, "--path", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.strip().splitlines() if line]
    assert lines
    parsed = json.loads(lines[0])
    assert parsed["source"] == "thread"
    assert parsed["kind"] == "note"


def test_story_command_unknown_ticket_exits_nonzero(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["story", "no-such-ticket", "--path", str(tmp_path)]
    )
    assert result.exit_code != 0
