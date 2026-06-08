"""jig-N short keys and cross-process-safe assignment.

Every ticket gets a monotonic ``jig-N`` key at create, assigned in the shared
``TicketStore.create`` path under a cross-process ``flock`` so concurrent
processes never collide on a key. References resolve by key or by UUID.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_create_assigns_sequential_keys(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    keys = []
    for i in range(3):
        tid = await store.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title=f"t{i}",
                created_by="u",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        keys.append((await store.get(tid)).key)

    assert keys == ["jig-1", "jig-2", "jig-3"]


@pytest.mark.asyncio
async def test_resolve_ref_by_key_and_uuid(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    ticket = await store.get(tid)

    by_uuid = await store.resolve_ref(tid)
    by_key = await store.resolve_ref(ticket.key)

    assert by_uuid is not None and by_uuid.id == tid
    assert by_key is not None and by_key.id == tid
    assert await store.resolve_ref("jig-9999") is None


_WRITER = textwrap.dedent(
    """
    import asyncio, sys
    from pathlib import Path
    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket, WorkType

    AC = "## Acceptance criteria\\n- x\\n"

    async def main(path, n, label):
        store = TicketStore(Path(path))
        await store.load()
        for i in range(n):
            await store.create(Ticket(
                work_type=WorkType.FEATURE,
                title=f"{label}-{i}",
                created_by=label,
                description=AC,
            ))

    asyncio.run(main(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
    """
)


def test_concurrent_processes_assign_unique_keys(tmp_path: Path) -> None:
    store_path = tmp_path / "tickets.jsonl"
    per_proc = 10
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, str(store_path), str(per_proc), f"w{i}"]
        )
        for i in range(2)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0

    # Rebuild latest-record-per-id from the append-only JSONL.
    latest: dict[str, dict] = {}
    for line in store_path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        latest[rec["_id"]] = rec

    nums = sorted(int(rec["key"].split("-")[1]) for rec in latest.values())
    assert len(latest) == 2 * per_proc
    assert nums == list(range(1, 2 * per_proc + 1))
