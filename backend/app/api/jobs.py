from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, object_session

from app.api.deps import AppSettings, DbSession
from app.core.events import publish_job_changed
from app.intake import IntakeError, create_job_from_upload
from app.models import Clip, ClipStatus, Job, JobStatus, Rundown
from app.newsroom import service as newsroom_service
from app.pipeline import tasks
from app.pipeline.definition import PIPELINE, STEPS
from app.pipeline.orchestrator import TransitionError, create_steps
from app.review import service as review_service
from app.schemas.job import (
    AnalysisOut,
    ArtifactsOut,
    ClipCounts,
    DummyJobIn,
    JobCreateIn,
    JobDetailOut,
    JobListOut,
    JobOut,
    SceneCut,
    SilenceRange,
    StageOut,
)
from app.schemas.newsroom import RundownCounts, RundownOut, RundownRefreshIn
from app.storage import JobFiles

router = APIRouter(prefix="/api", tags=["jobs"])


# Approved clips on their way to (or on) a channel; UPLOAD_FAILED counts as approved, not uploaded.
UPLOADED = [ClipStatus.UPLOADING, ClipStatus.UPLOADED, ClipStatus.PROCESSING, ClipStatus.SCHEDULED, ClipStatus.PUBLISHED]
APPROVED_ALSO = [ClipStatus.UPLOAD_FAILED]


def clip_counts(db: Session, job_ids: list[int]) -> dict[int, ClipCounts]:
    rows = db.execute(
        select(
            Clip.job_id,
            func.count(),
            func.count(Clip.start_sec),
            func.sum(case((Clip.status.in_([ClipStatus.APPROVED, ClipStatus.RENDERING, ClipStatus.RENDERED, *UPLOADED, *APPROVED_ALSO]), 1), else_=0)),
            func.sum(case((Clip.status == ClipStatus.REJECTED, 1), else_=0)),
            func.sum(case((Clip.status.in_(UPLOADED), 1), else_=0)),
            func.sum(case((Clip.status == ClipStatus.UPLOAD_FAILED, 1), else_=0)),
        )
        .where(Clip.job_id.in_(job_ids))
        .group_by(Clip.job_id)
    ).all()
    return {
        job_id: ClipCounts(total=total, ranged=ranged, approved=approved or 0, rejected=rejected or 0,
                           uploaded=uploaded or 0, upload_failed=failed or 0)
        for job_id, total, ranged, approved, rejected, uploaded, failed in rows
    }


def _job_out(job: Job, model: type[JobOut] = JobOut, counts: ClipCounts | None = None) -> JobOut:
    out = model.model_validate(job)
    out.program_name = job.program.name if job.program else None
    if counts is None:
        session = object_session(job)
        counts = clip_counts(session, [job.id]).get(job.id) if session else None
    out.clip_counts = counts or ClipCounts()
    if isinstance(out, JobDetailOut):
        files = JobFiles(job.id)
        out.artifacts = ArtifactsOut(
            proxy=files.proxy.exists(),
            sprite=files.sprite.exists() and files.sprite_info.exists(),
            waveform=files.waveform.exists(),
            scenes=files.scenes.exists(),
            silences=files.silences.exists(),
        )
    return out


def _get_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    return job


@router.get("/pipeline", response_model=list[StageOut])
def get_pipeline() -> list[StageOut]:
    """Stage layout, for grouping steps in the UI."""
    return [StageOut(name=s.name, status=s.status, steps=[st.name for st in s.steps]) for s in PIPELINE]


@router.get("/jobs", response_model=JobListOut)
def list_jobs(
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JobListOut:
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(select(Job).order_by(Job.id.desc()).limit(limit).offset(offset)).unique().all()
    counts = clip_counts(db, [j.id for j in jobs])
    return JobListOut(items=[_job_out(j, counts=counts.get(j.id, ClipCounts())) for j in jobs], total=total)


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int, db: DbSession) -> JobOut:
    return _job_out(_get_job(db, job_id), JobDetailOut)


@router.post("/jobs", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
def create_job(body: JobCreateIn, db: DbSession) -> JobOut:
    """Create a job from a finished tus upload and start the pipeline."""
    try:
        job = create_job_from_upload(
            db, body.upload_id, body.program_id, body.broadcast_date, cue_sheet_id=body.cue_sheet_id
        )
    except IntakeError as e:
        db.rollback()
        raise HTTPException(e.status, str(e)) from e
    db.commit()
    tasks.start_job(job.id)
    db.expire_all()
    return _job_out(_get_job(db, job.id), JobDetailOut)


@router.get("/jobs/{job_id}/analysis", response_model=AnalysisOut)
def get_analysis(job_id: int, db: DbSession) -> AnalysisOut:
    """Scene cuts and silences found by the analyze step (empty fields until it has run)."""
    _get_job(db, job_id)
    files = JobFiles(job_id)
    out = AnalysisOut()
    if files.scenes.exists():
        data = json.loads(files.scenes.read_text())
        out.scenes = [SceneCut.model_validate(c) for c in data["cuts"]]
        out.scene_threshold = data.get("threshold")
    if files.silences.exists():
        silences = json.loads(files.silences.read_text())["silences"]
        out.silences = [SilenceRange.model_validate(r) for r in silences]
    return out


@router.post("/jobs/dummy", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
def create_dummy_job(
    body: DummyJobIn,
    db: DbSession,
    settings: AppSettings,
) -> JobOut:
    """Development only: a job whose steps just sleep, to exercise the orchestrator end to end."""
    if not settings.enable_dev_endpoints:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    unknown = sorted(set(body.fail_steps + body.flaky_steps) - STEPS.keys())
    if unknown:
        raise HTTPException(422, f"알 수 없는 단계: {', '.join(unknown)}")
    seconds = settings.dummy_step_seconds if body.seconds is None else body.seconds
    job = Job(
        status=JobStatus.UPLOADED,
        params={
            "dummy": {"seconds": seconds, "fail_steps": body.fail_steps, "flaky_steps": body.flaky_steps}
        },
    )
    create_steps(job)
    db.add(job)
    db.commit()
    publish_job_changed(job.id)
    tasks.start_job(job.id)
    db.expire_all()
    return _job_out(_get_job(db, job.id), JobDetailOut)


def _guard_reviewed(db: Session, job_id: int, force: bool) -> None:
    """Re-running a step remakes the clips (align) or their metadata and thumbnails: ask first
    when a reviewer has already worked on some (M5)."""
    if force:
        return
    reviewed = review_service.reviewed_clips(db, job_id)
    if reviewed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": f"검수한 클립 {len(reviewed)}개의 수정·승인 내용이 사라집니다. 그래도 진행하려면 force=true",
                "reviewed_clips": [c.id for c in reviewed],
            },
        )


@router.post("/jobs/{job_id}/steps/{step}/retry", response_model=JobDetailOut)
def retry_step(job_id: int, step: str, db: DbSession, force: bool = False) -> JobOut:
    _get_job(db, job_id)
    _guard_reviewed(db, job_id, force)
    try:
        tasks.retry_step(job_id, step)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except TransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.expire_all()
    return _job_out(_get_job(db, job_id), JobDetailOut)


@router.get("/jobs/{job_id}/rundown", response_model=RundownOut)
def get_rundown(job_id: int, db: DbSession) -> RundownOut:
    """The job's cue sheet with every item and its parsed script (404 until the newsroom step ran)."""
    _get_job(db, job_id)
    rundown = db.scalar(select(Rundown).where(Rundown.job_id == job_id))
    if rundown is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "아직 큐시트를 가져오지 않았습니다")
    out = RundownOut.model_validate(rundown)
    planned = [i.planned_duration for i in rundown.items if i.planned_duration]
    out.counts = RundownCounts(
        **newsroom_service.count_items(
            [i.type for i in rundown.items],
            [i.upload for i in rundown.items],
            [not i.upload and bool(i.dedupe_note) for i in rundown.items],
        ),
        planned_total_seconds=sum(planned) if planned else None,
    )
    return out


@router.post("/jobs/{job_id}/rundown/refresh", response_model=JobDetailOut)
def refresh_rundown(job_id: int, body: RundownRefreshIn, db: DbSession, force: bool = False) -> JobOut:
    """Look the cue sheet up again (optionally a specific one) and redo the steps after it.

    Used when the job waits for its cue sheet (WAITING_RUNDOWN) or the cue sheet was edited.
    """
    _get_job(db, job_id)
    _guard_reviewed(db, job_id, force)
    params = {"cue_sheet_id": body.cue_sheet_id} if body.cue_sheet_id is not None else None
    try:
        tasks.retry_step(job_id, "newsroom", params)
    except TransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.expire_all()
    return _job_out(_get_job(db, job_id), JobDetailOut)
