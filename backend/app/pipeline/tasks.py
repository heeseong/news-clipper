"""Celery tasks that drive the pipeline.

    dispatch_stage(n): chord( run_step(s) for s in stage n, each on its own queue )
                       → advance(n) → dispatch_stage(n + 1) if every step succeeded

`run_step` records failures on the JobStep row and returns normally, so the chord callback
always runs and decides what happens next. Transient errors are retried by Celery with
exponential backoff before the step is marked FAILED.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from celery import chord

from app.core.db import session_scope
from app.core.events import publish_job_changed
from app.worker import celery_app

from . import handlers  # noqa: F401  (registers step handlers)
from . import orchestrator as orch
from .definition import CONTROL_QUEUE, STEPS
from .steps import RetryableStepError, StepSkipped, resolve_handler

log = logging.getLogger(__name__)


def backoff_seconds(retries: int) -> int:
    return min(300, 10 * 2**retries)


def dispatch_stage(job_id: int, stage_idx: int) -> None:
    with session_scope() as session:
        queued = orch.begin_stage(session, job_id, stage_idx)
    publish_job_changed(job_id)
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
    publish_job_changed(job_id)
    ctx.report = _progress_reporter(job_id, step)
    extra = {"job_id": job_id, "step": step, "attempt": ctx.attempt}
    log.info("step started", extra=extra)
    started = time.monotonic()
    try:
        resolve_handler(step, ctx.is_dummy)(ctx)
    except StepSkipped as e:
        with session_scope() as session:
            orch.mark_skipped(session, job_id, step, str(e))
        publish_job_changed(job_id)
        log.info("step skipped", extra={**extra, "reason": str(e)})
        return "skipped"
    except RetryableStepError as e:
        _, step_def = STEPS[step]
        if self.request.retries < step_def.max_retries:
            countdown = backoff_seconds(self.request.retries)
            log.warning("step will be retried", extra={**extra, "error": str(e), "countdown": countdown})
            with session_scope() as session:
                orch.mark_retrying(session, job_id, step, str(e))
            publish_job_changed(job_id)
            raise self.retry(exc=e, countdown=countdown) from e
        return _fail(job_id, step, str(e), extra)
    except Exception as e:  # noqa: BLE001 - any handler error fails the step, not the worker
        log.exception("step failed", extra=extra)
        return _fail(job_id, step, str(e) or type(e).__name__, extra)
    with session_scope() as session:
        orch.mark_succeeded(session, job_id, step)
    publish_job_changed(job_id)
    log.info("step succeeded", extra={**extra, "seconds": round(time.monotonic() - started, 1)})
    return "succeeded"


def _progress_reporter(job_id: int, step: str, interval: float = 1.0) -> Callable[[float], None]:
    """Write progress at most once per `interval` seconds (and always 100%)."""
    last = 0.0

    def report(fraction: float) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last < interval and fraction < 1.0:
            return
        last = now
        with session_scope() as session:
            orch.set_progress(session, job_id, step, fraction)
        publish_job_changed(job_id)

    return report


def _fail(job_id: int, step: str, error: str, extra: dict) -> str:
    with session_scope() as session:
        orch.mark_failed(session, job_id, step, error)
    publish_job_changed(job_id)
    log.error("step failed", extra={**extra, "error": error})
    return "failed"


@celery_app.task(name="pipeline.advance")
def advance(job_id: int, stage_idx: int) -> str:
    with session_scope() as session:
        outcome = orch.stage_outcome(session, job_id, stage_idx)
    if outcome == "succeeded":  # all steps succeeded or were skipped
        dispatch_stage(job_id, stage_idx + 1)
    return outcome
