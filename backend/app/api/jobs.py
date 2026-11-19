from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, DbSession
from app.core.events import publish_job_changed
from app.intake import IntakeError, create_job_from_upload
from app.models import Job, JobStatus
from app.pipeline import tasks
from app.pipeline.definition import PIPELINE, STEPS
from app.pipeline.orchestrator import TransitionError, create_steps
from app.schemas.job import (
    AnalysisOut,
    ArtifactsOut,
    DummyJobIn,
    JobCreateIn,
    JobDetailOut,
    JobListOut,
    JobOut,
    StageOut,
)
from app.storage import JobFiles

router = APIRouter(prefix="/api", tags=["jobs"])


def _job_out(job: Job, model: type[JobOut] = JobOut) -> JobOut:
    out = model.model_validate(job)
    out.program_name = job.program.name if job.program else None
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
    return JobListOut(items=[_job_out(j) for j in jobs], total=total)


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int, db: DbSession) -> JobOut:
    return _job_out(_get_job(db, job_id), JobDetailOut)


@router.post("/jobs", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
def create_job(body: JobCreateIn, db: DbSession) -> JobOut:
    """Create a job from a finished tus upload and start the pipeline."""
    try:
        job = create_job_from_upload(db, body.upload_id, body.program_id, body.broadcast_date)
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
        out.scenes = data["cuts"]
        out.scene_threshold = data.get("threshold")
    if files.silences.exists():
        out.silences = json.loads(files.silences.read_text())["silences"]
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


@router.post("/jobs/{job_id}/steps/{step}/retry", response_model=JobDetailOut)
def retry_step(job_id: int, step: str, db: DbSession) -> JobOut:
    _get_job(db, job_id)
    try:
        tasks.retry_step(job_id, step)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except TransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.expire_all()
    return _job_out(_get_job(db, job_id), JobDetailOut)
