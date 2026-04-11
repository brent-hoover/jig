import asyncio
from pathlib import Path

import pytest

from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, TicketStatus, TicketType
from jig.store.tickets import TicketStore
from jig.store import Message, MessageType


@pytest.mark.asyncio
async def test_orchestrator_startup_loads_collections(tmp_path: Path) -> None:
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
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        assert orch.tickets is not None
        assert orch.comments is not None
        assert orch.bus is not None
        assert orch._live_subscribers == {}
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_resumes_in_progress_tickets(tmp_path: Path) -> None:
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
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(
        Ticket(
            type=TicketType.FEATURE,
            title="f",
            created_by="user",
            status=TicketStatus.IN_PROGRESS,
        )
    )

    orch = Orchestrator(project_path=tmp_path)
    seen: list[str] = []

    async def fake_run_ticket(ticket_id: str) -> None:
        seen.append(ticket_id)

    orch._run_ticket = fake_run_ticket  # type: ignore[method-assign]

    await orch.startup()
    try:
        await asyncio.sleep(0.05)
        assert seen == [tid]
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_dispatch_loop_spawns_for_unaddressed_message(tmp_path: Path) -> None:
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
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(
        Ticket(
            type=TicketType.QUESTION,
            title="q",
            created_by="dev",
            assignee="spec-writer",
            parent_id="parent-1",
        )
    )

    orch = Orchestrator(project_path=tmp_path)
    spawn_calls: list[tuple[str, str]] = []

    async def fake_spawn(ticket_id: str, role: str, initial_event) -> None:
        spawn_calls.append((ticket_id, role))

    orch._spawn_qa_responder = fake_spawn  # type: ignore[method-assign]

    await orch.startup()
    try:
        from jig.store import Message, MessageType
        await orch.bus.publish(
            Message(
                sender="dev",
                to="spec-writer",
                type=MessageType.CONTEXT_UPDATE,
                payload={"kind": "ticket_created", "ticket_id": tid},
                topic=f"tickets.{tid}",
            )
        )
        for _ in range(20):
            await asyncio.sleep(0.02)
            if spawn_calls:
                break
        assert (tid, "spec-writer") in spawn_calls
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_dispatch_loop_skips_user_role(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path)),
    )
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(
        Ticket(
            type=TicketType.QUESTION,
            title="q",
            created_by="dev",
            assignee="user",
        )
    )

    orch = Orchestrator(project_path=tmp_path)
    spawn_calls: list[tuple[str, str]] = []

    async def fake_spawn(ticket_id: str, role: str, initial_event) -> None:
        spawn_calls.append((ticket_id, role))

    orch._spawn_qa_responder = fake_spawn  # type: ignore[method-assign]

    await orch.startup()
    try:
        from jig.store import Message, MessageType
        await orch.bus.publish(
            Message(
                sender="dev",
                to="user",
                type=MessageType.CONTEXT_UPDATE,
                payload={"kind": "ticket_created", "ticket_id": tid},
                topic=f"tickets.{tid}",
            )
        )
        await asyncio.sleep(0.1)
        assert spawn_calls == []
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_dispatch_loop_skips_if_live_subscriber_present(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path)),
    )
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(
        Ticket(
            type=TicketType.TASK,
            title="t",
            created_by="o",
            assignee="dev",
        )
    )

    orch = Orchestrator(project_path=tmp_path)
    spawn_calls: list[tuple[str, str]] = []

    async def fake_spawn(ticket_id: str, role: str, initial_event) -> None:
        spawn_calls.append((ticket_id, role))

    orch._spawn_qa_responder = fake_spawn  # type: ignore[method-assign]

    await orch.startup()
    try:
        fake_task = asyncio.create_task(asyncio.sleep(60))
        orch._live_subscribers[(tid, "dev")] = fake_task
        from jig.store import Message, MessageType
        await orch.bus.publish(
            Message(
                sender="qa",
                to="dev",
                type=MessageType.CONTEXT_UPDATE,
                payload={"kind": "comment_posted"},
                topic=f"tickets.{tid}",
            )
        )
        await asyncio.sleep(0.1)
        fake_task.cancel()
        try:
            await fake_task
        except asyncio.CancelledError:
            pass
        assert spawn_calls == []
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_spawn_qa_responder_setup_failure_does_not_kill_dispatch(
    tmp_path: Path,
) -> None:
    """_spawn_qa_responder must swallow setup errors, not propagate them."""
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"),
    )
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(type=TicketType.TASK, title="t", created_by="o", assignee="qa")
        )
        fake_msg = Message(
            sender="o", to="qa", type=MessageType.CONTEXT_UPDATE,
            payload={}, topic=f"tickets.{tid}",
        )
        # Patch _ensure_worktree to raise — simulating any setup failure.
        async def boom(ticket):
            raise RuntimeError("boom")
        orch._ensure_worktree = boom  # type: ignore[method-assign]

        # Must not raise.
        await orch._spawn_qa_responder(tid, "qa", fake_msg)

        # Sentinel must have been cleaned up.
        assert (tid, "qa") not in orch._live_subscribers
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_spawn_qa_responder_reserves_slot_before_awaits(
    tmp_path: Path, monkeypatch
) -> None:
    """Slot must be reserved synchronously before the first await."""
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"),
    )
    (tmp_path / ".jig" / "agent_types").mkdir(parents=True)
    from jig.persistence import save_agent_type
    from jig.models import AgentTypeConfig
    save_agent_type(tmp_path, AgentTypeConfig(role="qa", phase_prompt="be qa"))

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    async def slow_run_agent(ctx):
        await asyncio.sleep(0.1)
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", slow_run_agent)

    async def fake_ensure(ticket):
        return tmp_path

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(type=TicketType.QUESTION, title="q", created_by="dev", assignee="qa")
        )
        fake_msg = Message(
            sender="dev", to="qa", type=MessageType.CONTEXT_UPDATE,
            payload={}, topic=f"tickets.{tid}",
        )
        # Start without awaiting; the sentinel should be set synchronously.
        task = asyncio.create_task(orch._spawn_qa_responder(tid, "qa", fake_msg))
        # Yield control once so the coroutine runs up to its first await.
        await asyncio.sleep(0)
        assert (tid, "qa") in orch._live_subscribers
        await task
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_emits_ticket_events_to_emitter(tmp_path: Path) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    from jig.events import EventEmitter
    emitter = EventEmitter()
    orch = Orchestrator(project_path=tmp_path, emitter=emitter)
    await orch.startup()
    try:
        queue = emitter.subscribe()
        await orch.tickets.create(Ticket(type=TicketType.FEATURE, title="f", created_by="user"))
        await orch.bus.publish(Message(
            sender="user", to="orchestrator", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_created"},
            topic="orchestrator",
        ))
        await asyncio.sleep(0.1)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        kinds = [e.type for e in events]
        assert "ticket_created" in kinds
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_spawn_qa_responder_calls_run_agent(tmp_path: Path, monkeypatch) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"))
    (tmp_path / ".jig" / "agent_types").mkdir(parents=True)
    from jig.persistence import save_agent_type
    from jig.models import AgentTypeConfig
    save_agent_type(tmp_path, AgentTypeConfig(role="qa", phase_prompt="be qa"))

    orch = Orchestrator(project_path=tmp_path)
    calls: list[str] = []

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    async def fake_run_agent(ctx):
        calls.append(ctx.role)
        return RunAgentResult(status="success", final_text="ok")
    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path
    orch._ensure_worktree = fake_ensure  # type: ignore

    await orch.startup()
    try:
        tid = await orch.tickets.create(Ticket(
            type=TicketType.QUESTION, title="q", created_by="dev", assignee="qa",
        ))
        fake_msg = Message(
            sender="dev", to="qa", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_created", "ticket_id": tid},
            topic=f"tickets.{tid}",
        )
        await orch._spawn_qa_responder(tid, "qa", fake_msg)
        # Give it a moment
        for _ in range(20):
            await asyncio.sleep(0.05)
            if calls:
                break
        assert calls == ["qa"]
    finally:
        await orch.shutdown()
