"""Review actions on clips (spec 5.8, 8; plan M5): edit, thumbnails, lock, approve, bulk, audit.

Every request that changes something names the reviewer in the X-Reviewer header (no login yet).
A clip another reviewer is editing (a fresh lock) answers 423.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from PIL import UnidentifiedImageError
from sqlalchemy import select

from app.api.clips import _clip, clip_detail
from app.api.deps import DbSession, Reviewer
from app.core.events import publish_job_changed
from app.media.ffmpeg import FfmpegError, ProbeResult
from app.models import AuditLog, Clip, Job, RundownItem, User
from app.review import service as review
from app.schemas.newsroom import (
    AuditOut,
    BulkIn,
    BulkResultOut,
    ClipDetailOut,
    ClipPatchIn,
    FrameThumbnailIn,
    RejectIn,
)
from app.storage import JobFiles
from app.thumbnails import service as thumbnails
from app.thumbnails.candidates import extract_frame

router = APIRouter(prefix="/api", tags=["review"])

UPLOAD_MAX_BYTES = 20 * 1024 * 1024


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except review.Locked as e:
        raise HTTPException(status.HTTP_423_LOCKED, str(e)) from e
    except review.NotApprovable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(e), "problems": e.problems}
        ) from e
    except review.ReviewError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


def _done(db, clip: Clip, *, rerender: bool = False) -> ClipDetailOut:
    if rerender:
        thumbnails.render_selected(db, clip.id)
    db.commit()
    publish_job_changed(clip.job_id)
    db.refresh(clip)
    return clip_detail(clip)


def _done_and_push(db, clip: Clip, pushes: list[int]) -> ClipDetailOut:
    """The thumbnail is rendered and committed first, then the platform gets it."""
    out = _done(db, clip, rerender=True)
    if pushes:
        _push_remote(pushes)
        db.expire_all()
        out = clip_detail(_clip(db, clip.id))
    return out


def _queue_render(clip_ids: list[int]) -> None:
    """Final render on the media queue (M6). Runs after the approval is committed."""
    from app.pipeline.tasks import render_clip

    for clip_id in clip_ids:
        render_clip.apply_async(args=(clip_id,), queue="media")


def _mark_remote(db, clip: Clip, *, fields=(), thumbnail: bool = False) -> list[int]:
    """Edits of an uploaded clip go to the platform (M8.3). Call before the commit; dispatch the
    returned task ids after it (_push_remote)."""
    from app.publish.service import SYNC_FIELDS, UPLOAD_STATES, mark_sync

    if clip.status not in UPLOAD_STATES or not (thumbnail or set(fields) & SYNC_FIELDS):
        return []
    return mark_sync(db, clip, thumbnail=thumbnail)


def _push_remote(task_ids: list[int]) -> None:
    if task_ids:
        from app.publish.service import dispatch_sync

        dispatch_sync(task_ids)


def _prepare_after_range(clip_id: int) -> None:
    from app.pipeline.tasks import prepare_clip_after_range

    prepare_clip_after_range.apply_async(args=(clip_id,), queue="media")


@router.patch("/clips/{clip_id}", response_model=ClipDetailOut)
def patch_clip(clip_id: int, body: ClipPatchIn, db: DbSession, user: Reviewer) -> ClipDetailOut:
    clip = _clip(db, clip_id)
    range_change = None
    if (body.start_sec is None) != (body.end_sec is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start_sec와 end_sec를 함께 보내야 합니다")
    if body.start_sec is not None:
        range_change = _run(review.set_range, db, clip, user, body.start_sec, body.end_sec)
    fields = body.model_dump(exclude_unset=True, include=set(review.METADATA_FIELDS))
    if body.clear_publish_at:
        fields["publish_at"] = None
    rerender = False
    pushes: list[int] = []
    if fields:
        diff: dict = {}
        meta = _run(review.update_metadata, db, clip, user, fields, diff)
        rerender = "thumbnail_text" in fields and meta is not None
        pushes = _mark_remote(db, clip, fields=diff, thumbnail="thumbnail_text" in diff)
    if body.warnings_acknowledged is not None:
        _run(review.acknowledge, db, clip, user, body.warnings_acknowledged)
    out = _done(db, clip, rerender=rerender)
    if pushes:
        _push_remote(pushes)
        db.expire_all()
        out = clip_detail(_clip(db, clip_id))
    if range_change and range_change.changed:
        _prepare_after_range(clip.id)  # new thumbnail candidates (and metadata for a new clip)
        db.expire_all()
        out = clip_detail(_clip(db, clip_id))
    return out


@router.post("/clips/{clip_id}/lock", response_model=ClipDetailOut)
def lock_clip(clip_id: int, db: DbSession, user: Reviewer) -> ClipDetailOut:
    """Take or refresh the edit lock (expires after LOCK_SECONDS without a refresh)."""
    clip = _clip(db, clip_id)
    _run(review.acquire_lock, db, clip, user)
    db.commit()
    return clip_detail(clip)


@router.delete("/clips/{clip_id}/lock", status_code=status.HTTP_204_NO_CONTENT)
def unlock_clip(clip_id: int, db: DbSession, user: Reviewer) -> Response:
    review.release_lock(db, _clip(db, clip_id), user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/clips/{clip_id}/approve", response_model=ClipDetailOut)
def approve_clip(clip_id: int, db: DbSession, user: Reviewer) -> ClipDetailOut:
    """Approve; the final render starts right after (spec 5.8)."""
    clip = _clip(db, clip_id)
    was = clip.status
    _run(review.approve, db, clip, user)
    out = _done(db, clip)
    if was != clip.status:
        _queue_render([clip.id])
        db.expire_all()
        out = clip_detail(_clip(db, clip_id))
    return out


@router.post("/clips/{clip_id}/render", response_model=ClipDetailOut)
def render_again(clip_id: int, db: DbSession, user: Reviewer) -> ClipDetailOut:
    """Render an approved clip again (after a failure, or to redo it)."""
    clip = _clip(db, clip_id)
    _run(review.request_render, db, clip, user)
    _done(db, clip)
    _queue_render([clip.id])
    db.expire_all()
    return clip_detail(_clip(db, clip_id))


@router.post("/clips/{clip_id}/reject", response_model=ClipDetailOut)
def reject_clip(clip_id: int, body: RejectIn, db: DbSession, user: Reviewer) -> ClipDetailOut:
    clip = _clip(db, clip_id)
    _run(review.reject, db, clip, user, body.reason)
    return _done(db, clip)


@router.post("/clips/{clip_id}/reopen", response_model=ClipDetailOut)
def reopen_clip(clip_id: int, db: DbSession, user: Reviewer) -> ClipDetailOut:
    clip = _clip(db, clip_id)
    _run(review.reopen, db, clip, user)
    return _done(db, clip)


# --- thumbnails ---


@router.post("/clips/{clip_id}/thumbnails/{thumbnail_id}/select", response_model=ClipDetailOut)
def select_thumbnail(clip_id: int, thumbnail_id: int, db: DbSession, user: Reviewer) -> ClipDetailOut:
    clip = _clip(db, clip_id)
    before = next((t.id for t in clip.thumbnails if t.selected), None)
    _run(review.select_thumbnail, db, clip, user, thumbnail_id)
    return _done_and_push(db, clip, _mark_remote(db, clip, thumbnail=True) if before != thumbnail_id else [])


@router.post("/clips/{clip_id}/thumbnails", response_model=ClipDetailOut)
def add_frame_thumbnail(clip_id: int, body: FrameThumbnailIn, db: DbSession, user: Reviewer) -> ClipDetailOut:
    """The frame at `frame_sec` (the player's current frame), taken from the original."""
    clip = _clip(db, clip_id)
    _run(review.check_lock, clip, user)
    job = db.get(Job, clip.job_id)
    files = JobFiles(job.id)
    if not job.source_path or not files.probe.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "원본 영상이 없습니다")
    if job.duration and body.frame_sec > job.duration:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "녹화본 밖의 시각입니다")
    probe = ProbeResult.model_validate_json(files.probe.read_text())
    out = thumbnails.manual_path(job.id, clip.id, f"frame_{round(body.frame_sec * 1000)}.jpg")
    try:
        extract_frame(Path(job.source_path), probe, body.frame_sec, out)
    except (FfmpegError, FileNotFoundError) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, f"프레임을 뽑지 못했습니다: {e}") from e
    _run(review.add_thumbnail, db, clip, user, str(out), "frame", round(body.frame_sec, 3))
    return _done_and_push(db, clip, _mark_remote(db, clip, thumbnail=True))


@router.put("/clips/{clip_id}/thumbnails/upload", response_model=ClipDetailOut)
async def upload_thumbnail(clip_id: int, request: Request, db: DbSession, user: Reviewer) -> ClipDetailOut:
    """An image file sent as the request body; cropped to 16:9 and scaled to 1280x720."""
    clip = _clip(db, clip_id)
    _run(review.check_lock, clip, user)
    data = await request.body()
    if not data or len(data) > UPLOAD_MAX_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "이미지는 20MB 이하여야 합니다")
    count = len(clip.thumbnails)
    out = thumbnails.manual_path(clip.job_id, clip.id, f"upload_{count + 1}.jpg")
    try:
        thumbnails.save_uploaded(data, out)
    except (UnidentifiedImageError, OSError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "이미지를 읽을 수 없습니다") from e
    _run(review.add_thumbnail, db, clip, user, str(out), "upload", None)
    return _done_and_push(db, clip, _mark_remote(db, clip, thumbnail=True))


@router.get("/clips/{clip_id}/thumbnail-preview")
def thumbnail_preview(
    clip_id: int,
    db: DbSession,
    text: str | None = Query(None, description="Thumbnail text to try (default: the saved one)"),
    thumbnail_id: int | None = None,
) -> Response:
    """The thumbnail with the given text, as it would be uploaded (nothing is saved)."""
    import io

    img = thumbnails.preview(db, _clip(db, clip_id), text, thumbnail_id)
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "선택된 썸네일이 없습니다")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/clips/{clip_id}/thumbnails/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate_thumbnails(clip_id: int, db: DbSession, user: Reviewer) -> dict:
    clip = _clip(db, clip_id)
    _run(review.check_lock, clip, user)
    if clip.needs_manual_range:
        raise HTTPException(status.HTTP_409_CONFLICT, "구간을 먼저 정해야 합니다")
    _prepare_after_range(clip.id)
    return {"clip_id": clip.id, "queued": True}


# --- bulk, audit ---


@router.post("/clips/bulk", response_model=list[BulkResultOut])
def bulk(body: BulkIn, db: DbSession, user: Reviewer) -> list[BulkResultOut]:
    """Approve several clips, schedule them one after another (cue sheet order), or upload them."""
    clips = list(
        db.scalars(select(Clip).join(RundownItem).where(Clip.id.in_(body.clip_ids)).order_by(RundownItem.item_no))
    )
    if len(clips) != len(set(body.clip_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 클립이 있습니다")
    results = []
    approved: list[int] = []
    uploads: list[int] = []
    if body.action == "schedule":
        if body.first_at is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "첫 예약 시각(first_at)이 필요합니다")
        times = review.schedule_times(body.first_at, body.interval_minutes, len(clips))
        for clip, at in zip(clips, times, strict=True):
            changes = {"publish_at": at} | ({"privacy": body.privacy} if body.privacy else {})
            try:
                diff: dict = {}
                review.update_metadata(db, clip, user, changes, diff)
                uploads.extend(_mark_remote(db, clip, fields=diff))  # uploaded clips: push the new time
                results.append(BulkResultOut(clip_id=clip.id, ok=True, publish_at=at))
            except review.ReviewError as e:
                results.append(BulkResultOut(clip_id=clip.id, ok=False, error=str(e)))
    elif body.action == "publish":
        from app.publish import service as publishing

        for clip in clips:
            try:
                ids = publishing.enqueue(db, clip, user)
                if ids:
                    review.audit(db, user, clip, "publish", {"tasks": ids})
                    uploads.extend(ids)
                results.append(BulkResultOut(clip_id=clip.id, ok=bool(ids), error=None if ids else "새로 올릴 채널이 없습니다"))
            except publishing.PublishFlowError as e:
                results.append(BulkResultOut(clip_id=clip.id, ok=False, error=str(e)))
    else:
        for clip in clips:
            try:
                was = clip.status
                review.approve(db, clip, user)
                results.append(BulkResultOut(clip_id=clip.id, ok=True))
                if was != clip.status:
                    approved.append(clip.id)
            except review.NotApprovable as e:
                results.append(BulkResultOut(clip_id=clip.id, ok=False, error=str(e), problems=e.problems))
            except review.ReviewError as e:
                results.append(BulkResultOut(clip_id=clip.id, ok=False, error=str(e)))
    db.commit()
    for job_id in {c.job_id for c in clips}:
        publish_job_changed(job_id)
    _queue_render(approved)
    if uploads:
        from app.publish.service import dispatch, dispatch_sync

        (dispatch_sync if body.action == "schedule" else dispatch)(uploads)
    return results


@router.get("/clips/{clip_id}/audit", response_model=list[AuditOut])
def clip_audit(clip_id: int, db: DbSession) -> list[AuditOut]:
    _clip(db, clip_id)
    rows = db.execute(
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.entity == "clip", AuditLog.entity_id == clip_id)
        .order_by(AuditLog.id.desc())
    ).all()
    return [AuditOut.model_validate(log).model_copy(update={"user_name": name}) for log, name in rows]

