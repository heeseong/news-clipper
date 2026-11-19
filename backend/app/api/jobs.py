from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, DbSession
from app.models import Job, JobStatus
from app.pipeline import tasks
from app.pipeline.definition import PIPELINE, STEPS
from app.pipeline.orchestrator import TransitionError, create_steps
from app.schemas.job import DummyJobIn, JobDetailOut, JobListOut, JobOut, StageOut

router = APIRouter(prefix="/api", tags=["jobs"])


def _job_out(job: Job, model: type[JobOut] = JobOut) -> JobOut:
    out = model.model_validate(job)
    out.program_name = job.program.name if job.program else None
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
