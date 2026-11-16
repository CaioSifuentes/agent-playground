"""In-process pub/sub used by Server-Sent Events.

Each connected dashboard gets its own bounded queue. Slow clients lose their
oldest queued event instead of blocking API requests. This is intentionally
in-process: the app is designed to run as a single Uvicorn worker.
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBroker:
    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        message = {"type": event_type, "payload": payload}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Keep the newest events for a slow consumer.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
