"""In-process pub/sub carrying run events to live listeners.

Agent runs execute on worker threads (SQLAlchemy, the Anthropic SDK and
httpx are all synchronous here), while SSE responses are served on the
event loop. Those two worlds cannot share an `asyncio.Queue` directly,
so publishing hops back onto the loop with `call_soon_threadsafe`.

Deliberately in-process: a second worker would not see these events. The
database remains the durable record -- this only makes it live.
"""

import asyncio
import threading
from collections import defaultdict
from typing import Any

#: Sent when a run reaches a terminal state, so a listener knows to stop.
TERMINAL = "__end__"


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop that worker threads must publish back onto."""
        self._loop = loop

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            listeners = self._subscribers.get(run_id)
            if not listeners:
                return
            listeners.discard(queue)
            if not listeners:
                self._subscribers.pop(run_id, None)

    def listener_count(self, run_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(run_id, ()))

    def publish(self, run_id: str, payload: dict[str, Any]) -> None:
        """Fan out to this run's listeners. Safe to call from any thread."""
        loop = self._loop
        with self._lock:
            queues = list(self._subscribers.get(run_id, ()))
        if not queues:
            return

        if loop is None or not loop.is_running():
            # No loop bound (tests, scripts): deliver inline.
            for queue in queues:
                queue.put_nowait(payload)
            return

        for queue in queues:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            except RuntimeError:
                # Loop shut down mid-run; the database still has the event.
                pass


broker = EventBroker()
