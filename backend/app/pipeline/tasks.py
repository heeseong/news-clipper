"""Celery tasks that drive the pipeline.

    dispatch_stage(n): chord( run_step(s) for s in stage n, each on its own queue )
                       → advance(n) → dispatch_stage(n + 1) if every step succeeded

`run_step` records failures on the JobStep row and returns normally, so the chord callback
always runs and decides what happens next. Transient errors are retried by Celery with
exponential backoff before the step is marked FAILED.
"""

from __future__ import annotations

import logging

from celery import chord

from app.core.db import session_scope
from app.worker import celery_app

from . import orchestrator as orch
from .definition import CONTROL_QUEUE, STEPS
from .steps import RetryableStepError, resolve_handler

log = logging.getLogger(__name__)


def backoff_seconds(retries: int) -> int:
    return min(300, 10 * 2**retries)


def dispatch_stage(job_id: int, stage_idx: int) -> None:
    with session_scope() as session:
        queued = orch.begin_stage(session, job_id, stage_idx)
    if not queued:
        return
    header = [run_step.si(job_id, q.step).set(queue=q.queue) for q in queued]
    chord(header)(advance.si(job_id, stage_idx).set(queue=CONTROL_QUEUE))


def start_job(job_id: int) -> None:
    dispatch_stage(job_id, 0)


def retry_step(job_id: int, step: str) -> None:
    """Manual retry from a step (API). Raises TransitionError/LookupError."""
    with session_scope() as session:
        stage_idx = orch.prepare_retry(session, job_id, step)
    dispatch_stage(job_id, stage_idx)


@celery_app.task(name="pipeline.run_step", bind=True, max_retries=None)
def run_step(self, job_id: int, step: str) -> str:
    with session_scope() as session:
        ctx = orch.mark_running(session, job_id, step)
    if ctx is None:
        return "skipped"
    extra = {"job_id": job_id, "step": step, "attempt": ctx.attempt}
    log.info("step started", extra=extra)
    try:
        resolve_handler(step, ctx.is_dummy)(ctx)
    except RetryableStepError as e:
        _, step_def = STEPS[step]
        if self.request.retries < step_def.max_retries:
            countdown = backoff_seconds(self.request.retries)
            log.warning("step will be retried", extra={**extra, "error": str(e), "countdown": countdown})
            with session_scope() as session:
                orch.mark_retrying(session, job_id, step, str(e))
            raise self.retry(exc=e, countdown=countdown) from e
        return _fail(job_id, step, str(e), extra)
    except Exception as e:  # noqa: BLE001 - any handler error fails the step, not the worker
        log.exception("step failed", extra=extra)
        return _fail(job_id, step, str(e) or type(e).__name__, extra)
    with session_scope() as session:
        orch.mark_succeeded(session, job_id, step)
    log.info("step succeeded", extra=extra)
    return "succeeded"


def _fail(job_id: int, step: str, error: str, extra: dict) -> str:
    with session_scope() as session:
        orch.mark_failed(session, job_id, step, error)
    log.error("step failed", extra={**extra, "error": error})
    return "failed"


@celery_app.task(name="pipeline.advance")
def advance(job_id: int, stage_idx: int) -> str:
    with session_scope() as session:
        outcome = orch.stage_outcome(session, job_id, stage_idx)
    if outcome == "succeeded":
        dispatch_stage(job_id, stage_idx + 1)
    return outcome
