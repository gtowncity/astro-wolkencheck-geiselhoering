"""Bounded in-memory event stream used by the same-origin SSE endpoint."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: int
    event_type: str
    created_at: datetime
    payload: dict[str, Any]

    def sse(self) -> str:
        body = json.dumps(
            {
                "eventId": self.event_id,
                "eventType": self.event_type,
                "createdAt": self.created_at.isoformat().replace("+00:00", "Z"),
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"id: {self.event_id}\nevent: {self.event_type}\ndata: {body}\n\n"


class EventBus:
    def __init__(self, *, max_events: int = 256) -> None:
        self._events: deque[RuntimeEvent] = deque(maxlen=max_events)
        self._next_id = 1
        self._condition = asyncio.Condition()

    def recent_after(self, last_event_id: int) -> tuple[RuntimeEvent, ...]:
        return tuple(event for event in self._events if event.event_id > last_event_id)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> RuntimeEvent:
        async with self._condition:
            event = RuntimeEvent(
                event_id=self._next_id,
                event_type=event_type,
                created_at=datetime.now(UTC),
                payload=payload,
            )
            self._next_id += 1
            self._events.append(event)
            self._condition.notify_all()
            return event

    async def stream(
        self,
        *,
        last_event_id: int = 0,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[str]:
        cursor = last_event_id
        while True:
            pending = self.recent_after(cursor)
            if pending:
                for event in pending:
                    cursor = event.event_id
                    yield event.sse()
                continue
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait(), timeout=heartbeat_seconds
                    )
            except TimeoutError:
                yield ": heartbeat\n\n"
