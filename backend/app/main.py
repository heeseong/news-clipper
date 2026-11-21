"""FastAPI application: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import channels, clips, events, health, jobs, media, newsroom, programs, publish, review, uploads
from app.core.config import get_settings
from app.core.logging import setup_logging

log = logging.getLogger("app.request")
QUIET_PATHS = ("/healthz", "/readyz", "/api/events")
QUIET_METHODS = ("HEAD", "PATCH")  # tus offset checks and chunks


class RequestLogMiddleware:
    """Logs one line per request and sets x-request-id. Pure ASGI so streaming bodies
    (tus uploads) and responses (SSE) pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path, method = scope.get("path", ""), scope.get("method", "")
            if path not in QUIET_PATHS and method not in QUIET_METHODS:
                log.info(
                    "request",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "status": status_code,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    },
                )


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    import app.worker  # noqa: F401  (configure Celery before tasks are dispatched)

    app = FastAPI(title="News Clipper", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Location", "Upload-Offset", "Upload-Length", "Tus-Resumable", "x-request-id"],
    )

    app.add_middleware(RequestLogMiddleware)

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(programs.router)
    app.include_router(newsroom.router)
    app.include_router(clips.router)
    app.include_router(review.router)
    app.include_router(channels.router)
    app.include_router(publish.router)
    app.include_router(uploads.router)
    app.include_router(events.router)
    app.include_router(media.router)
    return app


app = create_app()
