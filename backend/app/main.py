"""FastAPI application: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, jobs
from app.core.config import get_settings
from app.core.logging import setup_logging

log = logging.getLogger("app.request")


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
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        if request.url.path not in ("/healthz", "/readyz"):
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
        return response

    app.include_router(health.router)
    app.include_router(jobs.router)
    return app


app = create_app()
