from __future__ import annotations

import logging

import redis
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import AppSettings, DbSession

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


@router.get("/healthz")
def healthz() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> dict:
    """Readiness: the database and Redis are reachable."""
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
    if settings.celery_always_eager:
        checks["redis"] = "skipped (eager mode)"
    else:
        try:
            redis.Redis.from_url(settings.redis_url, socket_timeout=2).ping()
            checks["redis"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["redis"] = f"error: {type(e).__name__}"
    ok = all(v == "ok" or v.startswith("skipped") for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        log.warning("readiness check failed", extra=checks)
    return {"status": "ok" if ok else "error", "checks": checks}
