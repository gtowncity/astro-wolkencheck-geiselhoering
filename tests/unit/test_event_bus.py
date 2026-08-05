import json

import pytest

from nowcast_service.runtime.event_bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_replays_formats_and_heartbeats() -> None:
    bus = EventBus(max_events=2)
    await bus.publish("snapshot", {"value": 1})
    second = await bus.publish("alert", {"value": 2})
    assert [event.event_id for event in bus.recent_after(1)] == [2]
    assert json.loads(second.sse().split("data: ", 1)[1])["payload"]["value"] == 2
    stream = bus.stream(last_event_id=2, heartbeat_seconds=0.01)
    assert await anext(stream) == ": heartbeat\n\n"
    await stream.aclose()
