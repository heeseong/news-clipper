"""Server-sent events: `data: {"type": "job", "id": N}` whenever job N changes (plan 1.8)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core import events
from app.core.config import get_settings

router = APIRouter(prefix="/api", tags=["events"])
log = logging.getLogger(__name__)


@router.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    async def gen():
        yield "retry: 3000\n\n"
        if get_settings().celery_always_eager:  # no Redis: keepalives only
            while not await request.is_disconnected():
                yield events.sse_format(None)
                await asyncio.sleep(15)
            return
        try:
            async for data in events.subscribe():
                if await request.is_disconnected():
                    break
                yield events.sse_format(data)
        except Exception as e:  # noqa: BLE001 - the browser reconnects after `retry`
            log.warning("event stream ended", extra={"error": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
