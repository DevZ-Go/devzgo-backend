"""
Per-user event fan-out for WebSocket clients.

In memory by default (one backend process). Set REDIS_URL to fan out through Redis pub/sub
so every backend instance delivers events to its own connected sockets.
"""

import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, Optional, Set
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")
CHANNEL = "devzgo:events"


class Broadcaster:
    def __init__(self) -> None:
        self._subs: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._redis = None  # sync client used for publishing from request threads

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        url = os.getenv("REDIS_URL")
        if url:
            import redis
            import redis.asyncio as aioredis

            self._redis = redis.Redis.from_url(url)
            asyncio.create_task(self._listen(aioredis.from_url(url)))
            log.info("Realtime: using Redis pub/sub")

    async def _listen(self, client) -> None:
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL)
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                self._deliver(msg["data"])

    def publish(self, user_id: UUID, payload: Dict[str, Any]) -> None:
        """Thread-safe: callable from sync route handlers."""
        raw = json.dumps({"user_id": str(user_id), "event": payload}, default=str)
        if self._redis is not None:
            self._redis.publish(CHANNEL, raw)
        elif self._loop is not None:
            self._loop.call_soon_threadsafe(self._deliver, raw)

    def _deliver(self, raw) -> None:
        data = json.loads(raw)
        for queue in list(self._subs.get(data["user_id"], ())):
            queue.put_nowait(data["event"])

    def subscribe(self, user_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[user_id].add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        self._subs[user_id].discard(queue)
        if not self._subs[user_id]:
            del self._subs[user_id]


broadcaster = Broadcaster()


def queue_event(db: Session, user_id: UUID, payload: Dict[str, Any]) -> None:
    """Send `payload` to `user_id` once the current transaction commits (dropped on rollback)."""
    db.info.setdefault("realtime_events", []).append((user_id, payload))


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    for user_id, payload in session.info.pop("realtime_events", []):
        try:
            broadcaster.publish(user_id, payload)
        except Exception as exc:  # realtime is best-effort; the data is already saved
            log.warning("Realtime publish failed: %s", exc)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop("realtime_events", None)
