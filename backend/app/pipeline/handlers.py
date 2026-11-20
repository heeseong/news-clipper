"""Step handlers for real jobs (dummy jobs use steps.dummy_handler).

Implemented:
- proxy: 360p proxy video, waveform (spec 5.2), and from the same decode of the original: the
  thumbnail sprite and scene cuts (spec 5.3). The source is decoded and deinterlaced once.
- analyze: alignment audio and silences (spec 5.3).
- newsroom: find the job's cue sheet, fetch its items and scripts, parse and store them (spec 5.4).
  No cue sheet yet → the step WAITs and the job becomes WAITING_RUNDOWN (spec 10).
- align: re-check the cue sheet edition, compute (or reuse) the acoustic model's log-probs, align
  the scripts and create DRAFT clips for the upload reports (spec 5.5).
Other steps are SKIPPED until their milestone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.align import acoustic
from app.align import service as aligning
from app.align.logprobs import LogProbs
from app.core.config import get_settings
from app.core.db import session_scope
from app.media import analysis, proxy
from app.media.ffmpeg import FfmpegError, ProbeResult
from app.models import Job
from app.newsroom import service as newsroom
from app.newsroom.factory import get_provider
from app.newsroom.provider import NewsroomError, NewsroomUnavailable
from app.storage import JobFiles

from .steps import RetryableStepError, StepContext, StepError, StepWaiting, register

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobSource:
    path: Path
    probe: ProbeResult
    audio_track: int | None  # program profile setting; None = default


def load_source(ctx: StepContext) -> JobSource:
    files = JobFiles(ctx.job_id)
    with session_scope() as session:
        job = session.get(Job, ctx.job_id)
        if job is None or not job.source_path:
            raise StepError("작업에 원본 영상이 없습니다")
        path = Path(job.source_path)
        audio_track = job.program.audio_track if job.program else None
    if not path.exists():
        raise StepError(f"원본 영상 파일이 없습니다: {path.name}")
    if not files.probe.exists():
        raise StepError("원본 영상 검사 결과(probe.json)가 없습니다")
    probe = ProbeResult.model_validate_json(files.probe.read_text())
    return JobSource(path, probe, audio_track)


def _scaled(report, start: float, end: float):
    """Map a sub-task's 0..1 progress onto [start, end] of the step."""
    return lambda f: report(start + (end - start) * f)


@register("proxy")
def proxy_step(ctx: StepContext) -> None:
    s = get_settings()
    src = load_source(ctx)
    files = JobFiles(ctx.job_id)
    audio_order = None
    if src.probe.audio:
        audio_order = src.audio_track if src.audio_track is not None else 0
        if audio_order >= len(src.probe.audio):
            raise StepError(f"오디오 트랙 {audio_order}이 없습니다 (트랙 {len(src.probe.audio)}개)")
    try:
        proxy.make_proxy(
            src.path,
            files.proxy,
            src.probe,
            audio_order=audio_order,
            height=s.proxy_height,
            crf=s.proxy_crf,
            preset=s.proxy_preset,
            scenes_out=files.scenes,
            scene_threshold=s.scene_threshold,
            sprite_out=files.sprite,
            sprite_info_out=files.sprite_info,
            timeout=s.ffmpeg_timeout_seconds,
            on_progress=_scaled(ctx.report, 0.0, 0.95),
        )
        proxy.make_waveform(
            files.proxy, files.waveform, has_audio=audio_order is not None, timeout=s.ffmpeg_timeout_seconds
        )
    except FfmpegError as e:
        raise StepError(str(e)) from e
    ctx.report(1.0)


@register("analyze")
def analyze_step(ctx: StepContext) -> None:
    s = get_settings()
    src = load_source(ctx)
    files = JobFiles(ctx.job_id)
    try:
        used = analysis.extract_align_audio(
            src.path,
            files.align_audio,
            src.probe,
            track=src.audio_track,
            timeout=s.ffmpeg_timeout_seconds,
            on_progress=_scaled(ctx.report, 0.0, 0.8),
        )
        silences = analysis.detect_silences(
            files.align_audio,
            files.silences,
            src.probe.duration,
            noise_db=s.silence_noise_db,
            min_seconds=s.silence_min_seconds,
            timeout=s.ffmpeg_timeout_seconds,
        )
    except FfmpegError as e:
        raise StepError(str(e)) from e
    log.info(
        "analysis done",
        extra={
            "job_id": ctx.job_id,
            "audio": used,
            "silences": len(silences["silences"]),
        },
    )
    ctx.report(1.0)


@register("newsroom")
def newsroom_step(ctx: StepContext) -> None:
    with session_scope() as session:
        job = session.get(Job, ctx.job_id)
        if job is None:
            raise StepError("작업을 찾을 수 없습니다")
        if job.broadcast_date is None:
            raise StepError("방송일이 없어 큐시트를 찾을 수 없습니다")
        broadcast_date = job.broadcast_date
        program = newsroom.ProgramInfo.of(job.program)
    cue_sheet_id = ctx.params.get("cue_sheet_id")
    try:
        fetched = newsroom.fetch(get_provider(), broadcast_date, program, cue_sheet_id)
    except newsroom.RundownNotFound as e:
        raise StepWaiting(f"큐시트 대기: {e}") from e
    except NewsroomUnavailable as e:
        raise RetryableStepError(str(e)) from e
    except NewsroomError as e:
        raise StepError(str(e)) from e
    ctx.report(0.5)
    newsroom.write_files(ctx.job_id, fetched)
    with session_scope() as session:
        newsroom.store(session, session.get(Job, ctx.job_id), fetched)
    ctx.report(1.0)


def _recheck_edition(job_id: int) -> None:
    """Use the latest edition of the cue sheet (spec 5.4). A newsroom outage only warns."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        rundown = aligning.job_rundown(session, job_id)
        if job is None or rundown is None or job.broadcast_date is None:
            return
        cue_sheet_id, edition = rundown.newsroom_rundown_id, rundown.edition
        broadcast_date, program = job.broadcast_date, newsroom.ProgramInfo.of(job.program)
    try:
        provider = get_provider()
        if provider.get_rundown(cue_sheet_id).version == edition:
            return
        fetched = newsroom.fetch(provider, broadcast_date, program, cue_sheet_id)
    except (NewsroomError, newsroom.RundownNotFound) as e:
        log.warning("cue sheet edition check failed", extra={"job_id": job_id, "error": str(e)})
        return
    newsroom.write_files(job_id, fetched)
    with session_scope() as session:
        newsroom.store(session, session.get(Job, job_id), fetched)
    log.info("cue sheet edition changed before alignment", extra={"job_id": job_id, "from": edition,
                                                                   "to": fetched.summary.version})


def load_logprobs(ctx: StepContext, files: JobFiles) -> LogProbs:
    """Cached in analysis/logprobs.npz; recomputed when align.wav is newer."""
    s = get_settings()
    if not files.align_audio.exists():
        raise StepError("정렬용 오디오(align.wav)가 없습니다")
    cache = files.logprobs
    if cache.exists() and cache.stat().st_mtime >= files.align_audio.stat().st_mtime:
        return LogProbs.load(cache)
    try:
        lp = acoustic.compute_logprobs(
            files.align_audio,
            s.align_model,
            chunk_seconds=s.align_chunk_seconds,
            threads=s.align_threads,
            on_progress=_scaled(ctx.report, 0.05, 0.85),
        )
    except acoustic.ModelUnavailable as e:
        raise StepError(str(e)) from e
    lp.save(cache)
    return lp


@register("align")
def align_step(ctx: StepContext) -> None:
    _recheck_edition(ctx.job_id)
    ctx.report(0.05)
    files = JobFiles(ctx.job_id)
    with session_scope() as session:
        job = session.get(Job, ctx.job_id)
        rundown = aligning.job_rundown(session, ctx.job_id)
        if job is None or rundown is None:
            raise StepError("큐시트가 없어 정렬할 수 없습니다")
        items = aligning.align_items_from_db(list(rundown.items))
        fps = job.fps or 29.97
        pronunciations = dict(job.program.pronunciations or {}) if job.program else {}
    lp = load_logprobs(ctx, files)
    ctx.report(0.85)
    cuts, silences = aligning.load_signals(files.scenes, files.silences)
    result = aligning.plan_clips(lp, items, cuts, silences, fps, pronunciations)
    aligning.write_json(files.alignment, result)
    with session_scope() as session:
        job = session.get(Job, ctx.job_id)
        aligning.store_result(session, job, aligning.job_rundown(session, ctx.job_id), result)
    log.info(
        "alignment done",
        extra={
            "job_id": ctx.job_id,
            "clips": len(result.clips),
            "auto": sum(c.start is not None for c in result.clips),
            "warnings": result.warnings,
        },
    )
    ctx.report(1.0)
