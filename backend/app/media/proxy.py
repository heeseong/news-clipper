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


def make_proxy(
    src: Path,
    out: Path,
    probe: ProbeResult,
    *,
    audio_order: int | None,
    height: int = 720,
    crf: int = 28,
    preset: str = "veryfast",
    scenes_out: Path | None = None,
    scene_threshold: float = 0.1,
    timeout: float | None = None,
    on_progress: ProgressFn | None = None,
) -> dict | None:
    """720p H.264, GOP 1 s, AAC 128k, faststart. Same frame rate as the source and PTS starting
    at 0, so a time on the proxy is the same time on the original.

    With `scenes_out`, scene cuts are detected in the same pass (one decode + deinterlace for
    both, the expensive part on 1080i sources) and written there; returns the scenes data."""
    out.parent.mkdir(parents=True, exist_ok=True)
    gop = max(1, round(probe.fps))
    meta = scenes_out.with_suffix(".txt") if scenes_out else None
    args = ["-i", str(src)]
    if meta is None:
        args += ["-map", "0:v:0", "-vf", proxy_video_filter(probe, height)]
    else:
        meta.parent.mkdir(parents=True, exist_ok=True)
        head = f"{DEINTERLACE}," if probe.interlaced else ""
        graph = (
            f"[0:v:0]{head}split=2[pv][sv];"
            f"[pv]{','.join(_scale_filters(probe, height))}[proxy];"
            f"[sv]{scene_filter(scene_threshold, meta)}[scenes]"
        )
        args += ["-filter_complex", graph, "-map", "[proxy]"]
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
    if meta is not None:
        args += ["-map", "[scenes]", "-f", "null", "-"]
    run_ffmpeg(args, duration=probe.duration, on_progress=on_progress, timeout=timeout)
    _part(out).replace(out)
    if scenes_out is not None and meta is not None:
        return write_scenes(meta, scenes_out, probe, scene_threshold)
    return None


def make_sprite(
    proxy: Path, out: Path, info_out: Path, probe: ProbeResult, timeout: float | None = None
) -> dict:
    """One thumbnail per second, tiled SPRITE_COLUMNS wide into a single JPEG."""
    count = max(1, math.ceil(probe.duration))
    rows = math.ceil(count / SPRITE_COLUMNS)
    thumb_h = max(2, round(SPRITE_THUMB_WIDTH * probe.height / probe.width / 2) * 2)
    run_ffmpeg(
        [
            "-i", str(proxy), "-an",
            "-vf", f"fps=1,scale={SPRITE_THUMB_WIDTH}:{thumb_h},tile={SPRITE_COLUMNS}x{rows}",
            "-frames:v", "1", "-q:v", "5", str(_part(out)),
        ],
        timeout=timeout,
    )  # fmt: skip
    _part(out).replace(out)
    info = {
        "interval": 1,
        "count": count,
        "columns": SPRITE_COLUMNS,
        "rows": rows,
        "width": SPRITE_THUMB_WIDTH,
        "height": thumb_h,
    }
    info_out.write_text(json.dumps(info))
    return info


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
