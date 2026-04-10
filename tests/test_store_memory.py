import pytest
from jig.store.memory import Handoff, Learning, MemoryStore


async def test_write_and_read_handoff(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    doc_id = await mem.write_handoff(
        issue_id="JIG-1",
        from_phase="spec",
        to_phase="test",
        summary="spec complete",
        artifacts=["spec.md"],
    )
    assert isinstance(doc_id, str)

    handoff = await mem.read_handoff("JIG-1", "test")
    assert handoff is not None
    assert isinstance(handoff, Handoff)
    assert handoff.from_phase == "spec"
    assert handoff.to_phase == "test"
    assert handoff.summary == "spec complete"
    assert handoff.artifacts == ["spec.md"]


async def test_read_handoff_miss_returns_none(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    assert await mem.read_handoff("JIG-1", "test") is None


async def test_read_handoff_returns_most_recent(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.write_handoff(
        issue_id="JIG-1", from_phase="spec", to_phase="test",
        summary="first",
    )
    # Ensure distinct timestamps
    import asyncio
    await asyncio.sleep(0.01)
    await mem.write_handoff(
        issue_id="JIG-1", from_phase="spec", to_phase="test",
        summary="second",
    )
    handoff = await mem.read_handoff("JIG-1", "test")
    assert handoff.summary == "second"
