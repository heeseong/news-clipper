"""Orchestrator: parallel stages, joins, failures, automatic and manual retries (Celery eager)."""

from __future__ import annotations

import pytest

from app.core.db import session_scope
from app.models import Job, JobStatus, StepStatus
from app.pipeline import orchestrator as orch
from app.pipeline import tasks
from app.pipeline.definition import PIPELINE, STEPS

pytestmark = pytest.mark.usefixtures("app_env")


def make_job(**dummy) -> int:
    with session_scope() as s:
        job = Job(status=JobStatus.UPLOADED, params={"dummy": {"seconds": 0, **dummy}})
        orch.create_steps(job)
        s.add(job)
        s.flush()
        return job.id


def load(job_id: int) -> Job:
    with session_scope() as s:
        job = s.get(Job, job_id)
        job.steps  # noqa: B018  (load before the session closes)
        return job


def steps(job: Job) -> dict[str, tuple[StepStatus, int]]:
    return {r.step: (r.status, r.attempts) for r in job.steps}


def test_pipeline_has_parallel_and_sequential_stages():
    assert len(PIPELINE[0].steps) > 1  # parallel group
    assert len(PIPELINE) > 1  # followed by further stages
    assert {q for _, s in STEPS.values() for q in [s.queue]} <= {"media", "align", "ai", "publish"}


def test_job_runs_all_stages():
    job_id = make_job()
    tasks.start_job(job_id)
    job = load(job_id)
    assert job.status == JobStatus.REVIEW_READY
    assert job.current_step is None
    assert all(st == (StepStatus.SUCCEEDED, 1) for st in steps(job).values())
    assert [r.stage for r in job.steps] == sorted(r.stage for r in job.steps)


def test_failure_stops_pipeline_and_keeps_earlier_results():
    job_id = make_job(fail_steps=["align"])
    tasks.start_job(job_id)
    job = load(job_id)
    assert job.status == JobStatus.FAILED
    assert job.current_step == "align"
    assert "dummy failure" in job.error
    st = steps(job)
    assert st["proxy"] == (StepStatus.SUCCEEDED, 1)
    assert st["align"] == (StepStatus.FAILED, 1)
    assert st["metadata"] == (StepStatus.PENDING, 0)  # never started


def test_failure_in_parallel_group_still_runs_siblings():
    job_id = make_job(fail_steps=["newsroom"])
    tasks.start_job(job_id)
    st = steps(load(job_id))
    assert st["newsroom"][0] == StepStatus.FAILED
    assert st["proxy"][0] == st["analyze"][0] == StepStatus.SUCCEEDED
    assert st["align"] == (StepStatus.PENDING, 0)


def test_manual_retry_resumes_from_failed_step():
    job_id = make_job(fail_steps=["newsroom"])
    tasks.start_job(job_id)
    tasks.retry_step(job_id, "newsroom")
    job = load(job_id)
    assert job.status == JobStatus.REVIEW_READY
    assert job.error is None
    st = steps(job)
    assert st["newsroom"] == (StepStatus.SUCCEEDED, 2)
    assert st["proxy"] == (StepStatus.SUCCEEDED, 1)  # sibling not run again


def test_retry_of_finished_step_reruns_later_stages():
    job_id = make_job()
    tasks.start_job(job_id)
    tasks.retry_step(job_id, "align")
    st = steps(load(job_id))
    assert st["proxy"] == (StepStatus.SUCCEEDED, 1)
    assert st["align"] == (StepStatus.SUCCEEDED, 2)
    assert st["metadata"] == (StepStatus.SUCCEEDED, 2)


def test_transient_error_is_retried_automatically(monkeypatch):
    from app.worker import celery_app

    monkeypatch.setattr(tasks, "backoff_seconds", lambda retries: 0)
    # Eager mode only re-runs a retried task when exceptions are not propagated.
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    job_id = make_job(flaky_steps=["analyze"])
    tasks.start_job(job_id)
    job = load(job_id)
    assert job.status == JobStatus.REVIEW_READY
    assert steps(job)["analyze"] == (StepStatus.SUCCEEDED, 2)


def test_transient_error_fails_after_max_retries(monkeypatch):
    from app.pipeline import steps as steps_mod
    from app.worker import celery_app

    monkeypatch.setattr(tasks, "backoff_seconds", lambda retries: 0)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)

    def always_flaky(ctx):
        raise steps_mod.RetryableStepError("still down")

    monkeypatch.setattr(tasks, "resolve_handler", lambda step, is_dummy: always_flaky)
    job_id = make_job()
    tasks.start_job(job_id)
    job = load(job_id)
    assert job.status == JobStatus.FAILED
    max_retries = STEPS["proxy"][1].max_retries
    assert steps(job)["proxy"] == (StepStatus.FAILED, max_retries + 1)


def test_retry_rejected_while_steps_are_active():
    job_id = make_job(fail_steps=["proxy"])
    tasks.start_job(job_id)
    with session_scope() as s:
        orch._step(s, job_id, "analyze").status = StepStatus.RUNNING
    with pytest.raises(orch.TransitionError):
        tasks.retry_step(job_id, "proxy")


def test_retry_rejected_before_earlier_stages_finish():
    job_id = make_job(fail_steps=["proxy"])
    tasks.start_job(job_id)
    with pytest.raises(orch.TransitionError):
        tasks.retry_step(job_id, "align")


def test_duplicate_delivery_of_finished_step_is_skipped():
    job_id = make_job()
    tasks.start_job(job_id)
    assert tasks.run_step.apply(args=(job_id, "proxy")).get() == "skipped"
    assert steps(load(job_id))["proxy"] == (StepStatus.SUCCEEDED, 1)


def test_unimplemented_steps_are_skipped(monkeypatch):
    """Steps without a handler are SKIPPED and do not stop the pipeline."""
    from app.pipeline import steps as steps_mod

    monkeypatch.setattr(steps_mod, "_HANDLERS", {"proxy": lambda ctx: None})
    with session_scope() as s:
        job = Job(status=JobStatus.UPLOADED)
        orch.create_steps(job)
        s.add(job)
        s.flush()
        job_id = job.id
    tasks.start_job(job_id)
    job = load(job_id)
    assert job.status == JobStatus.REVIEW_READY
    st = steps(job)
    assert st["proxy"] == (StepStatus.SUCCEEDED, 1)
    assert st["analyze"][0] == StepStatus.SKIPPED
    assert "구현되지" in next(r.error for r in job.steps if r.step == "analyze")


def test_progress_is_recorded(monkeypatch):
    from app.pipeline import steps as steps_mod

    seen = []

    def handler(ctx):
        ctx.report(0.5)
        with session_scope() as s:
            seen.append(orch._step(s, ctx.job_id, ctx.step).progress)

    monkeypatch.setattr(steps_mod, "_HANDLERS", {"proxy": handler})
    with session_scope() as s:
        job = Job(status=JobStatus.UPLOADED)
        orch.create_steps(job)
        s.add(job)
        s.flush()
        job_id = job.id
    tasks.start_job(job_id)
    assert seen == [0.5]
    assert next(r for r in load(job_id).steps if r.step == "proxy").progress is None  # cleared when done
