"""Administration (plan M9): storage and retention cleanup, operating metrics.

No login (2026-11-21 decision: names only, as in review), like the rest of the app.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Reviewer
from app.core.config import get_settings
from app.maintenance import cleanup, metrics

router = APIRouter(prefix="/api/admin", tags=["admin"])
PLAN_LIMIT = 300


def _item(i: cleanup.Item) -> dict:
    return {"kind": i.kind, "job_id": i.job_id, "clip_id": i.clip_id, "upload_id": i.upload_id, "bytes": i.bytes,
            "reason": i.reason, "paths": [str(p) for p in i.paths]}  # fmt: skip


@router.get("/storage")
def storage(db: DbSession) -> dict:
    """Disk usage by kind, the retention settings, and what a cleanup run would delete now."""
    s = get_settings()
    items = cleanup.plan(db)
    return {
        "usage": cleanup.usage(),
        "retention": {"enabled": s.retention_enabled, "source_days": s.retention_source_days,
                      "work_days": s.retention_work_days, "render_days": s.retention_render_days,
                      "upload_hours": s.retention_upload_hours},  # fmt: skip
        "plan": [_item(i) for i in items[:PLAN_LIMIT]],
        "plan_count": len(items),
        "plan_bytes": sum(i.bytes for i in items),
    }


@router.post("/cleanup")
def run_cleanup(db: DbSession, user: Reviewer) -> dict:
    """Delete what /storage lists now (also when the daily run is switched off)."""
    result = cleanup.run(db)
    db.commit()
    return {"deleted": [_item(i) for i in result.deleted], "bytes": result.bytes, "errors": result.errors}


@router.get("/metrics")
def get_metrics(db: DbSession, days: int = Query(30, ge=1, le=366)) -> dict:
    return metrics.summary(db, days)
