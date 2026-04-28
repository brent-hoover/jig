import asyncio

import pytest

from jig.prompt_registry import PromptRegistry


@pytest.mark.asyncio
async def test_register_returns_unique_ids():
    reg = PromptRegistry()
    id1, _f1 = reg.register()
    id2, _f2 = reg.register()
    assert id1 != id2


@pytest.mark.asyncio
async def test_deliver_resolves_future():
    reg = PromptRegistry()
    prompt_id, future = reg.register()
    assert reg.deliver(prompt_id, "the reply") is True
    assert await future == "the reply"


@pytest.mark.asyncio
async def test_deliver_unknown_id_returns_false():
    reg = PromptRegistry()
    assert reg.deliver("nope", "x") is False


@pytest.mark.asyncio
async def test_deliver_consumes_pending_state():
    reg = PromptRegistry()
    prompt_id, future = reg.register()
    reg.deliver(prompt_id, "first")
    # Second deliver for same id should fail (already removed from pending)
    assert reg.deliver(prompt_id, "second") is False
    assert await future == "first"


@pytest.mark.asyncio
async def test_cancel_pending():
    reg = PromptRegistry()
    prompt_id, future = reg.register()
    assert reg.cancel(prompt_id) is True
    with pytest.raises(asyncio.CancelledError):
        await future


@pytest.mark.asyncio
async def test_cancel_all():
    reg = PromptRegistry()
    _id1, f1 = reg.register()
    _id2, f2 = reg.register()
    n = reg.cancel_all()
    assert n == 2
    for f in (f1, f2):
        with pytest.raises(asyncio.CancelledError):
            await f
