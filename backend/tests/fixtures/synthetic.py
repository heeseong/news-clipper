"""Synthetic broadcast-like test videos built with FFmpeg (no real material in the repo).

Each segment is a solid color (a "shot") with a tone or silence, so a test knows exactly where the
scene cuts and silences are:

    segments = [Segment("navy", 2.0, 440), Segment("darkred", 1.5, None), Segment("darkgreen", 2.0, 660)]
    -> cuts at 2.0 s and 3.5 s, silence from 2.0 s to 3.5 s
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    color: str
    seconds: float
    tone_hz: int | None  # None = silence


@dataclass(frozen=True)
class SyntheticVideo:
    path: Path
    segments: tuple[Segment, ...]
    fps: int
    width: int
    height: int

    @property
    def duration(self) -> float:
        return sum(s.seconds for s in self.segments)

    @property
    def cuts(self) -> list[float]:
        """Times where the picture changes (segment starts after the first)."""
        t, out = 0.0, []
        for s in self.segments[:-1]:
            t += s.seconds
            out.append(round(t, 3))
        return out

    @property
    def silences(self) -> list[tuple[float, float]]:
        t, out = 0.0, []
        for s in self.segments:
            if s.tone_hz is None:
                out.append((round(t, 3), round(t + s.seconds, 3)))
            t += s.seconds
        return out


DEFAULT_SEGMENTS = (
    Segment("navy", 2.0, 440),  # anchor
    Segment("darkred", 3.0, 660),  # report
    Segment("black", 1.0, None),  # pause
    Segment("darkgreen", 2.0, 550),  # next anchor
)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_synthetic_video(
    path: Path,
    segments: tuple[Segment, ...] = DEFAULT_SEGMENTS,
    fps: int = 25,
    width: int = 320,
    height: int = 240,
    sample_rate: int = 48000,
) -> SyntheticVideo:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for s in segments:
        args += ["-f", "lavfi", "-i", f"color=c={s.color}:s={width}x{height}:r={fps}:d={s.seconds}"]
        if s.tone_hz is None:
            args += ["-f", "lavfi", "-t", str(s.seconds), "-i", f"anullsrc=r={sample_rate}:cl=mono"]
        else:
            src = f"sine=frequency={s.tone_hz}:sample_rate={sample_rate}:duration={s.seconds}"
            args += ["-f", "lavfi", "-i", src]
    pads = "".join(f"[{2 * i}:v][{2 * i + 1}:a]" for i in range(len(segments)))
    args += [
        "-filter_complex",
        f"{pads}concat=n={len(segments)}:v=1:a=1[v][a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(fps),
        "-c:a",
        "aac",
        "-ac",
        "1",
        str(path),
    ]
    subprocess.run(args, check=True)
    return SyntheticVideo(path, tuple(segments), fps, width, height)
