"""Upload flow (plan 7.5): approved + rendered clip → PublishTask per channel → publish worker.

    RENDERED ──enqueue──▶ QUEUED ──worker──▶ UPLOADING ──▶ UPLOADED ─┬▶ PROCESSING ─▶ PUBLISHED
                             ▲                    │                  └▶ SCHEDULED ──▶ PUBLISHED
                             └── retry later ◀────┤ network / 5xx (1, 2, 4, 8, 16 min), quota (next day)
                                                  └▶ FAILED (auth, invalid, remote, too many tries)

The clip's status follows its tasks (the least advanced one; any failure → UPLOAD_FAILED).
Long calls (the upload itself) run outside database sessions; progress and the resumable session
URI are written as they happen, so a retry continues where the platform stopped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.events import publish_job_changed
from app.models import (
    Channel,
    ChannelStatus,
    Clip,
    ClipMetadata,
    ClipStatus,
    PublishStatus,
    PublishTask,
    User,
)
from app.models.base import utcnow
from app.storage import JobFiles

from . import quota
from .base import PublishError, PublishMeta, RemoteStatus
from .channels import ChannelError, active_channels
from .factory import publisher_for

log = logging.getLogger(__name__)

BACKOFF_MINUTES = (1, 2, 4, 8, 16)
QUOTA_GRACE = timedelta(minutes=5)  # after the reset, so clocks need not agree to the second
SCHEDULE_MARGIN = timedelta(minutes=2)  # publishAt must still be in the future when the upload starts
PUBLISH_LATE = timedelta(minutes=15)  # scheduled but still private this long after publish_at → failed
STALE_UPLOAD = timedelta(minutes=10)  # UPLOADING without progress: the worker died
LOST_DISPATCH = timedelta(minutes=2)
CHECK_STEPS_MIN = (1, 2, 4, 8, 10)  # status checks while the platform works (M8.1)
AFTER_PUBLISH_MIN = (10, 60, 360, 1440)  # a few more after publication: rejected or removed?
THUMBNAIL_RETRY_MIN = (2, 10, 30)  # transient thumbnail errors (M8.2)
STALE_SYNC = timedelta(minutes=10)
WATCHED = (PublishStatus.UPLOADED, PublishStatus.PROCESSING, PublishStatus.SCHEDULED, PublishStatus.PUBLISHED)

CLIP_FOR_TASK = {
    PublishStatus.QUEUED: ClipStatus.UPLOADING,
    PublishStatus.UPLOADING: ClipStatus.UPLOADING,
    PublishStatus.UPLOADED: ClipStatus.UPLOADED,
    PublishStatus.PROCESSING: ClipStatus.PROCESSING,
    PublishStatus.SCHEDULED: ClipStatus.SCHEDULED,
    PublishStatus.PUBLISHED: ClipStatus.PUBLISHED,
    PublishStatus.FAILED: ClipStatus.UPLOAD_FAILED,
}
# The clip shows its least advanced upload (a failure first).
CLIP_ORDER = [ClipStatus.UPLOAD_FAILED, ClipStatus.UPLOADING, ClipStatus.UPLOADED, ClipStatus.PROCESSING,
              ClipStatus.SCHEDULED, ClipStatus.PUBLISHED]  # fmt: skip
UPLOAD_STATES = tuple(CLIP_ORDER)
PUBLISHABLE = (ClipStatus.RENDERED, ClipStatus.UPLOAD_FAILED)


class PublishFlowError(Exception):
    """Cannot be done in the current state (HTTP 409)."""


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def current_tasks(session: Session, clip_id: int) -> list[PublishTask]:
    return list(
        session.scalars(
            select(PublishTask)
            .where(PublishTask.clip_id == clip_id, PublishTask.status != PublishStatus.CANCELLED)
            .order_by(PublishTask.id)
        )
    )


def sync_clip(session: Session, clip: Clip) -> None:
    tasks = current_tasks(session, clip.id)
    if tasks:
        states = {CLIP_FOR_TASK[t.status] for t in tasks}
        clip.status = next(s for s in CLIP_ORDER if s in states)
    elif clip.status in UPLOAD_STATES:
        clip.status = ClipStatus.RENDERED if clip.render_path else ClipStatus.APPROVED
    session.flush()


def has_remote_video(session: Session, clip_id: int) -> bool:
    return any(t.remote_id for t in current_tasks(session, clip_id))


def with_hashtags(description: str, hashtags: list[str]) -> str:
    """The reviewed description with the clip's hashtags at the end (2026-11-21), unless already there."""
    missing = [h for h in hashtags if h and h not in description]
    return f"{description.rstrip()}\n\n{' '.join(missing)}".strip() if missing else description


def publish_meta(meta: ClipMetadata, privacy: str | None = None, publish_at: datetime | None = None) -> PublishMeta:
    s = get_settings()
    description = with_hashtags(meta.description or "", meta.hashtags or [])
    return PublishMeta(
        title=meta.title, description=description, tags=list(meta.tags or []),
        privacy=privacy or meta.privacy, publish_at=_aware(publish_at if publish_at is not None else meta.publish_at),
        category_id=s.youtube_category_id, language=s.youtube_language, notify_subscribers=s.youtube_notify_subscribers,
    )  # fmt: skip


# --- enqueue ---


def enqueue(session: Session, clip: Clip, user: User | None = None, *, strict: bool = True) -> list[int]:
    """Create (or re-queue failed) tasks for the clip's channels. Returns task ids to dispatch
    after the commit. strict=False (automatic, after the render) skips instead of raising."""
    meta = session.get(ClipMetadata, clip.id)
    try:
        if clip.status not in PUBLISHABLE:
            raise PublishFlowError("렌더가 끝난 승인 클립만 업로드할 수 있습니다")
        if meta is None or not meta.channel_ids:
            raise PublishFlowError("업로드할 채널을 고르지 않았습니다")
        channels = active_channels(session, list(meta.channel_ids))
        if strict and meta.publish_at is not None and _aware(meta.publish_at) <= utcnow() + SCHEDULE_MARGIN:
            raise PublishFlowError("예약 시각이 지났거나 너무 가깝습니다. 예약 시각을 바꾼 뒤 업로드하세요")
        if not clip.render_path or not Path(clip.render_path).exists():
            raise PublishFlowError("렌더 파일이 없습니다")
    except (PublishFlowError, ChannelError) as e:
        if strict:
            raise PublishFlowError(str(e)) from e
        log.info("not uploading", extra={"clip_id": clip.id, "reason": str(e)})
        return []
    existing = {t.channel_id: t for t in current_tasks(session, clip.id)}
    queued = []
    for ch in channels:
        t = existing.get(ch.id)
        if t is None:
            t = PublishTask(clip_id=clip.id, channel_id=ch.id, created_by=user.id if user else None)
            session.add(t)
        elif t.status != PublishStatus.FAILED:
            continue
        else:
            _reset_for_retry(t)
        t.status, t.privacy, t.publish_at = PublishStatus.QUEUED, meta.privacy, _aware(meta.publish_at)
        session.flush()
        queued.append(t.id)
    sync_clip(session, clip)
    return queued


def _reset_for_retry(t: PublishTask) -> None:
    if t.error_kind == "remote":  # rejected / deleted on the platform: upload a new video
        t.remote_id = t.remote_url = t.uploaded_at = None
        t.remote_status, t.thumbnail_status, t.thumbnail_error = {}, None, None
    t.attempts, t.next_retry_at, t.last_error, t.error_kind = 0, None, None, None


def dispatch(task_ids: list[int]) -> None:
    from .tasks import upload

    for task_id in task_ids:
        upload.apply_async(args=(task_id,), queue="publish")


def dispatch_thumbnail(task_id: int) -> None:
    from .tasks import thumbnail

    thumbnail.apply_async(args=(task_id,), queue="publish")


# --- the upload ---


@dataclass
class _Job:
    task_id: int
    clip_id: int
    job_id: int
    channel: Channel
    video: Path
    thumbnail: Path
    meta: PublishMeta
    resume_uri: str | None


def _finish_change(session: Session, t: PublishTask) -> int:
    clip = session.get(Clip, t.clip_id)
    sync_clip(session, clip)
    return clip.job_id


def _defer_for_quota(t: PublishTask, now: datetime, message: str) -> None:
    t.status, t.error_kind = PublishStatus.QUEUED, "quota"
    t.next_retry_at = quota.next_reset(now) + QUOTA_GRACE
    t.last_error = message


def _fail(t: PublishTask, message: str, kind: str) -> None:
    t.status, t.last_error, t.error_kind, t.next_retry_at = PublishStatus.FAILED, message, kind, None


def _claim(task_id: int, now: datetime) -> _Job | str:
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        if t is None or t.status != PublishStatus.QUEUED:
            return "skipped"
        if t.next_retry_at and _aware(t.next_retry_at) > now:
            return "waiting"
        clip = s.get(Clip, t.clip_id)
        meta = s.get(ClipMetadata, t.clip_id)
        ch = t.channel
        problem = None
        if ch.status != ChannelStatus.ACTIVE:
            problem = ("채널을 다시 연결해야 합니다", "auth")
        elif meta is None or not clip.render_path or not Path(clip.render_path).exists():
            problem = ("렌더 파일이나 메타데이터가 없습니다", "invalid")
        elif t.publish_at is not None and _aware(t.publish_at) <= now + SCHEDULE_MARGIN and not t.resumable_uri:
            problem = ("예약 시각이 지났습니다. 예약 시각을 바꾼 뒤 다시 시도하세요", "invalid")
        if problem:
            _fail(t, *problem)
            _finish_change(s, t)
            return "failed"
        if not t.resumable_uri and not quota.reserve(s, ch.platform, quota.upload_cost(ch.platform), now):
            _defer_for_quota(t, now, f"오늘 API 할당량을 다 써서 {quota.kst(quota.next_reset(now) + QUOTA_GRACE)}(한국 시간)에 올립니다")
            _finish_change(s, t)
            return "deferred"
        if not t.resumable_uri:
            t.quota_units += quota.upload_cost(ch.platform)
        t.status, t.attempts, t.next_retry_at = PublishStatus.UPLOADING, t.attempts + 1, None
        video = Path(clip.render_path)
        t.file_size = video.stat().st_size
        job_id = _finish_change(s, t)
        return _Job(t.id, clip.id, job_id, ch, video, JobFiles(clip.job_id).thumbnail(clip.id),
                    publish_meta(meta, t.privacy, t.publish_at), t.resumable_uri)  # fmt: skip


def run_upload(task_id: int, now: datetime | None = None) -> str:
    now = now or utcnow()
    job = _claim(task_id, now)
    if isinstance(job, str):
        return job
    publish_job_changed(job.job_id)

    def on_session(uri: str) -> None:
        with session_scope() as s:
            s.get(PublishTask, task_id).resumable_uri = uri

    last = 0.0

    def on_progress(sent: int, total: int) -> None:
        nonlocal last
        if time.monotonic() - last < 2 and sent < total:
            return
        last = time.monotonic()
        with session_scope() as s:
            s.get(PublishTask, task_id).bytes_sent = sent
        publish_job_changed(job.job_id)

    log.info("upload started", extra={"task_id": task_id, "clip_id": job.clip_id, "channel": job.channel.name})
    try:
        publisher = publisher_for(job.channel)
        result = publisher.upload(job.video, job.meta, resume_uri=job.resume_uri, on_session=on_session, on_progress=on_progress)
    except PublishError as e:
        return _upload_failed(task_id, e, now)
    except OSError as e:
        return _upload_failed(task_id, PublishError(f"파일을 읽지 못했습니다: {e}", "invalid"), now)
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        t.remote_id, t.remote_url, t.uploaded_at = result.remote_id, result.url, utcnow()
        t.resumable_uri, t.bytes_sent, t.status = None, t.file_size or 0, PublishStatus.UPLOADED
        t.last_error = t.error_kind = None
        if result.status is not None:
            apply_remote(t, result.status, now)
        schedule_next_check(t, now, changed=True)
        sync_waiting = t.sync_status == "pending"  # edited while uploading (M8.3)
        _finish_change(s, t)
    log.info("upload finished", extra={"task_id": task_id, "remote_id": result.remote_id})
    set_thumbnail(task_id, publisher, now=now, charge=False)
    if sync_waiting:
        dispatch_sync([task_id])
    publish_job_changed(job.job_id)
    return "uploaded"


def _upload_failed(task_id: int, e: PublishError, now: datetime) -> str:
    s_ = get_settings()
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        ch = t.channel
        if e.reason == "sessionExpired":
            t.resumable_uri = None
        if e.kind == "quota":
            t.attempts = max(0, t.attempts - 1)
            when = quota.kst(quota.next_reset(now) + QUOTA_GRACE)
            if e.reason == "uploadLimitExceeded":
                # The channel's own daily upload count (seen at the 11th upload of a day on the
                # test channel), not the project's units: other channels can still upload.
                _defer_for_quota(t, now, f"채널의 하루 업로드 개수 한도에 걸렸습니다(uploadLimitExceeded): {when}(한국 시간)에 다시 올립니다")
            else:
                quota.exhaust(s, ch.platform, now)
                _defer_for_quota(t, now, f"API 할당량 초과({e.reason}): {when}(한국 시간)에 다시 올립니다")
            outcome = "deferred"
        elif e.retryable and t.attempts < s_.publish_max_attempts:
            minutes = BACKOFF_MINUTES[min(t.attempts, len(BACKOFF_MINUTES)) - 1]
            t.status, t.error_kind, t.last_error = PublishStatus.QUEUED, e.kind, str(e)
            t.next_retry_at = now + timedelta(minutes=minutes)
            outcome = "retry"
        else:
            if e.kind == "auth" and ch.status == ChannelStatus.ACTIVE:
                ch.status, ch.last_error = ChannelStatus.REAUTH_REQUIRED, str(e)
            message = str(e) if not e.retryable else f"{e} ({t.attempts}회 시도)"
            _fail(t, message, e.kind)
            outcome = "failed"
        job_id = _finish_change(s, t)
    log.warning("upload failed", extra={"task_id": task_id, "kind": e.kind, "error": str(e), "outcome": outcome})
    publish_job_changed(job_id)
    return outcome


def set_thumbnail(task_id: int, publisher=None, now: datetime | None = None, *, charge: bool = True) -> str:
    """thumbnails.set; a failure keeps the video and only marks the thumbnail (spec 5.10).
    Transient errors are tried again by the sweep (2, 10, 30 minutes later, M8.2)."""
    now = now or utcnow()
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        if t is None or not t.remote_id:
            return "skipped"
        image = JobFiles(s.get(Clip, t.clip_id).job_id).thumbnail(t.clip_id)
        ch = t.channel
        remote_id = t.remote_id
    retryable = False
    if not image.exists():
        outcome, error = "none", "썸네일 파일이 없습니다"
    else:
        try:
            (publisher or publisher_for(ch)).set_thumbnail(remote_id, image)
            outcome, error = "ok", None
        except PublishError as e:
            outcome, error, retryable = "failed", str(e), e.retryable
            if e.reason in ("forbidden",) or "custom" in str(e).lower():
                error += " (채널에서 맞춤 썸네일을 쓰려면 YouTube 전화번호 인증이 필요할 수 있습니다)"
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        t.thumbnail_status, t.thumbnail_error, t.thumbnail_next_at = outcome, error, None
        if outcome == "ok":
            t.thumbnail_attempts = 0
            if charge and ch.platform == "youtube":  # the upload already reserved its first thumbnail
                quota.add(s, ch.platform, get_settings().youtube_quota_thumbnail, now)
        elif retryable and t.thumbnail_attempts < len(THUMBNAIL_RETRY_MIN):
            t.thumbnail_next_at = now + timedelta(minutes=THUMBNAIL_RETRY_MIN[t.thumbnail_attempts])
            t.thumbnail_attempts += 1
    return outcome


# --- status ---


def apply_remote(t: PublishTask, st: RemoteStatus, now: datetime) -> None:
    """Move the task by what the platform reports."""
    t.checked_at = now
    t.remote_status = {
        "exists": st.exists, "upload_status": st.upload_status, "privacy": st.privacy,
        "publish_at": st.publish_at.isoformat() if st.publish_at else None,
        "failure_reason": st.failure_reason, "rejection_reason": st.rejection_reason,
    }  # fmt: skip
    if not st.exists:
        return _fail(t, "플랫폼에서 영상이 삭제됐습니다", "remote")
    if st.upload_status in ("failed", "rejected"):
        reason = st.failure_reason or st.rejection_reason or st.upload_status
        return _fail(t, f"플랫폼 처리 실패: {reason}", "remote")
    if st.publish_at is not None:
        t.status = PublishStatus.SCHEDULED
        return None
    if st.upload_status == "uploaded":  # still processing
        t.status = PublishStatus.SCHEDULED if t.publish_at else PublishStatus.PROCESSING
        return None
    if t.publish_at and st.privacy == "private" and t.privacy != "private":
        if now > _aware(t.publish_at) + PUBLISH_LATE:
            return _fail(t, "예약 시각이 지났는데도 비공개입니다. 미인증 API 프로젝트에서 올린 영상은 비공개로 "
                            "고정됩니다(YouTube API 감사 필요). YouTube 스튜디오에서 확인하세요", "remote")  # fmt: skip
        t.status = PublishStatus.SCHEDULED
        return None
    t.status = PublishStatus.PUBLISHED
    t.published_at = t.published_at or now
    return None


def _next_check(t: PublishTask, now: datetime) -> datetime | None:
    """When to ask the platform again (M8.1): 1, 2, 4, 8, then every 10 minutes while it works;
    at publish_at for a processed scheduled video; 10 min, 1 h, 6 h, 24 h after publication."""
    if t.status == PublishStatus.PUBLISHED:
        base = _aware(t.published_at) or now
        return next((base + timedelta(minutes=m) for m in AFTER_PUBLISH_MIN if base + timedelta(minutes=m) > now), None)
    if t.status not in WATCHED or not t.remote_id:
        return None
    publish_at = _aware(t.publish_at)
    processed = (t.remote_status or {}).get("upload_status") == "processed"
    if t.status == PublishStatus.SCHEDULED and processed and publish_at is not None and publish_at > now:
        return publish_at + timedelta(minutes=1)
    return now + timedelta(minutes=CHECK_STEPS_MIN[min(t.check_count, len(CHECK_STEPS_MIN) - 1)])


def schedule_next_check(t: PublishTask, now: datetime, *, changed: bool) -> None:
    t.check_count = 0 if changed else t.check_count + 1
    t.next_check_at = _next_check(t, now)


def check_status(task_id: int, now: datetime | None = None) -> str:
    now = now or utcnow()
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        if t is None or not t.remote_id or t.status in (PublishStatus.CANCELLED, PublishStatus.UPLOADING):
            return "skipped"
        ch = t.channel
        remote_id = t.remote_id
    try:
        st = publisher_for(ch).get_status(remote_id)
    except PublishError as e:
        with session_scope() as s:
            t = s.get(PublishTask, task_id)
            t.checked_at = now
            if e.kind == "auth" and ch.status == ChannelStatus.ACTIVE:
                s.get(Channel, ch.id).status = ChannelStatus.REAUTH_REQUIRED
            t.remote_status = {**(t.remote_status or {}), "check_error": str(e)}
            if e.kind == "quota":
                t.next_check_at = quota.next_reset(now) + QUOTA_GRACE
            else:
                schedule_next_check(t, now, changed=False)
        return "error"
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        quota.add(s, ch.platform, 1, now)
        before = t.status
        apply_remote(t, st, now)
        schedule_next_check(t, now, changed=before != t.status)
        job_id = _finish_change(s, t)
    if before != t.status:
        publish_job_changed(job_id)
    return str(t.status)


# --- user actions ---


def retry(session: Session, t: PublishTask) -> str:
    """FAILED → QUEUED (count starts over), or only the thumbnail again. Returns what to dispatch."""
    if t.status == PublishStatus.FAILED:
        if session.get(Channel, t.channel_id).status != ChannelStatus.ACTIVE:
            raise PublishFlowError("채널을 먼저 다시 연결하세요")
        meta = session.get(ClipMetadata, t.clip_id)
        if meta is not None:
            t.privacy, t.publish_at = meta.privacy, _aware(meta.publish_at)
        if t.publish_at is not None and _aware(t.publish_at) <= utcnow() + SCHEDULE_MARGIN and not t.remote_id:
            raise PublishFlowError("예약 시각이 지났습니다. 클립의 예약 시각을 바꾼 뒤 다시 시도하세요")
        _reset_for_retry(t)
        if t.remote_id:  # uploaded, but a check failed (e.g. still private after publish_at): check again
            t.status = PublishStatus.UPLOADED
            t.next_check_at, t.check_count = utcnow(), 0
            _finish_change(session, t)
            return "check"
        t.status = PublishStatus.QUEUED
        _finish_change(session, t)
        return "upload"
    if t.remote_id and t.sync_status == "failed":
        t.sync_status, t.sync_attempts, t.sync_next_at, t.sync_error = "pending", 0, None, None
        return "sync"
    if t.remote_id and t.thumbnail_status in ("failed", "none"):
        t.thumbnail_attempts, t.thumbnail_next_at = 0, None
        return "thumbnail"
    if t.status == PublishStatus.QUEUED:  # waiting for a retry time: now
        t.next_retry_at = None
        return "upload"
    raise PublishFlowError("다시 시도할 것이 없습니다")


def cancel(session: Session, t: PublishTask) -> None:
    """Remove the upload; a video already on the platform is deleted (spec 5.10)."""
    if t.status == PublishStatus.UPLOADING:
        raise PublishFlowError("업로드 중에는 취소할 수 없습니다. 끝난 뒤 취소하세요")
    if t.status == PublishStatus.CANCELLED:
        return
    if t.remote_id:
        ch = session.get(Channel, t.channel_id)
        try:
            publisher_for(ch).delete(t.remote_id)
        except PublishError as e:
            raise PublishFlowError(f"영상을 지우지 못했습니다: {e}") from e
        if ch.platform == "youtube":
            quota.add(session, ch.platform, get_settings().youtube_quota_delete)
    t.status, t.next_retry_at = PublishStatus.CANCELLED, None
    _finish_change(session, t)


def cancel_pending(session: Session, clip: Clip) -> None:
    """Reopening a clip: drop tasks that never reached the platform."""
    for t in current_tasks(session, clip.id):
        if t.remote_id or t.status == PublishStatus.UPLOADING:
            raise PublishFlowError("이미 업로드된(또는 업로드 중인) 영상이 있습니다. 업로드를 취소한 뒤 다시 여세요")
        t.status = PublishStatus.CANCELLED


# --- periodic sweep (Celery beat, every minute) ---


def sweep(now: datetime | None = None) -> dict[str, int]:
    """Every minute (Celery beat): uploads whose retry time has come, stalled uploads and syncs,
    status checks that are due (M8.1), thumbnail retries (M8.2) and pending edits (M8.3)."""
    now = now or utcnow()
    with session_scope() as s:
        stale = s.scalars(select(PublishTask).where(
            PublishTask.status == PublishStatus.UPLOADING, PublishTask.updated_at < now - STALE_UPLOAD))  # fmt: skip
        for t in stale:
            t.status, t.last_error = PublishStatus.QUEUED, "업로드가 멈춰 다시 시작합니다 (작업자 재시작?)"
        for t in s.scalars(select(PublishTask).where(
                PublishTask.sync_status == "syncing", PublishTask.updated_at < now - STALE_SYNC)):  # fmt: skip
            t.sync_status = "pending"
        due = list(s.scalars(select(PublishTask.id).where(
            PublishTask.status == PublishStatus.QUEUED,
            or_(PublishTask.next_retry_at <= now,
                PublishTask.next_retry_at.is_(None) & (PublishTask.updated_at < now - LOST_DISPATCH)),
        )))  # fmt: skip
        checks = list(s.scalars(select(PublishTask.id).where(
            PublishTask.status.in_(list(WATCHED)), PublishTask.remote_id.is_not(None),
            or_(PublishTask.next_check_at <= now,
                # tasks from before M8 have no next check time
                PublishTask.next_check_at.is_(None) & (PublishTask.status != PublishStatus.PUBLISHED)),
        )))  # fmt: skip
        thumbs = list(s.scalars(select(PublishTask.id).where(
            PublishTask.thumbnail_status == "failed", PublishTask.thumbnail_next_at <= now,
            PublishTask.status != PublishStatus.CANCELLED)))  # fmt: skip
        syncs = list(s.scalars(select(PublishTask.id).where(
            PublishTask.sync_status == "pending", PublishTask.remote_id.is_not(None),
            PublishTask.status.not_in([PublishStatus.UPLOADING, PublishStatus.CANCELLED]),
            or_(PublishTask.sync_next_at <= now,
                PublishTask.sync_next_at.is_(None) & (PublishTask.updated_at < now - LOST_DISPATCH)),
        )))  # fmt: skip
    dispatch(due)
    from .tasks import check, thumbnail

    for task_id in checks:
        check.apply_async(args=(task_id,), queue="publish")
    for task_id in thumbs:
        thumbnail.apply_async(args=(task_id,), queue="publish")
    dispatch_sync(syncs)
    return {"uploads": len(due), "checks": len(checks), "thumbnails": len(thumbs), "syncs": len(syncs)}


# --- edits after the upload (M8.3): pushed to the platform automatically ---

SYNC_FIELDS = {"title", "description", "tags", "hashtags", "privacy", "publish_at"}


def mark_sync(session: Session, clip: Clip, *, thumbnail: bool = False) -> list[int]:
    """The reviewer changed the metadata (or the thumbnail) of an uploaded clip: every upload of it
    gets the change. Returns task ids to sync now; an upload in progress syncs when it finishes,
    a queued one simply uploads the new metadata."""
    meta = session.get(ClipMetadata, clip.id)
    now_ids = []
    for t in current_tasks(session, clip.id):
        if t.status == PublishStatus.QUEUED and not t.remote_id and meta is not None:
            t.privacy, t.publish_at = meta.privacy, _aware(meta.publish_at)
            continue
        if not t.remote_id and t.status != PublishStatus.UPLOADING:
            continue
        if t.status == PublishStatus.FAILED and t.error_kind == "remote":
            continue  # rejected or removed on the platform: a retry uploads a new video
        t.sync_version += 1
        t.sync_status, t.sync_attempts, t.sync_next_at, t.sync_error = "pending", 0, None, None
        t.sync_thumbnail = t.sync_thumbnail or thumbnail
        if t.remote_id and t.status != PublishStatus.UPLOADING:
            now_ids.append(t.id)
    session.flush()
    return now_ids


def dispatch_sync(task_ids: list[int]) -> None:
    from .tasks import sync

    for task_id in task_ids:
        sync.apply_async(args=(task_id,), queue="publish")


def run_sync(task_id: int, now: datetime | None = None) -> str:
    """videos.update with the clip's current metadata (and thumbnails.set when it changed)."""
    from sqlalchemy import update

    now = now or utcnow()
    with session_scope() as s:
        claimed = s.execute(update(PublishTask).where(PublishTask.id == task_id, PublishTask.sync_status == "pending")
                            .values(sync_status="syncing", updated_at=now)).rowcount  # fmt: skip
    if not claimed:
        return "skipped"
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        meta = s.get(ClipMetadata, t.clip_id)
        ch = t.channel
        if not t.remote_id or t.status in (PublishStatus.UPLOADING, PublishStatus.CANCELLED) or meta is None:
            t.sync_status = "pending" if t.status == PublishStatus.UPLOADING else None
            return "skipped"
        if ch.status != ChannelStatus.ACTIVE:
            t.sync_status, t.sync_error = "failed", "채널을 다시 연결해야 YouTube에 반영할 수 있습니다"
            return "failed"
        version, thumb, remote_id = t.sync_version, t.sync_thumbnail, t.remote_id
        publish_at = _aware(meta.publish_at)
        if t.status == PublishStatus.PUBLISHED or (publish_at is not None and publish_at <= now + SCHEDULE_MARGIN):
            publish_at = None  # already public (a past publishAt cannot be sent)
        pm = publish_meta(meta, meta.privacy, publish_at)
        pm.publish_at = publish_at
        job_id = s.get(Clip, t.clip_id).job_id
    publisher = publisher_for(ch)
    try:
        publisher.update(remote_id, pm)
    except PublishError as e:
        return _sync_failed(task_id, e, now, job_id)
    s_ = get_settings()
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        if ch.platform == "youtube":
            quota.add(s, ch.platform, s_.youtube_quota_update, now)
        t.privacy = pm.privacy
        if pm.publish_at is not None or t.status != PublishStatus.PUBLISHED:
            t.publish_at = pm.publish_at  # a published video keeps the time it went public
        t.synced_at, t.synced_version, t.sync_error, t.sync_attempts, t.sync_next_at = now, version, None, 0, None
        current = t.sync_version == version
        t.sync_status = "ok" if current else "pending"
        if current:
            t.sync_thumbnail = False
        t.next_check_at, t.check_count = now + timedelta(minutes=1), 0  # see what the platform made of it
    if thumb:
        set_thumbnail(task_id, publisher, now=now)
    log.info("synced", extra={"task_id": task_id, "remote_id": remote_id, "version": version})
    if not current:
        dispatch_sync([task_id])
    publish_job_changed(job_id)
    return "synced"


def _sync_failed(task_id: int, e: PublishError, now: datetime, job_id: int) -> str:
    s_ = get_settings()
    with session_scope() as s:
        t = s.get(PublishTask, task_id)
        ch = t.channel
        t.sync_attempts += 1
        t.sync_error = str(e)
        if e.kind == "quota":
            if e.reason != "uploadLimitExceeded":
                quota.exhaust(s, ch.platform, now)
            t.sync_status, t.sync_next_at = "pending", quota.next_reset(now) + QUOTA_GRACE
            outcome = "deferred"
        elif e.retryable and t.sync_attempts < s_.publish_max_attempts:
            t.sync_status = "pending"
            t.sync_next_at = now + timedelta(minutes=BACKOFF_MINUTES[min(t.sync_attempts, len(BACKOFF_MINUTES)) - 1])
            outcome = "retry"
        else:
            if e.kind == "auth" and ch.status == ChannelStatus.ACTIVE:
                ch.status, ch.last_error = ChannelStatus.REAUTH_REQUIRED, str(e)
            t.sync_status, t.sync_next_at = "failed", None
            outcome = "failed"
    log.warning("sync failed", extra={"task_id": task_id, "kind": e.kind, "error": str(e), "outcome": outcome})
    publish_job_changed(job_id)
    return outcome
