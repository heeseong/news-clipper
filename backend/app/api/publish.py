"""Uploads (spec 8: /api/publish-tasks; plan 7.5): dashboard, retry, cancel, manual upload."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.clips import _clip, clip_detail
from app.api.deps import DbSession, Reviewer
from app.core.events import publish_job_changed
from app.models import Channel, Clip, PublishStatus, PublishTask, RundownItem
from app.models.base import utcnow
from app.publish import quota
from app.publish import service as svc
from app.review import service as review
from app.schemas.newsroom import ClipDetailOut
from app.schemas.publish import ChannelOut, ChannelSummaryOut, PublishSummaryOut, PublishTaskOut, QuotaOut

router = APIRouter(prefix="/api", tags=["publish"])
KST = ZoneInfo("Asia/Seoul")


def task_out(t: PublishTask, clip: Clip | None = None, item: RundownItem | None = None) -> PublishTaskOut:
    out = PublishTaskOut.model_validate(t)
    out.channel_name = t.channel.name if t.channel else ""
    if clip is not None:
        out.job_id = clip.job_id
    if item is not None:
        out.item_no, out.title = item.item_no, item.title
    return out


def _task(db, task_id: int) -> PublishTask:
    t = db.get(PublishTask, task_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "업로드 작업을 찾을 수 없습니다")
    return t


@router.get("/publish-tasks", response_model=list[PublishTaskOut])
def list_tasks(
    db: DbSession,
    status_: Annotated[list[str] | None, Query(alias="status")] = None,
    channel_id: int | None = None,
    clip_id: int | None = None,
    include_cancelled: bool = False,
    limit: int = Query(200, ge=1, le=1000),
) -> list[PublishTaskOut]:
    q = select(PublishTask, Clip, RundownItem).join(Clip, Clip.id == PublishTask.clip_id).join(
        RundownItem, RundownItem.id == Clip.rundown_item_id
    )
    if status_:
        q = q.where(PublishTask.status.in_(status_))
    elif not include_cancelled:
        q = q.where(PublishTask.status != PublishStatus.CANCELLED)
    if channel_id is not None:
        q = q.where(PublishTask.channel_id == channel_id)
    if clip_id is not None:
        q = q.where(PublishTask.clip_id == clip_id)
    rows = db.execute(q.order_by(PublishTask.id.desc()).limit(limit)).all()
    return [task_out(t, c, i) for t, c, i in rows]


@router.get("/publish/summary", response_model=PublishSummaryOut)
def summary(db: DbSession) -> PublishSummaryOut:
    """Per channel: today's (KST) scheduled and published uploads, failures; the API quota."""
    now = utcnow()
    today = now.astimezone(KST).date()
    start = datetime.combine(today, time(0), KST)
    end = start + timedelta(days=1)

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    channels = list(db.scalars(select(Channel).order_by(Channel.name)))
    tasks = list(db.scalars(select(PublishTask).where(PublishTask.status != PublishStatus.CANCELLED)))
    out = []
    for ch in channels:
        mine = [t for t in tasks if t.channel_id == ch.id]
        row = ChannelSummaryOut(channel=ChannelOut.model_validate(ch))
        for t in mine:
            if t.status == PublishStatus.QUEUED:
                row.queued += 1
            elif t.status == PublishStatus.UPLOADING:
                row.uploading += 1
            elif t.status == PublishStatus.FAILED:
                row.failed += 1
            if t.status == PublishStatus.SCHEDULED and t.publish_at and start <= aware(t.publish_at) < end:
                row.scheduled_today += 1
            if t.status == PublishStatus.PUBLISHED and t.published_at and start <= aware(t.published_at) < end:
                row.published_today += 1
        if mine or ch.status == "active":
            out.append(row)
    quotas = []
    if any(c.platform == "youtube" for c in channels):
        cap = quota.limit("youtube") or 0
        used = quota.used(db, "youtube", now)
        quotas.append(QuotaOut(platform="youtube", day=quota.quota_day(now).isoformat(), used=used, limit=cap,
                               resets_at=quota.next_reset(now), uploads_left=max(0, cap - used) // max(1, quota.upload_cost("youtube"))))  # fmt: skip
    return PublishSummaryOut(channels=out, quota=quotas)


def _run(fn, *args):
    try:
        return fn(*args)
    except svc.PublishFlowError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.post("/publish-tasks/{task_id}/retry", response_model=PublishTaskOut)
def retry(task_id: int, db: DbSession, user: Reviewer) -> PublishTaskOut:
    """Try a failed upload again (attempts start over), or only the thumbnail."""
    t = _task(db, task_id)
    what = _run(svc.retry, db, t)
    clip = db.get(Clip, t.clip_id)
    review.audit(db, user, clip, "publish_retry", {"task": t.id, "channel": t.channel.name, "what": what})
    db.commit()
    publish_job_changed(clip.job_id)
    if what == "upload":
        svc.dispatch([t.id])
    elif what == "thumbnail":
        svc.dispatch_thumbnail(t.id)
    else:
        svc.check_status(t.id)
    db.expire_all()
    return task_out(_task(db, task_id), clip, clip.rundown_item)


@router.post("/publish-tasks/{task_id}/cancel", response_model=PublishTaskOut)
def cancel(task_id: int, db: DbSession, user: Reviewer) -> PublishTaskOut:
    """Cancel the upload; a video already uploaded (scheduled or published) is deleted."""
    t = _task(db, task_id)
    clip = db.get(Clip, t.clip_id)
    had_video = t.remote_id
    _run(svc.cancel, db, t)
    review.audit(db, user, clip, "publish_cancel", {"task": t.id, "channel": t.channel.name, "deleted": had_video})
    db.commit()
    publish_job_changed(clip.job_id)
    return task_out(t, clip, clip.rundown_item)


@router.post("/publish-tasks/{task_id}/check", response_model=PublishTaskOut)
def check(task_id: int, db: DbSession) -> PublishTaskOut:
    """Ask the platform for the video's state now (normally the minute sweep does this)."""
    t = _task(db, task_id)
    if not t.remote_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "아직 업로드되지 않았습니다")
    db.commit()
    svc.check_status(task_id)
    db.expire_all()
    t = _task(db, task_id)
    clip = db.get(Clip, t.clip_id)
    return task_out(t, clip, clip.rundown_item)


@router.post("/clips/{clip_id}/publish", response_model=ClipDetailOut)
def publish_clip(clip_id: int, db: DbSession, user: Reviewer) -> ClipDetailOut:
    """Upload a rendered clip to its channels now (channels chosen after the render, or again
    after a failure)."""
    clip = _clip(db, clip_id)
    ids = _run(svc.enqueue, db, clip, user)
    if not ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "새로 올릴 채널이 없습니다 (이미 업로드했거나 진행 중)")
    review.audit(db, user, clip, "publish", {"tasks": ids})
    db.commit()
    publish_job_changed(clip.job_id)
    svc.dispatch(ids)
    db.expire_all()
    return clip_detail(_clip(db, clip_id))
