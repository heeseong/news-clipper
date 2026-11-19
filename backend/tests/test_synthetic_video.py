"""The synthetic test video has the streams, cuts and silences it claims to have.

M1 (scene/silence analysis) builds its tests on this fixture.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests.fixtures.synthetic import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def ffprobe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def test_streams_and_duration(synthetic_video):
    info = ffprobe(synthetic_video.path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (synthetic_video.width, synthetic_video.height)
    assert video["r_frame_rate"] == f"{synthetic_video.fps}/1"
    assert audio["channels"] == 1
    assert float(info["format"]["duration"]) == pytest.approx(synthetic_video.duration, abs=0.1)


def test_scene_cuts_detectable(synthetic_video):
    # Same threshold the PoC uses for anchor-shot cuts.
    run = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(synthetic_video.path),
            "-vf", "select='gt(scene,0.1)',showinfo", "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    found = [float(t) for t in re.findall(r"pts_time:([\d.]+)", run.stderr)]
    assert found == pytest.approx(synthetic_video.cuts, abs=1 / synthetic_video.fps)


def test_silences_detectable(synthetic_video):
    run = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(synthetic_video.path),
            "-af", "silencedetect=noise=-50dB:d=0.3", "-vn", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    starts = [float(t) for t in re.findall(r"silence_start: ([\d.]+)", run.stderr)]
    ends = [float(t) for t in re.findall(r"silence_end: ([\d.]+)", run.stderr)]
    expected = synthetic_video.silences
    assert starts == pytest.approx([s for s, _ in expected], abs=0.05)
    assert ends == pytest.approx([e for _, e in expected], abs=0.05)
