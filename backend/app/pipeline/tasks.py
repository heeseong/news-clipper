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
from pathlib import Path

from celery import chord

from app.core.db import session_scope
from app.core.events import publish_job_changed
from app.models import Job
from app.worker import celery_app

from . import handlers  # noqa: F401  (registers step handlers)
from . import orchestrator as orch
from .definition import CONTROL_QUEUE, STEPS
from .steps import RetryableStepError, StepSkipped, StepWaiting, resolve_handler

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


def retry_step(job_id: int, step: str, params: dict | None = None) -> None:
    """Manual retry from a step (API). Raises TransitionError/LookupError.

    `params` are merged into the job's params first (e.g. the cue sheet the user picked)."""
    with session_scope() as session:
        stage_idx = orch.prepare_retry(session, job_id, step)
        if params:
            job = session.get(Job, job_id)
            job.params = {**(job.params or {}), **params}
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
    except StepWaiting as e:
        with session_scope() as session:
            orch.mark_waiting(session, job_id, step, str(e))
        publish_job_changed(job_id)
        log.info("step waiting", extra={**extra, "reason": str(e)})
        return "waiting"
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
    elif outcome == "waiting":
        with session_scope() as session:
            orch.wait_job(session, job_id, stage_idx)
        publish_job_changed(job_id)
    return outcome


@celery_app.task(name="clips.regenerate_metadata")
def regenerate_clip_metadata(clip_id: int) -> str:
    """Plan 4.6: the metadata of one clip again (also after the reviewer sets a range, M5)."""
    from app.metadata import service as metadata
    from app.metadata.factory import get_llm_provider
    from app.models import Clip
    from app.thumbnails import service as thumbnails

    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None:
            return "missing"
        job_id = clip.job_id
        meta = metadata.generate_for_clip(session, clip, get_llm_provider())
        status = meta.llm_status
    with session_scope() as session:
        thumbnails.render_selected(session, clip_id)
    publish_job_changed(job_id)
    return status


@celery_app.task(name="clips.prepare_after_range")
def prepare_clip_after_range(clip_id: int) -> str:
    """After the reviewer sets or changes a clip's range (M5): thumbnail candidates from the new
    range, and metadata when the clip has none yet (a clip the alignment could not place)."""
    from app.align import service as aligning
    from app.core.config import get_settings as settings_of
    from app.media.ffmpeg import ProbeResult
    from app.metadata import service as metadata
    from app.metadata.factory import get_llm_provider
    from app.models import Clip, ClipMetadata
    from app.storage import JobFiles
    from app.thumbnails import service as thumbnails

    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None or clip.needs_manual_range:
            return "missing"
        job = session.get(Job, clip.job_id)
        job_id, source = job.id, job.source_path
        if session.get(ClipMetadata, clip_id) is None:
            metadata.generate_for_clip(session, clip, get_llm_provider())
        (r,) = thumbnails.clip_ranges(session, job_id, [clip_id])
    files = JobFiles(job_id)
    s = settings_of()
    if source and files.probe.exists():
        probe = ProbeResult.model_validate_json(files.probe.read_text())
        cuts, _ = aligning.load_signals(files.scenes, files.silences)
        rows = thumbnails.make_candidates(
            job_id, r, Path(source), probe, cuts, count=s.thumbnail_candidates, samples=s.thumbnail_samples
        )
        with session_scope() as session:
            thumbnails.store(session, clip_id, rows)
    with session_scope() as session:
        thumbnails.render_selected(session, clip_id)
    publish_job_changed(job_id)
    return "ok"


@celery_app.task(name="clips.render", bind=True, max_retries=1)
def render_clip(self, clip_id: int) -> str:
    """Final render of an approved clip from the original (M6, spec 5.6 6-b).

    APPROVED → RENDERING → RENDERED (render_path). A failure is retried once, then the clip goes
    back to APPROVED with render_error (POST /api/clips/{id}/render tries again). A clip reopened
    or re-ranged meanwhile keeps nothing of this render."""
    from app.core.config import get_settings as settings_of
    from app.media.ffmpeg import FfmpegError, ProbeResult
    from app.models import Clip, ClipStatus
    from app.models.base import utcnow
    from app.render.render import RenderError, RenderOptions
    from app.render.render import render_clip as render
    from app.storage import JobFiles

    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        if clip is None or clip.status not in (ClipStatus.APPROVED, ClipStatus.RENDERING) or clip.needs_manual_range:
            return "skipped"
        job = session.get(Job, clip.job_id)
        clip.status, clip.render_error = ClipStatus.RENDERING, None
        job_id, source = job.id, job.source_path
        track = job.program.audio_track if job.program else None
        start, end = clip.start_sec, clip.end_sec
    publish_job_changed(job_id)
    files = JobFiles(job_id)
    s = settings_of()
    out = files.final(clip_id)
    error = None
    try:
        if not source or not Path(source).exists() or not files.probe.exists():
            raise RenderError("원본 영상이 없습니다 (보존 기간이 지나 삭제됐을 수 있습니다)")
        probe = ProbeResult.model_validate_json(files.probe.read_text())
        opts = RenderOptions(
            max_height=s.render_max_height, crf=s.render_crf, preset=s.render_preset, loudness=s.render_loudness,
            fade_in=s.render_fade_in, fade_out=s.render_fade_out, timeout=s.ffmpeg_timeout_seconds,
        )  # fmt: skip
        render(Path(source), probe, start, end, out, track=track, options=opts)
    except (FfmpegError, RenderError, OSError) as e:
        if self.request.retries < self.max_retries and not isinstance(e, RenderError):
            raise self.retry(exc=e, countdown=5) from e
        error = str(e)
        log.warning("render failed", extra={"clip_id": clip_id, "error": error})
    with session_scope() as session:
        clip = session.get(Clip, clip_id)
        current = clip is not None and clip.status == ClipStatus.RENDERING and (clip.start_sec, clip.end_sec) == (start, end)
        if not current:
            out.unlink(missing_ok=True)
            return "stale"
        uploads: list[int] = []
        if error:
            clip.status, clip.render_error = ClipStatus.APPROVED, error
        else:
            clip.status, clip.render_path, clip.rendered_at = ClipStatus.RENDERED, str(out), utcnow()
            if s.publish_auto:  # M7: upload to the clip's channels right away (none chosen → stays RENDERED)
                from app.publish import service as publishing

                uploads = publishing.enqueue(session, clip, strict=False)
    publish_job_changed(job_id)
    if uploads:
        from app.publish import service as publishing

        publishing.dispatch(uploads)
    return "failed" if error else "rendered"
