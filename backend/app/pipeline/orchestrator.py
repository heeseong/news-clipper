"""Job/step state transitions, kept free of Celery so they are easy to test and reason about.

`tasks.py` calls these and does the dispatching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job, JobStatus, JobStep, StepStatus
from app.models.base import utcnow

from .definition import FINAL_STATUS, PIPELINE, STEPS
from .steps import StepContext

log = logging.getLogger(__name__)

ACTIVE = (StepStatus.QUEUED, StepStatus.RUNNING, StepStatus.RETRYING)
DONE = (StepStatus.SUCCEEDED, StepStatus.SKIPPED)


class TransitionError(Exception):
    """The requested transition is not allowed in the current state (HTTP 409)."""


@dataclass(frozen=True)
class QueuedStep:
    step: str
    queue: str


def create_steps(job: Job) -> None:
    job.steps = [
        JobStep(stage=i, step=step.name, queue=step.queue, status=StepStatus.PENDING)
        for i, stage in enumerate(PIPELINE)
        for step in stage.steps
    ]


def _job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(f"job {job_id} not found")
    return job


def _step(session: Session, job_id: int, step: str) -> JobStep:
    row = session.scalar(select(JobStep).where(JobStep.job_id == job_id, JobStep.step == step))
    if row is None:
        raise LookupError(f"job {job_id} has no step '{step}'")
    return row


def begin_stage(session: Session, job_id: int, stage_idx: int) -> list[QueuedStep]:
    """Move the job into a stage and queue its unfinished steps. Past the last stage: finish."""
    job = _job(session, job_id)
    if stage_idx >= len(PIPELINE):
        job.status = FINAL_STATUS
        job.current_step = None
        log.info("job finished pipeline", extra={"job_id": job_id, "status": str(FINAL_STATUS)})
        return []
    stage = PIPELINE[stage_idx]
    job.status = stage.status
    job.current_step = stage.name
    job.error = None
    queued = []
    for row in job.steps:
        if row.stage == stage_idx and row.status in (
            StepStatus.PENDING,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
        ):
            row.status = StepStatus.QUEUED
            row.error = None
            queued.append(QueuedStep(row.step, row.queue))
    log.info(
        "stage started", extra={"job_id": job_id, "stage": stage.name, "steps": [q.step for q in queued]}
    )
    return queued


def mark_running(session: Session, job_id: int, step: str) -> StepContext | None:
    """Returns None if the step already succeeded (duplicate delivery): nothing to do."""
    row = _step(session, job_id, step)
    if row.status == StepStatus.SUCCEEDED:
        return None
    row.status = StepStatus.RUNNING
    row.progress = 0.0
    row.attempts += 1
    row.started_at = utcnow()
    row.finished_at = None
    row.error = None
    job = row.job
    return StepContext(
        job_id=job_id,
        step=step,
        attempt=row.attempts,
        job_dir=get_settings().storage_root / "jobs" / str(job_id),
        is_dummy=job.is_dummy,
        params=dict(job.params or {}),
    )


def mark_succeeded(session: Session, job_id: int, step: str) -> None:
    row = _step(session, job_id, step)
    row.status = StepStatus.SUCCEEDED
    row.finished_at = utcnow()
    row.progress = None
    row.error = None


def mark_skipped(session: Session, job_id: int, step: str, reason: str) -> None:
    row = _step(session, job_id, step)
    row.status = StepStatus.SKIPPED
    row.finished_at = utcnow()
    row.progress = None
    row.error = reason


def set_progress(session: Session, job_id: int, step: str, fraction: float) -> None:
    row = _step(session, job_id, step)
    if row.status == StepStatus.RUNNING:
        row.progress = round(fraction, 3)
        row.job.updated_at = utcnow()


def mark_retrying(session: Session, job_id: int, step: str, error: str) -> None:
    row = _step(session, job_id, step)
    row.status = StepStatus.RETRYING
    row.finished_at = utcnow()
    row.error = error


def mark_failed(session: Session, job_id: int, step: str, error: str) -> None:
    row = _step(session, job_id, step)
    row.status = StepStatus.FAILED
    row.finished_at = utcnow()
    row.progress = None
    row.error = error
    job = row.job
    job.status = JobStatus.FAILED
    job.current_step = step
    job.error = f"{step}: {error}"


def stage_outcome(
    session: Session, job_id: int, stage_idx: int
) -> Literal["succeeded", "failed", "incomplete"]:
    rows = [r for r in _job(session, job_id).steps if r.stage == stage_idx]
    if all(r.status in DONE for r in rows):
        return "succeeded"
    if any(r.status in ACTIVE or r.status == StepStatus.PENDING for r in rows):
        return "incomplete"
    return "failed"


def prepare_retry(session: Session, job_id: int, step: str) -> int:
    """Reset a step and everything after it; returns the stage to run again.

    Outputs of earlier steps are kept (spec 10). Steps of the same stage that already
    succeeded are not run again.
    """
    job = _job(session, job_id)
    if step not in STEPS:
        raise LookupError(f"unknown step '{step}'")
    if any(r.status in ACTIVE for r in job.steps):
        raise TransitionError("다른 단계가 아직 실행 중입니다")
    stage_idx, _ = STEPS[step]
    target = _step(session, job_id, step)
    if target.status not in (StepStatus.FAILED, *DONE):
        raise TransitionError(f"'{step}' 단계는 아직 실행되지 않았습니다")
    if any(r.stage < stage_idx and r.status not in DONE for r in job.steps):
        raise TransitionError("앞 단계가 끝나지 않았습니다")
    for row in job.steps:
        if row.step == step or row.stage > stage_idx:
            row.status = StepStatus.PENDING
            row.error = None
            row.started_at = None
            row.finished_at = None
            row.progress = None
    log.info("step retry requested", extra={"job_id": job_id, "step": step})
    return stage_idx
