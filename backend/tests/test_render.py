"""Final render (M6): frame-accurate cut, length, audio, loudness, interlaced and late-video sources."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.media.ffmpeg import probe
from app.render.render import RenderError, RenderOptions, audio_source, render_clip
from tests.fixtures.synthetic import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")

FPS = 25
W, H = 160, 120


def frame_coded_video(path: Path, seconds: float = 6.0, *, audio: bool = True, tracks: int = 1,
                      start_offset: float = 0.0, interlaced: bool = False, height: int = H) -> Path:
    """Every frame's brightness encodes its number: Y = 16 + 4 * (n mod 50) (two-second cycle)."""
    width = height * 4 // 3
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={FPS}:d={seconds},geq=lum='16+4*mod(N\\,50)':cb=128:cr=128"]  # fmt: skip
    for i in range(tracks if audio else 0):
        args += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * i}:sample_rate=48000:duration={seconds}"]
    args += ["-map", "0:v"] + [x for i in range(tracks if audio else 0) for x in ("-map", f"{i + 1}:a")]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-g", "12"]
    if interlaced:
        args += ["-flags", "+ildct+ilme", "-x264opts", "tff=1"]
    if audio:
        args += ["-c:a", "aac"]
    if start_offset:
        args += ["-output_ts_offset", str(start_offset)]
    subprocess.run([*args, str(path)], check=True)
    return path


def frames(path: Path) -> np.ndarray:
    """Mean luma of every frame (full-range grey)."""
    p = probe(path)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", "format=gray", "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, p.height, p.width)
    return arr.reshape(len(arr), -1).mean(axis=1)


def frame_number(luma: float, source_levels: np.ndarray) -> int:
    """Index (mod 50) of the source frame whose brightness is closest."""
    return int(np.argmin(np.abs(source_levels[:50] - luma)))


@pytest.mark.parametrize("k", [37, 63, 101])
def test_first_and_last_frame_are_the_reviewed_ones(tmp_path: Path, k: int):
    """DoD: the render starts on the frame the reviewer set and ends right before the end."""
    src = frame_coded_video(tmp_path / "src.mp4")
    levels = frames(src)
    start, end = k / FPS, (k + 40) / FPS  # 40 frames
    result = render_clip(src, probe(src), start, end, tmp_path / "final.mp4")
    out = frames(result.path)
    assert len(out) == 40
    assert frame_number(out[0], levels) == k % 50
    assert frame_number(out[-1], levels) == (k + 39) % 50
    assert abs(result.duration - 1.6) <= 0.1
    assert probe(result.path).audio


def test_times_are_on_the_file_start_axis(tmp_path: Path):
    """A recording whose timestamps do not start at 0: a clip at t is the frame at t from the file start."""
    src = frame_coded_video(tmp_path / "src.ts", start_offset=1.4)
    levels = frames(src)
    p = probe(src)
    assert p.start_time > 1.0  # timestamps do not start at 0 (TS adds its own delay too)
    t = 30 / FPS
    out = frames(render_clip(src, p, t, 50 / FPS, tmp_path / "final.mp4").path)
    # The frame on screen at t (the video starts p.video_start after the file start, here 0.02 s).
    on_screen = int((t - p.video_start) * FPS + 1e-6)
    assert p.video_start > 0 and on_screen == 29
    assert frame_number(out[0], levels) == on_screen % 50


def test_loudness_is_normalised(tmp_path: Path):
    src = frame_coded_video(tmp_path / "src.mp4")
    result = render_clip(src, probe(src), 1.0, 5.0, tmp_path / "final.mp4", options=RenderOptions(loudness=-14.0))
    assert result.loudness is not None and float(result.loudness["input_i"]) < -14.5  # the sine was quieter
    stderr = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(result.path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr  # fmt: skip
    measured = json.loads(stderr[stderr.rindex("{") : stderr.rindex("}") + 1])
    assert float(measured["input_i"]) == pytest.approx(-14.0, abs=1.0)


def test_no_audio_and_audio_tracks(tmp_path: Path):
    silent_src = frame_coded_video(tmp_path / "noaudio.mp4", audio=False)
    result = render_clip(silent_src, probe(silent_src), 0.0, 2.0, tmp_path / "a.mp4")
    assert not probe(result.path).audio and result.loudness is None
    two = frame_coded_video(tmp_path / "two.mp4", tracks=2)
    p = probe(two)
    assert len(p.audio) == 2
    assert audio_source(p, 1)[0].startswith("[0:a:1]")
    assert "amix=inputs=2" in audio_source(p, None)[0]
    assert len(probe(render_clip(two, p, 0.0, 2.0, tmp_path / "b.mp4", track=1).path).audio) == 1
    with pytest.raises(RenderError):
        render_clip(two, p, 0.0, 2.0, tmp_path / "c.mp4", track=5)


def test_size_limit_interlacing_and_fades(tmp_path: Path):
    src = frame_coded_video(tmp_path / "big.mp4", height=240, interlaced=True)
    p = probe(src)
    opts = RenderOptions(max_height=120, fade_in=0.2, fade_out=0.3)
    result = render_clip(src, p, 1.0, 3.0, tmp_path / "final.mp4", options=opts)
    assert result.height == 120 and abs(result.duration - 2.0) <= 0.1
    out = frames(result.path)
    assert out[0] < out[10] - 5  # faded in from black


def test_bad_range(tmp_path: Path):
    src = frame_coded_video(tmp_path / "src.mp4", seconds=2.0)
    with pytest.raises(RenderError):
        render_clip(src, probe(src), 1.0, 1.0, tmp_path / "x.mp4")


@pytest.mark.parametrize("name,offset", [("src.mp4", 0.0), ("src.ts", 1.4)])
def test_thumbnail_frame_is_the_render_first_frame(tmp_path: Path, name: str, offset: float):
    """A frame picked in review (thumbnail) and the render's first frame are the same picture."""
    from PIL import Image

    from app.thumbnails.candidates import extract_frame

    src = frame_coded_video(tmp_path / name, start_offset=offset)
    levels = frames(src)
    p = probe(src)
    t = 2.36
    first = frames(render_clip(src, p, t, t + 1.0, tmp_path / "final.mp4").path)[0]
    shot = extract_frame(src, p, t, tmp_path / "thumb.jpg")
    with Image.open(shot) as img:
        gray = np.asarray(img.convert("L"), dtype=float)
    picture = gray[gray > 0].mean()  # leave out the pillarbox bars
    assert frame_number(first, levels) == frame_number(picture, levels) == int((t - p.video_start) * FPS) % 50
