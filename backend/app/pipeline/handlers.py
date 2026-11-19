"""Step handlers for real jobs (dummy jobs use steps.dummy_handler).

Implemented:
- proxy: 360p proxy video, waveform (spec 5.2), and from the same decode of the original: the
  thumbnail sprite and scene cuts (spec 5.3). The source is decoded and deinterlaced once.
- analyze: alignment audio and silences (spec 5.3).
Other steps are SKIPPED until their milestone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.db import session_scope
from app.media import analysis, proxy
from app.media.ffmpeg import FfmpegError, ProbeResult
from app.models import Job
from app.storage import JobFiles

from .steps import StepContext, StepError, register

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
