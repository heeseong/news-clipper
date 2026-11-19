"""Proxy video, thumbnail sprite and waveform (spec 5.2, plan 1.5)."""

from __future__ import annotations

import json
import math
from array import array
from pathlib import Path

from .analysis import DEINTERLACE, scene_filter, write_scenes
from .ffmpeg import ProbeResult, ProgressFn, run_ffmpeg

SPRITE_THUMB_WIDTH = 160
SPRITE_COLUMNS = 10
WAVEFORM_RATE = 10  # peaks per second


def _part(path: Path) -> Path:
    """Write to a temporary name first so a half-written file never looks finished."""
    return path.with_name(f"{path.stem}.part{path.suffix}")


def proxy_video_filter(probe: ProbeResult, height: int) -> str:
    filters = [DEINTERLACE] if probe.interlaced else []
    filters += _scale_filters(probe, height)
    return ",".join(filters)


def _scale_filters(probe: ProbeResult, height: int) -> list[str]:
    filters = []
    if probe.height > height:
        filters.append(f"scale=-2:{height}")
    else:
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    filters.append("format=yuv420p")
    return filters


def sprite_layout(probe: ProbeResult) -> dict:
    """One thumbnail per second, SPRITE_COLUMNS per row, aspect ratio of the original."""
    count = max(1, math.ceil(probe.duration))
    return {
        "interval": 1,
        "count": count,
        "columns": SPRITE_COLUMNS,
        "rows": math.ceil(count / SPRITE_COLUMNS),
        "width": SPRITE_THUMB_WIDTH,
        "height": max(2, round(SPRITE_THUMB_WIDTH * probe.height / probe.width / 2) * 2),
    }


def make_proxy(
    src: Path,
    out: Path,
    probe: ProbeResult,
    *,
    audio_order: int | None,
    height: int = 360,
    crf: int = 28,
    preset: str = "veryfast",
    scenes_out: Path | None = None,
    scene_threshold: float = 0.1,
    sprite_out: Path | None = None,
    sprite_info_out: Path | None = None,
    timeout: float | None = None,
    on_progress: ProgressFn | None = None,
) -> dict | None:
    """360p H.264 (by default), GOP 1 s, AAC 128k, faststart. Same frame rate as the source and
    PTS starting at 0, so a time on the proxy is the same time on the original.

    The source is decoded (and deinterlaced) once and split into:
    - the proxy,
    - scene cut detection when `scenes_out` is given (returns the scenes data),
    - the thumbnail sprite when `sprite_out` is given, taken from the original frames rather
      than the low-resolution proxy.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    gop = max(1, round(probe.fps))
    meta = scenes_out.with_suffix(".txt") if scenes_out else None
    sprite = sprite_layout(probe) if sprite_out else None

    branches = [f"{','.join(_scale_filters(probe, height))}[proxy]"]
    if meta is not None:
        meta.parent.mkdir(parents=True, exist_ok=True)
        branches.append(f"{scene_filter(scene_threshold, meta)}[scenes]")
    if sprite is not None:
        branches.append(
            f"fps=1,scale={sprite['width']}:{sprite['height']},"
            f"tile={sprite['columns']}x{sprite['rows']}[sprite]"
        )
    head = f"{DEINTERLACE}," if probe.interlaced else ""
    labels = [f"[b{i}]" for i in range(len(branches))]
    graph = f"[0:v:0]{head}split={len(branches)}{''.join(labels)};" + ";".join(
        f"{label}{branch}" for label, branch in zip(labels, branches, strict=True)
    )

    args = ["-i", str(src), "-filter_complex", graph, "-map", "[proxy]"]
    if audio_order is not None:
        args += ["-map", f"0:a:{audio_order}", "-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    args += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        "-r", probe.fps_rational, "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-map_metadata", "-1",
        str(_part(out)),
    ]  # fmt: skip
    if sprite_out is not None:
        sprite_out.parent.mkdir(parents=True, exist_ok=True)
        args += ["-map", "[sprite]", "-frames:v", "1", "-q:v", "5", "-update", "1", str(_part(sprite_out))]
    if meta is not None:
        # Mapping audio keeps cut times on the file-start axis (see analysis.detect_scenes).
        args += ["-map", "[scenes]", "-f", "null", "-"]
    run_ffmpeg(args, duration=probe.duration, on_progress=on_progress, timeout=timeout)
    _part(out).replace(out)
    if sprite_out is not None and sprite is not None:
        _part(sprite_out).replace(sprite_out)
        if sprite_info_out is not None:
            sprite_info_out.write_text(json.dumps(sprite))
    if scenes_out is not None and meta is not None:
        return write_scenes(meta, scenes_out, probe, scene_threshold)
    return None


def make_waveform(proxy: Path, out: Path, has_audio: bool, timeout: float | None = None) -> dict:
    """Peak amplitude (0..1) per 1/WAVEFORM_RATE second, for the review timeline."""
    peaks: list[float] = []
    if has_audio:
        raw = out.with_suffix(".raw")
        sample_rate = 8000
        run_ffmpeg(
            ["-i", str(proxy), "-map", "0:a:0", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", str(raw)],
            timeout=timeout,
        )
        samples = array("h")
        samples.frombytes(raw.read_bytes())
        raw.unlink()
        step = sample_rate // WAVEFORM_RATE
        for i in range(0, len(samples), step):
            chunk = samples[i : i + step]
            peak = max(max(chunk), -min(chunk))
            peaks.append(round(peak / 32768, 3))
    data = {"rate": WAVEFORM_RATE, "peaks": peaks}
    out.write_text(json.dumps(data, separators=(",", ":")))
    return data
