"""Change notifications for the browser (Redis pub/sub → SSE, plan 1.8).

Workers and the API publish "job N changed"; the SSE endpoint relays these to browsers, which
refetch what they show. Payloads stay small: state always comes from the database.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

import redis
import redis.asyncio as aioredis

from .config import get_settings

log = logging.getLogger(__name__)

CHANNEL = "newsclipper:events"


@lru_cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


def publish_job_changed(job_id: int) -> None:
    """Best effort: a lost notification only delays the UI until its next refetch."""
    if get_settings().celery_always_eager:
        return
    try:
        _client().publish(CHANNEL, json.dumps({"type": "job", "id": job_id}))
    except redis.RedisError as e:
        log.warning("could not publish event", extra={"job_id": job_id, "error": str(e)})


async def subscribe(keepalive_seconds: float = 15.0) -> AsyncIterator[str | None]:
    """Yields event payloads (JSON strings), or None every `keepalive_seconds` without events."""
    client = aioredis.Redis.from_url(get_settings().redis_url)
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=keepalive_seconds)
            if msg is None:
                yield None
            elif msg.get("type") == "message":
                data = msg["data"]
                yield data.decode() if isinstance(data, bytes) else str(data)
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        await client.aclose()


def sse_format(data: str | None) -> str:
    """One SSE frame; None becomes a comment line that keeps proxies from closing the stream."""
    return ": keepalive\n\n" if data is None else f"data: {data}\n\n"
