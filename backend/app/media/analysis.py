"""Alignment audio and signal analysis (spec 5.3, plan 1.6–1.7).

Scene cuts use FFmpeg's scene score with a low threshold (0.1), the method validated in the
alignment PoC (eval/poc/RESULTS.md). Times are seconds from the start of the recording, the same
axis as the proxy (FFmpeg shifts input timestamps so the first frame is at 0).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .ffmpeg import FfmpegError, ProbeResult, ProgressFn, run_ffmpeg

ALIGN_SAMPLE_RATE = 16000
# One output frame per input frame keeps the frame rate (and time axis) unchanged.
DEINTERLACE = "yadif=mode=send_frame:parity=auto:deint=all"
SCENE_ANALYSIS_WIDTH = 320


def extract_align_audio(
    src: Path,
    out: Path,
    probe: ProbeResult,
    *,
    track: int | None,
    timeout: float | None = None,
    on_progress: ProgressFn | None = None,
) -> str:
    """16 kHz mono 16-bit WAV. `track` picks one audio stream (program profile setting);
    otherwise all audio streams are mixed down. Returns a description of what was used."""
    if not probe.audio:
        raise FfmpegError("no audio stream")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f"{out.stem}.part{out.suffix}")
    if track is not None:
        if track >= len(probe.audio):
            raise FfmpegError(f"audio track {track} does not exist (file has {len(probe.audio)})")
        mapping = ["-map", f"0:a:{track}"]
        used = f"track {track}"
    elif len(probe.audio) == 1:
        mapping = ["-map", "0:a:0"]
        used = "track 0"
    else:
        inputs = "".join(f"[0:a:{a.order}]" for a in probe.audio)
        mapping = ["-filter_complex", f"{inputs}amix=inputs={len(probe.audio)}[mix]", "-map", "[mix]"]
        used = f"mix of {len(probe.audio)} tracks"
    run_ffmpeg(
        ["-i", str(src), *mapping, "-ac", "1", "-ar", str(ALIGN_SAMPLE_RATE), "-c:a", "pcm_s16le", str(part)],
        duration=probe.duration,
        on_progress=on_progress,
        timeout=timeout,
    )
    part.replace(out)
    return used


_PTS_RE = re.compile(r"pts_time:(-?[\d.]+)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=([\d.]+)")


def parse_scene_metadata(text: str) -> list[tuple[float, float]]:
    """Parse `metadata=print` output into (time, score) pairs."""
    cuts: list[tuple[float, float]] = []
    time: float | None = None
    for line in text.splitlines():
        if m := _PTS_RE.search(line):
            time = float(m.group(1))
        elif (m := _SCORE_RE.search(line)) and time is not None:
            cuts.append((time, float(m.group(1))))
            time = None
    return cuts


def scene_filter(threshold: float, meta: Path) -> str:
    """Filter chain (after deinterlacing) that logs frames whose scene score exceeds `threshold`."""
    return ",".join(
        [
            f"scale={SCENE_ANALYSIS_WIDTH}:-2",
            f"select='gt(scene,{threshold})'",
            f"metadata=print:file='{_filter_path(meta)}'",
        ]
    )


def write_scenes(meta: Path, out: Path, probe: ProbeResult, threshold: float) -> dict:
    """Turn the metadata log written by `scene_filter` into scenes.json."""
    pairs = parse_scene_metadata(meta.read_text() if meta.exists() else "")
    meta.unlink(missing_ok=True)
    data = {
        "threshold": threshold,
        "fps": probe.fps,
        "duration": probe.duration,
        "cuts": [
            # frame: index in the video stream (counted from its first frame)
            {"time": round(t, 4), "frame": round((t - probe.video_start) * probe.fps), "score": round(s, 4)}
            for t, s in pairs
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.part")
    tmp.write_text(json.dumps(data))
    tmp.replace(out)
    return data


def detect_scenes(
    src: Path,
    out: Path,
    probe: ProbeResult,
    *,
    threshold: float = 0.1,
    timeout: float | None = None,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Shot changes on their own pass. Each cut is the time (from the file start, the same axis as
    the alignment audio) of the first frame of the new shot.
    The pipeline detects cuts during the proxy encode instead (`proxy.make_proxy`), which saves
    decoding and deinterlacing the source twice.

    The cut frame itself may be a one-frame blend; boundary logic (M3) handles that.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = out.with_suffix(".txt")
    filters = [DEINTERLACE] if probe.interlaced else []
    filters.append(scene_filter(threshold, meta))
    # Keep the audio streams in the run: FFmpeg measures time from the start of the streams in
    # use, and the alignment audio is measured from the file start (audio usually starts first).
    # A video-only run would shift cuts by the audio/video start difference.
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-filter_complex",
            f"[0:v:0]{','.join(filters)}[scenes]",
            "-map",
            "[scenes]",
            "-map",
            "0:a?",
            "-c:a",
            "pcm_s16le",
            "-f",
            "null",
            "-",
        ],  # fmt: skip
        duration=probe.duration,
        on_progress=on_progress,
        timeout=timeout,
    )
    return write_scenes(meta, out, probe, threshold)


def _filter_path(path: Path) -> str:
    # Inside a quoted filter argument only backslash and quote need escaping.
    return str(path).replace("\\", "\\\\").replace("'", r"'\''")


_SILENCE_START_RE = re.compile(r"silence_start: (-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end: (-?[\d.]+)")


def parse_silencedetect(stderr: str, duration: float) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        if m := _SILENCE_START_RE.search(line):
            start = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END_RE.search(line)) and start is not None:
            ranges.append((start, float(m.group(1))))
            start = None
    if start is not None:  # silence runs to the end of the file
        ranges.append((start, duration))
    return ranges


def detect_silences(
    wav: Path,
    out: Path,
    duration: float,
    *,
    noise_db: float = -35.0,
    min_seconds: float = 0.25,
    timeout: float | None = None,
) -> dict:
    stderr = run_ffmpeg(
        ["-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"],
        timeout=timeout,
    )
    ranges = parse_silencedetect(stderr, duration)
    data = {
        "noise_db": noise_db,
        "min_seconds": min_seconds,
        "silences": [{"start": round(s, 4), "end": round(e, 4)} for s, e in ranges],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data))
    return data
