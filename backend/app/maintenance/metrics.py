"""Operating metrics (spec 9, 11; plan M9.3), computed from what the pipeline already records.

Per broadcast (job): step times, how many clips the alignment placed by itself, how many of those
a reviewer moved, how many needed a manual range, titles edited, LLM tokens, uploads.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.align.service import REJECTING
from app.models import (
    AuditLog,
    Clip,
    ClipMetadata,
    ClipStatus,
    Job,
    JobStep,
    PublishStatus,
    PublishTask,
    StepStatus,
)
from app.models.base import utcnow

REVIEWED = (ClipStatus.APPROVED, ClipStatus.RENDERING, ClipStatus.RENDERED, ClipStatus.UPLOADING, ClipStatus.UPLOADED,
            ClipStatus.PROCESSING, ClipStatus.SCHEDULED, ClipStatus.PUBLISHED, ClipStatus.UPLOAD_FAILED)  # fmt: skip


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


@dataclass
class JobMetrics:
    job_id: int
    program: str | None
    broadcast_date: str | None
    duration: float | None  # recording, seconds
    pipeline_seconds: float | None  # first step start → last step end (until review is ready)
    steps: dict[str, float] = field(default_factory=dict)  # seconds per step (last successful run)
    retried_steps: int = 0
    clips: int = 0  # upload targets
    auto: int = 0  # placed by the alignment
    auto_moved: int = 0  # ... and a reviewer moved a boundary
    manual: int = 0  # range set by a reviewer (the alignment could not place it)
    no_range: int = 0
    approved: int = 0
    rejected: int = 0
    titles_edited: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    uploads: int = 0
    published: int = 0
    on_platform: int = 0  # uploaded and waiting to go public (scheduled, processing)
    upload_failed: int = 0
    upload_retries: int = 0


def _job_metrics(session: Session, job: Job) -> JobMetrics:
    m = JobMetrics(job.id, job.program.name if job.program else None,
                   job.broadcast_date.isoformat() if job.broadcast_date else None, job.duration, None)  # fmt: skip
    steps = list(session.scalars(select(JobStep).where(JobStep.job_id == job.id)))
    starts = [_aware(s.started_at) for s in steps if s.started_at]
    ends = [_aware(s.finished_at) for s in steps if s.finished_at]
    if starts and ends:
        m.pipeline_seconds = round((max(ends) - min(starts)).total_seconds(), 1)
    for s in steps:
        if s.status == StepStatus.SUCCEEDED and s.started_at and s.finished_at:
            m.steps[s.step] = round((_aware(s.finished_at) - _aware(s.started_at)).total_seconds(), 1)
        m.retried_steps += 1 if (s.attempts or 0) > 1 else 0
    clips = list(session.scalars(select(Clip).where(Clip.job_id == job.id)))
    m.clips = len(clips)
    titles = {log.entity_id for log in session.scalars(
        select(AuditLog).where(AuditLog.job_id == job.id, AuditLog.entity == "clip", AuditLog.action == "metadata"))
        if "title" in (log.diff or {})}  # fmt: skip
    m.titles_edited = len(titles)
    for c in clips:
        rejected_by_align = any(f in (c.flags or []) for f in REJECTING)
        if c.start_sec is None or c.end_sec is None:
            m.no_range += 1
        elif rejected_by_align:
            m.manual += 1
        else:
            m.auto += 1
            if c.start_snap == "manual" or c.end_snap == "manual":
                m.auto_moved += 1
        if c.status in REVIEWED:
            m.approved += 1
        elif c.status == ClipStatus.REJECTED:
            m.rejected += 1
        meta = session.get(ClipMetadata, c.id)
        if meta is not None:
            m.llm_input_tokens += meta.llm_input_tokens or 0
            m.llm_output_tokens += meta.llm_output_tokens or 0
    clip_ids = [c.id for c in clips]
    if clip_ids:
        for t in session.scalars(select(PublishTask).where(PublishTask.clip_id.in_(clip_ids),
                                                           PublishTask.status != PublishStatus.CANCELLED)):  # fmt: skip
            m.uploads += 1
            m.published += t.status == PublishStatus.PUBLISHED
            m.on_platform += t.status in (PublishStatus.UPLOADED, PublishStatus.PROCESSING, PublishStatus.SCHEDULED)
            m.upload_failed += t.status == PublishStatus.FAILED
            m.upload_retries += max(0, (t.attempts or 0) - 1)
    return m


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def summary(session: Session, days: int = 30, now: datetime | None = None) -> dict:
    now = now or utcnow()
    since = now - timedelta(days=days)
    jobs = [j for j in session.scalars(select(Job).order_by(Job.id.desc()))
            if _aware(j.created_at) >= since and not j.is_dummy]  # fmt: skip
    rows = [_job_metrics(session, j) for j in jobs]
    by_step: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for k, v in r.steps.items():
            by_step[k].append(v)

    def total(attr: str) -> int:
        return sum(getattr(r, attr) for r in rows)

    totals = {
        "jobs": len(rows),
        "clips": total("clips"),
        "auto": total("auto"),
        "auto_moved": total("auto_moved"),
        "manual": total("manual"),
        "no_range": total("no_range"),
        "approved": total("approved"),
        "rejected": total("rejected"),
        "titles_edited": total("titles_edited"),
        "llm_input_tokens": total("llm_input_tokens"),
        "llm_output_tokens": total("llm_output_tokens"),
        "uploads": total("uploads"),
        "published": total("published"),
        "on_platform": total("on_platform"),
        "upload_failed": total("upload_failed"),
        "upload_retries": total("upload_retries"),
        # Rates the reviewers care about (spec 11)
        "detection_rate": _rate(total("auto"), total("clips")),  # placed by the alignment
        "boundary_edit_rate": _rate(total("auto_moved"), total("auto")),  # ... then moved by a reviewer
        "title_edit_rate": _rate(total("titles_edited"), total("approved")),
        # Uploads that reached the platform, of those that finished one way or the other
        "upload_success_rate": _rate(total("published") + total("on_platform"),
                                     total("published") + total("on_platform") + total("upload_failed")),
        "median_pipeline_seconds": round(median([r.pipeline_seconds for r in rows if r.pipeline_seconds]), 1)
        if any(r.pipeline_seconds for r in rows) else None,
        "median_step_seconds": {k: round(median(v), 1) for k, v in sorted(by_step.items())},
    }  # fmt: skip
    return {"days": days, "since": since.isoformat(), "totals": totals, "jobs": [r.__dict__ for r in rows]}
