"""Clip review (spec 5.8, plan M5): range and metadata edits, thumbnails, approval, locks, audit.

Who: login is not built yet (M0.7). The browser sends the reviewer's name in the `X-Reviewer`
header and a User row is kept per name, so locks and the audit log can say who did what.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Clip, ClipMetadata, ClipStatus, Job, Thumbnail, User
from app.models.base import utcnow
from app.models.user import Role

LOCK_SECONDS = 120  # the editor refreshes its lock every 30 s while it is open
TITLE_MAX = 100
MIN_CLIP_SECONDS = 1.0
PRIVACY = ("private", "unlisted", "public")
# A clip in these states was looked at: an align re-run asks before throwing it away.
REVIEWED = (ClipStatus.APPROVED, ClipStatus.REJECTED, ClipStatus.RENDERING, ClipStatus.RENDERED)
APPROVED_STATES = (ClipStatus.APPROVED, ClipStatus.RENDERING, ClipStatus.RENDERED)
# Editable states (later states belong to rendering and publishing, M6/M7).
EDITABLE = (ClipStatus.DRAFT, ClipStatus.NOT_FOUND)


class ReviewError(Exception):
    """A request that cannot be done in the clip's current state (HTTP 409)."""


class Locked(ReviewError):
    """Another reviewer holds the clip (HTTP 423)."""


class NotApprovable(ReviewError):
    """Approval checks failed (HTTP 422); `problems` lists them."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("승인할 수 없습니다: " + " / ".join(problems))
        self.problems = problems


def reviewer(session: Session, name: str) -> User:
    name = " ".join(name.split())[:100]
    email = f"reviewer-{hashlib.sha1(name.encode()).hexdigest()[:12]}@local"
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name, role=Role.PUBLISHER)
        session.add(user)
        session.flush()
    return user


def audit(session: Session, user: User | None, clip: Clip, action: str, diff: dict[str, Any] | None = None) -> None:
    session.add(
        AuditLog(user_id=user.id if user else None, entity="clip", entity_id=clip.id, job_id=clip.job_id,
                 action=action, diff=diff or {})
    )  # fmt: skip


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


# --- locks ---


def lock_holder(clip: Clip, now: datetime | None = None) -> User | None:
    """The reviewer holding a fresh lock on the clip, if any."""
    now = now or utcnow()
    if clip.locked_by is None or clip.locked_at is None:
        return None
    if now - _aware(clip.locked_at) > timedelta(seconds=LOCK_SECONDS):
        return None
    return clip.locker


def check_lock(clip: Clip, user: User) -> None:
    holder = lock_holder(clip)
    if holder is not None and holder.id != user.id:
        raise Locked(f"{holder.name} 님이 편집 중입니다")


def acquire_lock(session: Session, clip: Clip, user: User) -> None:
    check_lock(clip, user)
    clip.locked_by, clip.locked_at = user.id, utcnow()
    session.flush()


def release_lock(session: Session, clip: Clip, user: User) -> None:
    if clip.locked_by == user.id:
        clip.locked_by, clip.locked_at = None, None
        session.flush()


# --- edits ---


def _editable(clip: Clip, user: User) -> None:
    check_lock(clip, user)
    if clip.status not in EDITABLE:
        raise ReviewError("승인·반려된 클립은 먼저 다시 열어야 고칠 수 있습니다")


def _touch(clip: Clip, user: User) -> None:
    clip.edited_at, clip.edited_by = utcnow(), user.id


@dataclass
class RangeChange:
    first_range: bool  # the clip had no range before (metadata and thumbnails must be made)
    changed: bool


def set_range(session: Session, clip: Clip, user: User, start: float, end: float) -> RangeChange:
    """The reviewer's range (spec 5-3-1, 5.8). Times are seconds from the file start."""
    _editable(clip, user)
    job = session.get(Job, clip.job_id)
    start, end = round(float(start), 3), round(float(end), 3)
    if start < 0 or (job.duration and end > job.duration + 0.001):
        raise ReviewError("구간이 녹화본 밖입니다")
    if end - start < MIN_CLIP_SECONDS:
        raise ReviewError(f"구간은 {MIN_CLIP_SECONDS:g}초 이상이어야 합니다")
    before = (clip.start_sec, clip.end_sec)
    first = clip.start_sec is None or clip.end_sec is None
    changed = before != (start, end)
    if changed:
        clip.start_sec, clip.end_sec = start, end
        clip.start_snap = clip.start_snap if before[0] == start else "manual"
        clip.end_snap = clip.end_snap if before[1] == end else "manual"
        if clip.status == ClipStatus.NOT_FOUND:
            clip.status = ClipStatus.DRAFT
        _touch(clip, user)
        audit(session, user, clip, "range", {"start_sec": [before[0], start], "end_sec": [before[1], end]})
    session.flush()
    return RangeChange(first, changed)


METADATA_FIELDS = ("title", "description", "tags", "hashtags", "thumbnail_text", "privacy", "publish_at", "channel_ids")


PUBLISH_FIELDS = {"privacy", "publish_at", "channel_ids"}  # do not change the render


def update_metadata(session: Session, clip: Clip, user: User, changes: dict[str, Any]) -> ClipMetadata:
    if set(changes) <= PUBLISH_FIELDS and clip.status in APPROVED_STATES:
        check_lock(clip, user)  # publishing settings can still change after approval
    else:
        _editable(clip, user)
    meta = session.get(ClipMetadata, clip.id)
    if meta is None:
        raise ReviewError("메타데이터가 아직 없습니다 (구간을 정하면 만들어집니다)")
    if "privacy" in changes and changes["privacy"] not in PRIVACY:
        raise ReviewError(f"공개 설정은 {', '.join(PRIVACY)} 중 하나입니다")
    if "title" in changes:
        changes["title"] = " ".join(str(changes["title"]).split())
    if "tags" in changes:
        changes["tags"] = list(dict.fromkeys(t.strip() for t in changes["tags"] if t.strip()))
    if "hashtags" in changes:
        changes["hashtags"] = list(
            dict.fromkeys("#" + h.strip().lstrip("#").replace(" ", "") for h in changes["hashtags"] if h.strip("# "))
        )
    if changes.get("publish_at") is not None:
        changes["publish_at"] = _aware(changes["publish_at"])
    diff = {}
    for key, value in changes.items():
        if key not in METADATA_FIELDS:
            continue
        old = getattr(meta, key)
        if old != value:
            diff[key] = [_jsonable(old), _jsonable(value)]
            setattr(meta, key, value)
    if diff:
        _touch(clip, user)
        audit(session, user, clip, "metadata", diff)
    session.flush()
    return meta


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def select_thumbnail(session: Session, clip: Clip, user: User, thumbnail_id: int) -> Thumbnail:
    _editable(clip, user)
    thumbs = list(session.scalars(select(Thumbnail).where(Thumbnail.clip_id == clip.id)))
    chosen = next((t for t in thumbs if t.id == thumbnail_id), None)
    if chosen is None:
        raise ReviewError("이 클립의 썸네일이 아닙니다")
    before = next((t.id for t in thumbs if t.selected), None)
    for t in thumbs:
        t.selected = t.id == thumbnail_id
    if before != thumbnail_id:
        _touch(clip, user)
        audit(session, user, clip, "thumbnail", {"selected": [before, thumbnail_id]})
    session.flush()
    return chosen


def add_thumbnail(
    session: Session, clip: Clip, user: User, path: str, source: str, frame_sec: float | None
) -> Thumbnail:
    """A frame the reviewer picked or an uploaded image; it becomes the selected one."""
    _editable(clip, user)
    for t in session.scalars(select(Thumbnail).where(Thumbnail.clip_id == clip.id)):
        t.selected = False
    thumb = Thumbnail(clip_id=clip.id, source=source, rank=0, frame_sec=frame_sec, path=path, selected=True)
    session.add(thumb)
    session.flush()
    _touch(clip, user)
    audit(session, user, clip, "thumbnail_add", {"source": source, "frame_sec": frame_sec, "id": thumb.id})
    session.flush()
    return thumb


def acknowledge(session: Session, clip: Clip, user: User, value: bool = True) -> None:
    _editable(clip, user)
    if clip.warnings_acknowledged != value:
        clip.warnings_acknowledged = value
        audit(session, user, clip, "acknowledge", {"warnings_acknowledged": [not value, value]})
    session.flush()


# --- approval ---


def warnings_of(clip: Clip, meta: ClipMetadata | None) -> list[str]:
    return list(clip.flags or []) + (list(meta.warnings or []) if meta else [])


def approval_problems(session: Session, clip: Clip, now: datetime | None = None) -> list[str]:
    """Spec 5.8: a title within the limit, a selected thumbnail, warnings looked at."""
    now = now or utcnow()
    meta = session.get(ClipMetadata, clip.id)
    problems = []
    if clip.needs_manual_range:
        problems.append("구간이 정해지지 않았습니다")
    if meta is None:
        problems.append("메타데이터가 없습니다")
    else:
        if not meta.title.strip():
            problems.append("제목이 없습니다")
        elif len(meta.title) > TITLE_MAX:
            problems.append(f"제목이 {TITLE_MAX}자를 넘습니다 ({len(meta.title)}자)")
        if meta.publish_at is not None and _aware(meta.publish_at) <= now:
            problems.append("예약 시각이 지났습니다")
    selected = session.scalar(select(Thumbnail).where(Thumbnail.clip_id == clip.id, Thumbnail.selected))
    if selected is None:
        problems.append("썸네일이 선택되지 않았습니다")
    if warnings_of(clip, meta) and not clip.warnings_acknowledged:
        problems.append("경고를 확인하지 않았습니다")
    return problems


def approve(session: Session, clip: Clip, user: User) -> None:
    """Approve; the caller then queues the final render (render_requested)."""
    check_lock(clip, user)
    if clip.status in APPROVED_STATES:
        return
    if clip.status not in EDITABLE:
        raise ReviewError(f"이 상태({clip.status})에서는 승인할 수 없습니다")
    problems = approval_problems(session, clip)
    if problems:
        raise NotApprovable(problems)
    before = clip.status
    clip.status, clip.reviewed_at, clip.reviewed_by = ClipStatus.APPROVED, utcnow(), user.id
    clip.reject_reason = None
    audit(session, user, clip, "approve", {"status": [str(before), "APPROVED"]})
    session.flush()


def request_render(session: Session, clip: Clip, user: User | None = None) -> None:
    """Render (again) an approved clip: after approval, after a failed render, or on request."""
    if clip.status not in APPROVED_STATES:
        raise ReviewError("승인된 클립만 렌더할 수 있습니다")
    if clip.status == ClipStatus.RENDERING and not clip.render_error:
        raise ReviewError("렌더 중입니다")
    if user is not None and clip.status == ClipStatus.RENDERED:
        audit(session, user, clip, "render", {"status": ["RENDERED", "APPROVED"]})
    clip.status, clip.render_error = ClipStatus.APPROVED, None
    session.flush()


def discard_render(clip: Clip) -> None:
    """A reopened clip's render is out of date: remove it."""
    from app.storage import JobFiles

    JobFiles(clip.job_id).final(clip.id).unlink(missing_ok=True)
    clip.render_path = clip.rendered_at = clip.render_error = None


def reject(session: Session, clip: Clip, user: User, reason: str | None) -> None:
    check_lock(clip, user)
    if clip.status not in EDITABLE:
        raise ReviewError(f"이 상태({clip.status})에서는 반려할 수 없습니다")
    before = clip.status
    clip.status, clip.reviewed_at, clip.reviewed_by = ClipStatus.REJECTED, utcnow(), user.id
    clip.reject_reason = (reason or "").strip()[:255] or None
    audit(session, user, clip, "reject", {"status": [str(before), "REJECTED"], "reason": clip.reject_reason})
    session.flush()


def reopen(session: Session, clip: Clip, user: User) -> None:
    """Back to editing (before rendering starts)."""
    check_lock(clip, user)
    if clip.status not in REVIEWED:
        raise ReviewError("승인·반려·렌더된 클립만 다시 열 수 있습니다")
    before = clip.status
    clip.status = ClipStatus.DRAFT if not clip.needs_manual_range else ClipStatus.NOT_FOUND
    clip.reviewed_at = clip.reviewed_by = None
    discard_render(clip)
    audit(session, user, clip, "reopen", {"status": [str(before), str(clip.status)]})
    session.flush()


def schedule_times(first: datetime, interval_minutes: int, count: int) -> list[datetime]:
    first = _aware(first)
    return [first + timedelta(minutes=interval_minutes * i) for i in range(count)]


# --- protection against re-runs ---


def reviewed_clips(session: Session, job_id: int) -> list[Clip]:
    """Clips a reviewer has touched: an align re-run would replace them."""
    return list(
        session.scalars(
            select(Clip).where(
                Clip.job_id == job_id, or_(Clip.edited_at.is_not(None), Clip.status.in_(list(REVIEWED)))
            )
        )
    )
