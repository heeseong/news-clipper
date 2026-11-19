"""ffprobe / ffmpeg wrappers (plan 1.4).

- `probe()` returns the facts the pipeline needs (duration, fps, size, interlacing, audio tracks).
- `run_ffmpeg()` runs one command with progress reporting (`-progress pipe:1`), a timeout and
  readable errors. stderr is returned because filters such as silencedetect log there.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)

ProgressFn = Callable[[float], None]


class FfmpegError(Exception):
    def __init__(self, message: str, returncode: int | None = None, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail

    def __str__(self) -> str:
        tail = self.stderr_tail.strip().splitlines()[-3:]
        return " / ".join([self.args[0], *tail]) if tail else self.args[0]


class AudioStream(BaseModel):
    index: int  # absolute stream index in the file
    order: int  # n-th audio stream (ffmpeg `0:a:{order}`)
    codec: str | None
    channels: int
    sample_rate: int | None
    language: str | None = None


class ProbeResult(BaseModel):
    format_name: str
    duration: float
    start_time: float
    # Video stream start relative to the file start. Pipeline times (cuts, silences, alignment)
    # are measured from the file start, so the first frame is at this time, not necessarily 0.
    video_start: float = 0.0
    width: int
    height: int
    fps: float
    fps_rational: str  # e.g. "30000/1001"
    video_codec: str | None
    field_order: str | None  # progressive, tt, bb, tb, bt, or None when unknown
    audio: list[AudioStream]
    creation_time: str | None = None

    @property
    def interlaced(self) -> bool:
        return self.field_order in ("tt", "bb", "tb", "bt")


def _parse_rate(rate: str | None) -> Fraction | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        value = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe(path: Path, timeout: int = 120) -> ProbeResult:
    """Raises FfmpegError if the file cannot be read or has no video stream."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FfmpegError("ffprobe timed out") from e
    except FileNotFoundError as e:
        raise FfmpegError("ffprobe is not installed on the server") from e
    if out.returncode != 0:
        raise FfmpegError("ffprobe could not read the file", out.returncode, out.stderr)
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover(s)), None)
    if video is None:
        raise FfmpegError("no video stream")
    rate = _parse_rate(video.get("r_frame_rate")) or _parse_rate(video.get("avg_frame_rate"))
    if rate is None:
        raise FfmpegError("unknown frame rate")
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise FfmpegError("unknown duration")
    audio = [
        AudioStream(
            index=s["index"],
            order=i,
            codec=s.get("codec_name"),
            channels=int(s.get("channels") or 0),
            sample_rate=int(s["sample_rate"]) if s.get("sample_rate") else None,
            language=(s.get("tags") or {}).get("language"),
        )
        for i, s in enumerate(s for s in streams if s.get("codec_type") == "audio")
    ]
    start_time = float(fmt.get("start_time") or 0)
    video_start = float(video.get("start_time") or start_time) - start_time
    return ProbeResult(
        format_name=fmt.get("format_name", ""),
        duration=duration,
        start_time=start_time,
        video_start=round(max(0.0, video_start), 6),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=float(rate),
        fps_rational=f"{rate.numerator}/{rate.denominator}",
        video_codec=video.get("codec_name"),
        field_order=video.get("field_order"),
        audio=audio,
        creation_time=(fmt.get("tags") or {}).get("creation_time"),
    )


def _is_cover(stream: dict) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def run_ffmpeg(
    args: Sequence[str],
    *,
    duration: float | None = None,
    on_progress: ProgressFn | None = None,
    timeout: float | None = None,
    progress_interval: float = 1.0,
) -> str:
    """Run `ffmpeg <args>`; returns stderr. `duration` (seconds of output) enables progress."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-nostats", "-progress", "pipe:1", *args]
    log.debug("ffmpeg", extra={"cmd": " ".join(cmd)})
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError as e:
        raise FfmpegError("ffmpeg is not installed on the server") from e

    stderr_lines: list[str] = []
    tail: deque[str] = deque(maxlen=20)

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)
            tail.append(line)

    reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()

    killer: threading.Timer | None = None
    timed_out = threading.Event()
    if timeout:

        def kill() -> None:
            timed_out.set()
            proc.kill()

        killer = threading.Timer(timeout, kill)
        killer.start()

    last_report = 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if not on_progress or not duration:
                continue
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.lstrip("-").isdigit():
                now = time.monotonic()
                if now - last_report >= progress_interval:
                    last_report = now
                    on_progress(max(0.0, min(1.0, int(value) / 1e6 / duration)))
            elif key == "progress" and value == "end":
                on_progress(1.0)
        proc.wait()
    finally:
        if killer:
            killer.cancel()
        reader.join(timeout=5)
    if timed_out.is_set():
        raise FfmpegError(f"ffmpeg timed out after {timeout:.0f}s", proc.returncode, "".join(tail))
    if proc.returncode != 0:
        raise FfmpegError(f"ffmpeg failed (exit {proc.returncode})", proc.returncode, "".join(tail))
    return "".join(stderr_lines)
