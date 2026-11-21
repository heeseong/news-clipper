"""Daily maintenance (Celery beat)."""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.db import session_scope
from app.worker import celery_app

from . import cleanup

log = logging.getLogger(__name__)


@celery_app.task(name="maintenance.cleanup")
def run_cleanup(force: bool = False) -> dict:
    """Retention cleanup; does nothing unless RETENTION_ENABLED (or forced from the admin screen)."""
    if not (force or get_settings().retention_enabled):
        return {"skipped": "retention disabled"}
    with session_scope() as s:
        result = cleanup.run(s)
    log.info("cleanup finished", extra={"items": len(result.deleted), "bytes": result.bytes, "errors": len(result.errors)})
    return {"items": len(result.deleted), "bytes": result.bytes, "errors": result.errors}
