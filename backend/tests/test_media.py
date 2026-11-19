"""FFmpeg wrapper, proxy and analysis on synthetic video (plan 1.4–1.7)."""

from __future__ import annotations

import json
import subprocess
import wave

import pytest

from app.media import analysis, proxy
from app.media.ffmpeg import FfmpegError, ProbeResult, probe, run_ffmpeg
from tests.fixtures.synthetic import Segment, ffmpeg_available, make_synthetic_video

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


# --- ffprobe / ffmpeg wrapper ---------------------------------------------------------------


def test_probe(synthetic_video):
    p = probe(synthetic_video.path)
    assert (p.width, p.height) == (320, 240)
    assert p.fps == 25 and p.fps_rational == "25/1"
    assert p.duration == pytest.approx(synthetic_video.duration, abs=0.1)
    assert len(p.audio) == 1 and p.audio[0].channels == 1
    assert not p.interlaced


def test_probe_rejects_non_video(tmp_path):
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(FfmpegError):
        probe(bad)


def test_run_ffmpeg_reports_progress(synthetic_video, tmp_path):
    seen = []
    run_ffmpeg(
        ["-i", str(synthetic_video.path), "-f", "null", "-"],
        duration=synthetic_video.duration,
        on_progress=seen.append,
        progress_interval=0,
    )
    assert seen and seen[-1] == 1.0
    assert all(0 <= f <= 1 for f in seen)


def test_run_ffmpeg_error_has_message(tmp_path):
    with pytest.raises(FfmpegError) as e:
        run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), "-f", "null", "-"])
    assert "No such file" in str(e.value)


def test_missing_binaries_raise_ffmpeg_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))  # no ffmpeg/ffprobe on PATH
    with pytest.raises(FfmpegError, match="not installed"):
        probe(tmp_path / "x.mp4")
    with pytest.raises(FfmpegError, match="not installed"):
        run_ffmpeg(["-version"])


def test_run_ffmpeg_timeout():
    with pytest.raises(FfmpegError, match="timed out"):
        run_ffmpeg(["-f", "lavfi", "-i", "sine=duration=600", "-f", "null", "-"], timeout=0.5)


# --- proxy ----------------------------------------------------------------------------------


def _keyframe_times(path) -> list[float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [float(x.strip(",")) for x in out.split() if x.strip(",")]


def test_proxy_keeps_time_axis_and_has_one_second_gop(synthetic_video, tmp_path):
    p = probe(synthetic_video.path)
    out = tmp_path / "proxy.mp4"
    seen = []
    proxy.make_proxy(synthetic_video.path, out, p, audio_order=0, on_progress=seen.append)
    q = probe(out)
    assert q.fps_rational == p.fps_rational
    assert q.duration == pytest.approx(p.duration, abs=0.1)
    assert q.start_time == pytest.approx(0, abs=0.05)
    assert q.video_codec == "h264" and q.audio[0].codec == "aac"
    keys = _keyframe_times(out)
    assert keys[:4] == pytest.approx([0, 1, 2, 3], abs=0.05)
    assert not out.with_name("proxy.part.mp4").exists()


def test_proxy_filter_scales_and_deinterlaces():
    base = dict(format_name="mxf", duration=10, start_time=0, fps=29.97, fps_rational="30000/1001",
                video_codec="mpeg2video", audio=[])  # fmt: skip
    hd = ProbeResult(width=1920, height=1080, field_order="tt", **base)
    assert proxy.proxy_video_filter(hd, 360).startswith("yadif=mode=send_frame")
    assert "scale=-2:360" in proxy.proxy_video_filter(hd, 360)
    small = ProbeResult(width=320, height=240, field_order="progressive", **base)
    f = proxy.proxy_video_filter(small, 360)
    assert "yadif" not in f and "scale=-2:360" not in f


def test_sprite_from_original_and_waveform(tmp_path):
    """The sprite comes from the original frames in the proxy pass, not from the 360p proxy."""
    v = make_synthetic_video(tmp_path / "hd.mp4", width=1280, height=720)
    p = probe(v.path)
    prx = tmp_path / "proxy.mp4"
    proxy.make_proxy(
        v.path, prx, p, audio_order=0,
        sprite_out=tmp_path / "sprite.jpg", sprite_info_out=tmp_path / "sprite.json",
    )  # fmt: skip
    assert (probe(prx).width, probe(prx).height) == (640, 360)
    info = json.loads((tmp_path / "sprite.json").read_text())
    assert info == proxy.sprite_layout(p)
    assert info["count"] == 8 and info["columns"] == 10 and info["rows"] == 1
    assert (info["width"], info["height"]) == (160, 90)  # original aspect ratio
    dims = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(tmp_path / "sprite.jpg")],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert dims == f"{10 * info['width']},{info['height']}"
    assert not (tmp_path / "sprite.part.jpg").exists()

    wf = proxy.make_waveform(prx, tmp_path / "waveform.json", has_audio=True)
    peaks = wf["peaks"]
    assert len(peaks) == pytest.approx(10 * v.duration, abs=2)
    # The 5–6 s segment is silent, the first 2 s are a tone.
    assert max(peaks[52:58]) < 0.02
    assert min(peaks[2:18]) > 0.05  # lavfi sine amplitude is 1/8
    assert json.loads((tmp_path / "waveform.json").read_text())["rate"] == 10


# --- analysis -------------------------------------------------------------------------------


def test_align_audio_is_16k_mono(synthetic_video, tmp_path):
    p = probe(synthetic_video.path)
    out = tmp_path / "align.wav"
    used = analysis.extract_align_audio(synthetic_video.path, out, p, track=None)
    assert used == "track 0"
    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() / 16000 == pytest.approx(synthetic_video.duration, abs=0.1)
    with pytest.raises(FfmpegError, match="track 3"):
        analysis.extract_align_audio(synthetic_video.path, out, p, track=3)


def test_align_audio_mixes_multiple_tracks(tmp_path):
    src = tmp_path / "two_tracks.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x120:r=25:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
         "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-c:a", "aac", str(src)],
        check=True,
    )  # fmt: skip
    p = probe(src)
    assert len(p.audio) == 2
    used = analysis.extract_align_audio(src, tmp_path / "a.wav", p, track=None)
    assert used == "mix of 2 tracks"
    assert analysis.extract_align_audio(src, tmp_path / "b.wav", p, track=1) == "track 1"


def test_scene_cuts(synthetic_video, tmp_path):
    p = probe(synthetic_video.path)
    data = analysis.detect_scenes(synthetic_video.path, tmp_path / "scenes.json", p, threshold=0.1)
    times = [c["time"] for c in data["cuts"]]
    assert times == pytest.approx(synthetic_video.cuts, abs=1 / synthetic_video.fps)
    assert [c["frame"] for c in data["cuts"]] == [round(t * 25) for t in synthetic_video.cuts]
    assert all(c["score"] > 0.1 for c in data["cuts"])
    assert not (tmp_path / "scenes.txt").exists()


def test_silences(synthetic_video, tmp_path):
    p = probe(synthetic_video.path)
    wav = tmp_path / "align.wav"
    analysis.extract_align_audio(synthetic_video.path, wav, p, track=None)
    data = analysis.detect_silences(wav, tmp_path / "silences.json", p.duration)
    got = [(s["start"], s["end"]) for s in data["silences"]]
    assert len(got) == len(synthetic_video.silences)
    for (gs, ge), (es, ee) in zip(got, synthetic_video.silences, strict=True):
        assert gs == pytest.approx(es, abs=0.05) and ge == pytest.approx(ee, abs=0.05)


def test_times_start_at_zero_when_source_timestamps_do_not(tmp_path):
    """Broadcast files (TS/MXF) often start at a non-zero timestamp. Cuts must still be measured
    from the first frame, like the proxy."""
    segs = (Segment("navy", 2.0, 440), Segment("darkred", 2.0, None), Segment("darkgreen", 2.0, 660))
    v = make_synthetic_video(tmp_path / "offset.ts", segs, start_offset=10.0)
    p = probe(v.path)
    assert p.start_time > 9
    data = analysis.detect_scenes(v.path, tmp_path / "scenes.json", p)
    expected = [t + p.video_start for t in v.cuts]
    assert [c["time"] for c in data["cuts"]] == pytest.approx(expected, abs=0.002)
    prx = tmp_path / "proxy.mp4"
    proxy.make_proxy(v.path, prx, p, audio_order=0)
    assert probe(prx).start_time == pytest.approx(0, abs=0.05)
    wav = tmp_path / "align.wav"
    analysis.extract_align_audio(v.path, wav, p, track=None)
    sil = analysis.detect_silences(wav, tmp_path / "silences.json", p.duration)["silences"]
    assert sil[0]["start"] == pytest.approx(2.0, abs=0.1)


def test_parsers():
    text = "frame:0    pts:50      pts_time:2\nlavfi.scene_score=0.95\nframe:1 pts:125 pts_time:5.04\nlavfi.scene_score=0.4\n"
    assert analysis.parse_scene_metadata(text) == [(2.0, 0.95), (5.04, 0.4)]
    stderr = "[silencedetect @ 0x1] silence_start: 1.5\n[silencedetect @ 0x1] silence_end: 2.25 | silence_duration: 0.75\n[silencedetect @ 0x1] silence_start: 9\n"
    assert analysis.parse_silencedetect(stderr, 10.0) == [(1.5, 2.25), (9.0, 10.0)]


def test_proxy_pass_detects_same_cuts(tmp_path):
    """Scene cuts found during the proxy encode (what the pipeline does) match a separate pass,
    including for an offset-timestamp source."""
    segs = (Segment("navy", 2.0, 440), Segment("white", 1.6, None), Segment("darkgreen", 2.0, 660))
    v = make_synthetic_video(tmp_path / "offset.ts", segs, start_offset=5.0)
    p = probe(v.path)
    combined = proxy.make_proxy(
        v.path, tmp_path / "proxy.mp4", p, audio_order=0, scenes_out=tmp_path / "scenes.json"
    )
    separate = analysis.detect_scenes(v.path, tmp_path / "scenes2.json", p)
    assert combined is not None
    # In this TS the video stream starts a little after the audio; times are measured from the
    # file start, so cuts are shifted by that amount.
    expected = [t + p.video_start for t in v.cuts]
    assert [c["time"] for c in combined["cuts"]] == pytest.approx(expected, abs=0.002)
    assert [c["frame"] for c in combined["cuts"]] == [c["frame"] for c in separate["cuts"]]
    assert json.loads((tmp_path / "scenes.json").read_text()) == combined
    assert probe(tmp_path / "proxy.mp4").duration == pytest.approx(p.duration, abs=0.1)
