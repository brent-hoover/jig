import asyncio
import json


from jig.events import EventEmitter, JigEvent


class TestJigEvent:
    def test_serializes_to_json(self):
        event = JigEvent(type="phase_started", data={"phase": "spec", "role_cfg": "spec"})
        text = event.to_json()
        parsed = json.loads(text)
        assert parsed["type"] == "phase_started"
        assert parsed["data"]["phase"] == "spec"

    def test_event_types(self):
        for event_type in (
            "workflow_started", "phase_started", "phase_completed",
            "workflow_completed", "workflow_paused", "workflow_failed",
            "message", "agent_output",
        ):
            event = JigEvent(type=event_type, data={})
            assert event.type == event_type


class TestEventEmitter:
    async def test_subscribe_and_emit(self):
        emitter = EventEmitter()
        queue = emitter.subscribe()
        event = JigEvent(type="phase_started", data={"phase": "spec"})
        await emitter.emit(event)
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.type == "phase_started"

    async def test_multiple_subscribers(self):
        emitter = EventEmitter()
        q1 = emitter.subscribe()
        q2 = emitter.subscribe()
        event = JigEvent(type="workflow_started", data={})
        await emitter.emit(event)
        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.type == r2.type

    async def test_unsubscribe(self):
        emitter = EventEmitter()
        queue = emitter.subscribe()
        emitter.unsubscribe(queue)
        event = JigEvent(type="phase_started", data={})
        await emitter.emit(event)
        assert queue.empty()
