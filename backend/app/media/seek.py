"""Frame-accurate access to a time range of the original (render, thumbnail frames).

Times in the pipeline are seconds from the file start. FFmpeg's input-side `-ss` is fast but not
exact on every container: on MPEG-TS with timestamps that do not start at 0 it landed 6 frames
late (2026-11-21, tests/test_render.py). So the input is only sought roughly (COARSE seconds
early) and the exact frames are picked by timestamp with `trim`/`atrim` on the original
timestamps (`-copyts`):

- the first frame is the one on screen at `start` (pts <= start < pts + 1 frame), which is what
  the review player shows at that time,
- frames with pts < `end` are kept (an end half a frame before a cut leaves the cut out).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import ProbeResult

COARSE = 3.0  # seconds sought early on the input side


@dataclass(frozen=True)
class Window:
    input_args: list[str]  # before the output options: -ss … -copyts -i SRC
    video_trim: str  # filter, timestamps reset to 0
    audio_trim: str


def window(src: Path, probe: ProbeResult, start: float, end: float | None = None) -> Window:
    base = probe.start_time
    frame = 1 / probe.fps if probe.fps else 0.04
    v_start = base + start - frame + 0.001
    v_end = f":end={base + end:.6f}" if end is not None else ""
    a_end = f":end={base + end:.6f}" if end is not None else ""
    return Window(
        input_args=["-ss", f"{max(0.0, start - COARSE):.3f}", "-copyts", "-i", str(src)],
        video_trim=f"trim=start={v_start:.6f}{v_end},setpts=PTS-STARTPTS",
        audio_trim=f"atrim=start={base + start:.6f}{a_end},asetpts=PTS-STARTPTS",
    )
