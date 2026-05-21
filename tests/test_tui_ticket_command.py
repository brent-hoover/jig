import pytest

from jig.tui.commands import get_handler, known_commands


def test_ticket_command_is_registered():
    assert "ticket" in known_commands()


@pytest.mark.asyncio
async def test_ticket_rejects_no_subcommand():
    handler = get_handler("ticket")

    class FakeOrch:
        pass

    result = await handler(args=[], orch=FakeOrch(), project_path=None)
    assert result["ok"] is False
    assert "subcommand" in result["error"]


@pytest.mark.asyncio
async def test_ticket_new_requires_title():
    handler = get_handler("ticket")

    class FakeOrch:
        pass

    result = await handler(
        args=["new", "--size", "m"], orch=FakeOrch(), project_path=None
    )
    assert result["ok"] is False
    assert "title" in result["error"].lower()


@pytest.mark.asyncio
async def test_ticket_unknown_subcommand():
    handler = get_handler("ticket")

    class FakeOrch:
        pass

    result = await handler(args=["nuke"], orch=FakeOrch(), project_path=None)
    assert result["ok"] is False
    assert "unknown" in result["error"].lower()


@pytest.mark.asyncio
async def test_ticket_no_orch():
    handler = get_handler("ticket")
    result = await handler(args=["new", "--title", "x"], orch=None, project_path=None)
    assert result["ok"] is False
    assert "orchestrator" in result["error"].lower()


@pytest.mark.asyncio
async def test_ticket_update_requires_kvs():
    handler = get_handler("ticket")

    class FakeOrch:
        pass

    result = await handler(
        args=["update", "abc-123"], orch=FakeOrch(), project_path=None
    )
    assert result["ok"] is False
    assert "field=value" in result["error"]


@pytest.mark.asyncio
async def test_ticket_new_creates_via_orch(tmp_path):
    """End-to-end through a real (lightweight) orchestrator-shaped object."""
    handler = get_handler("ticket")

    from jig.store import MessageBus
    from jig.store.tickets import TicketStore

    class FakeOrch:
        def __init__(self, path):
            store_dir = path / ".jig" / "store"
            store_dir.mkdir(parents=True, exist_ok=True)
            self.tickets = TicketStore(store_dir / "tickets.jsonl")
            self.bus = MessageBus(store_dir / "messages.jsonl")

        async def load(self):
            await self.tickets.load()
            await self.bus.load()

    orch = FakeOrch(tmp_path)
    await orch.load()

    result = await handler(
        args=[
            "new",
            "--title",
            "test ticket",
            "--size",
            "s",
            "--description",
            "## Acceptance criteria\n- Ticket lands in the store.",
        ],
        orch=orch,
        project_path=tmp_path,
    )
    assert result["ok"] is True, result
    assert result["data"]["ticket_id"]
    # Verify ticket actually landed in the store
    listed = await orch.tickets.list_all()
    titles = [t.title for t in listed]
    assert "test ticket" in titles
