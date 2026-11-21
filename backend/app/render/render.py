"""Final render of an approved clip from the original (spec 5.6 6-b, plan M6).

- Frame-accurate cut on the file-start axis (cuts, silences, alignment): a rough input-side seek and
  the exact frames picked by timestamp (app/media/seek.py). The first frame is the one the review
  player shows at the clip start; frames before the clip end are kept.
- H.264 High, CRF 18, original size up to `max_height` (1080 by default), deinterlaced when the
  original is interlaced, the original frame rate.
- AAC 192 kb/s 48 kHz; loudness normalised with EBU R128 `loudnorm` in two passes (measure, then
  apply the measured values linearly), -14 LUFS by default.
- Optional fades (off by default: the first frame stays exactly the reviewed one).
- `+faststart`; checked with ffprobe: length within ±0.1 s, an audio stream when the source has one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.media.analysis import DEINTERLACE
from app.media.ffmpeg import FfmpegError, ProbeResult, ProgressFn, probe, run_ffmpeg
from app.media.seek import window

LENGTH_TOLERANCE = 0.1


class RenderError(Exception):
    """The output failed its checks."""


@dataclass(frozen=True)
class RenderOptions:
    max_height: int = 1080
    crf: int = 18
    preset: str = "fast"
    audio_bitrate: str = "192k"
    sample_rate: int = 48000
    loudness: float = -14.0  # integrated LUFS
    true_peak: float = -1.0
    loudness_range: float = 11.0
    fade_in: float = 0.0  # seconds, 0 = off
    fade_out: float = 0.0
    timeout: float | None = None


def audio_source(probe_: ProbeResult, track: int | None, trim: str = "anull") -> tuple[str, str] | None:
    """(filter graph, label) picking the program's audio track, or all tracks mixed, then `trim`."""
    if not probe_.audio:
        return None
    if track is not None:
        if track >= len(probe_.audio):
            raise RenderError(f"오디오 트랙 {track}이 없습니다 (트랙 {len(probe_.audio)}개)")
        return f"[0:a:{track}]{trim}[ain]", "[ain]"
    if len(probe_.audio) == 1:
        return f"[0:a:0]{trim}[ain]", "[ain]"
    inputs = "".join(f"[0:a:{a.order}]{trim}[a{a.order}];" for a in probe_.audio)
    mixed = "".join(f"[a{a.order}]" for a in probe_.audio)
    return f"{inputs}{mixed}amix=inputs={len(probe_.audio)}:normalize=0[ain]", "[ain]"


def _loudnorm(o: RenderOptions, measured: dict | None) -> str:
    base = f"loudnorm=I={o.loudness}:TP={o.true_peak}:LRA={o.loudness_range}"
    if measured is None:
        return base + ":print_format=json"
    return (
        base
        + f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        + f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        + f":offset={measured['target_offset']}:linear=true:print_format=json"
    )


_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def parse_loudnorm(stderr: str) -> dict:
    found = _JSON_RE.findall(stderr)
    if not found:
        raise RenderError("음량 측정 결과를 읽지 못했습니다")
    data = json.loads(found[-1])
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        if data.get(key) in (None, "-inf", "inf"):  # silence: nothing to normalise
            raise RenderError("silent")
    return data


def measure_loudness(src: Path, probe_: ProbeResult, start: float, duration: float, track: int | None,
                     o: RenderOptions) -> dict | None:
    """Pass 1: the clip's loudness. None for a silent clip or a source without audio."""
    w = window(src, probe_, start, start + duration)
    audio = audio_source(probe_, track, w.audio_trim)
    if audio is None:
        return None
    graph = f"{audio[0]};{audio[1]}{_loudnorm(o, None)}[aout]"
    stderr = run_ffmpeg(
        [*w.input_args, "-filter_complex", graph, "-map", "[aout]", "-f", "null", "-"],
        timeout=o.timeout,
    )
    try:
        return parse_loudnorm(stderr)
    except RenderError as e:
        if str(e) == "silent":
            return None
        raise


def video_filter(probe_: ProbeResult, duration: float, o: RenderOptions, trim: str = "null") -> str:
    parts = [trim] + ([DEINTERLACE] if probe_.interlaced else [])
    if probe_.height > o.max_height:
        parts.append(f"scale=-2:{o.max_height}")
    parts.append("format=yuv420p")
    if o.fade_in > 0:
        parts.append(f"fade=t=in:st=0:d={o.fade_in}")
    if o.fade_out > 0:
        parts.append(f"fade=t=out:st={max(0.0, duration - o.fade_out):.3f}:d={o.fade_out}")
    return ",".join(parts)


def render_args(src: Path, out: Path, probe_: ProbeResult, start: float, duration: float, track: int | None,
                o: RenderOptions, measured: dict | None) -> list[str]:
    w = window(src, probe_, start, start + duration)
    graph = [f"[0:v:0]{video_filter(probe_, duration, o, w.video_trim)}[vout]"]
    maps = ["-map", "[vout]"]
    audio = audio_source(probe_, track, w.audio_trim)
    if audio is not None:
        chain = []
        if measured is not None:
            chain.append(_loudnorm(o, measured))
        if o.fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={o.fade_in}")
        if o.fade_out > 0:
            chain.append(f"afade=t=out:st={max(0.0, duration - o.fade_out):.3f}:d={o.fade_out}")
        chain.append(f"aresample={o.sample_rate}")
        graph += [audio[0], f"{audio[1]}{','.join(chain)}[aout]"]
        maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", o.audio_bitrate, "-ar", str(o.sample_rate), "-ac", "2"]
    return [
        *w.input_args,
        "-filter_complex", ";".join(graph), *maps,
        "-c:v", "libx264", "-profile:v", "high", "-preset", o.preset, "-crf", str(o.crf),
        "-movflags", "+faststart", "-map_metadata", "-1",
        str(out),
    ]  # fmt: skip


@dataclass
class RenderResult:
    path: Path
    duration: float
    width: int
    height: int
    loudness: dict | None  # pass-1 measurement


def verify(out: Path, duration: float, expect_audio: bool) -> ProbeResult:
    try:
        p = probe(out)
    except FfmpegError as e:
        raise RenderError(f"렌더 결과를 읽을 수 없습니다: {e}") from e
    if abs(p.duration - duration) > LENGTH_TOLERANCE:
        raise RenderError(f"렌더 길이가 다릅니다: {p.duration:.3f}초 (구간 {duration:.3f}초)")
    if expect_audio and not p.audio:
        raise RenderError("렌더 결과에 오디오가 없습니다")
    return p


def render_clip(src: Path, probe_: ProbeResult, start: float, end: float, out: Path, *, track: int | None = None,
                options: RenderOptions | None = None, on_progress: ProgressFn | None = None) -> RenderResult:
    """Render [start, end) of the original to `out` (written atomically) and check it."""
    o = options or RenderOptions()
    duration = round(end - start, 3)
    if duration <= 0:
        raise RenderError("구간 길이가 0입니다")
    out.parent.mkdir(parents=True, exist_ok=True)
    measured = measure_loudness(src, probe_, start, duration, track, o)
    if on_progress:
        on_progress(0.2)
    part = out.with_name(out.stem + ".part" + out.suffix)
    scaled = (lambda f: on_progress(0.2 + 0.75 * f)) if on_progress else None
    run_ffmpeg(render_args(src, part, probe_, start, duration, track, o, measured),
               duration=duration, on_progress=scaled, timeout=o.timeout)  # fmt: skip
    p = verify(part, duration, expect_audio=bool(probe_.audio))
    part.replace(out)
    if on_progress:
        on_progress(1.0)
    return RenderResult(out, p.duration, p.width, p.height, measured)
