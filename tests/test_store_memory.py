from jig.store.memory import Handoff, Learning, MemoryStore


async def test_write_and_read_handoff(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    doc_id = await mem.write_handoff(
        ticket_id="JIG-1",
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
        ticket_id="JIG-1", from_phase="spec", to_phase="test",
        summary="first",
    )
    # Ensure distinct timestamps
    import asyncio
    await asyncio.sleep(0.01)
    await mem.write_handoff(
        ticket_id="JIG-1", from_phase="spec", to_phase="test",
        summary="second",
    )
    handoff = await mem.read_handoff("JIG-1", "test")
    assert handoff.summary == "second"


async def test_add_and_get_learnings_returns_models(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.add_learning(
        ticket_id="JIG-1", phase="test",
        content="pytest fixtures are sticky", tags=["testing"],
    )
    learnings = await mem.get_learnings("JIG-1")
    assert len(learnings) == 1
    assert isinstance(learnings[0], Learning)
    assert learnings[0].content == "pytest fixtures are sticky"


async def test_get_learnings_filters_by_tag_overlap(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.add_learning(
        ticket_id="JIG-1", phase="test", content="a", tags=["testing"],
    )
    await mem.add_learning(
        ticket_id="JIG-1", phase="test", content="b", tags=["architecture"],
    )
    await mem.add_learning(
        ticket_id="JIG-1", phase="test", content="c", tags=["gotcha", "testing"],
    )
    filtered = await mem.get_learnings("JIG-1", tags=["testing"])
    assert {item.content for item in filtered} == {"a", "c"}


async def test_get_learnings_respects_limit_and_sort_order(tmp_path):
    import asyncio
    mem = MemoryStore(tmp_path)
    await mem.load()
    for content in ["first", "second", "third", "fourth"]:
        await mem.add_learning(ticket_id="JIG-1", phase="test", content=content)
        await asyncio.sleep(0.01)
    recent = await mem.get_learnings("JIG-1", limit=2)
    assert [item.content for item in recent] == ["fourth", "third"]


async def test_get_learnings_filters_by_issue(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.add_learning(ticket_id="JIG-1", phase="test", content="a")
    await mem.add_learning(ticket_id="JIG-2", phase="test", content="b")
    jig1 = await mem.get_learnings("JIG-1")
    assert [item.content for item in jig1] == ["a"]


async def test_context_block_with_handoff_and_learnings(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.write_handoff(
        ticket_id="JIG-1", from_phase="spec", to_phase="test",
        summary="spec approved",
    )
    await mem.add_learning(
        ticket_id="JIG-1", phase="spec",
        content="prefer fixtures", tags=["testing"],
    )
    block = await mem.get_context_block("JIG-1", "test")
    assert "## Handoff from spec" in block
    assert "spec approved" in block
    assert "## Learnings" in block
    assert "prefer fixtures" in block
    assert "testing" in block


async def test_context_block_handoff_only_no_learnings_section(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.write_handoff(
        ticket_id="JIG-1", from_phase="spec", to_phase="test",
        summary="done",
    )
    block = await mem.get_context_block("JIG-1", "test")
    assert "## Handoff from spec" in block
    assert "## Learnings" not in block


async def test_context_block_learnings_only_no_handoff_section(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.add_learning(
        ticket_id="JIG-1", phase="spec", content="x",
    )
    block = await mem.get_context_block("JIG-1", "test")
    assert "## Handoff" not in block
    assert "## Learnings" in block
    assert "x" in block


async def test_context_block_empty_returns_empty_string(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    block = await mem.get_context_block("JIG-1", "test")
    assert block == ""


async def test_add_and_get_role_learning(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.add_role_learning(role="dev", content="always uv run pytest")
    await mem.add_role_learning(role="dev", content="use pathlib not os.path")
    await mem.add_role_learning(role="qa", content="run full suite before signoff")

    dev_memories = await mem.get_role_learnings("dev")
    assert [m.content for m in dev_memories] == [
        "always uv run pytest",
        "use pathlib not os.path",
    ]
    qa_memories = await mem.get_role_learnings("qa")
    assert [m.content for m in qa_memories] == ["run full suite before signoff"]


async def test_role_learnings_empty_when_none(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    assert await mem.get_role_learnings("dev") == []


async def test_role_learnings_persist_across_reload(tmp_path):
    mem = MemoryStore(tmp_path)
    await mem.load()
    await mem.add_role_learning(role="dev", content="x")

    mem2 = MemoryStore(tmp_path)
    await mem2.load()
    assert [m.content for m in await mem2.get_role_learnings("dev")] == ["x"]
