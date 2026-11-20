"""Thumbnails (M4): candidate times, scoring, frame extraction from the original, overlay, and the
metadata + thumbnails steps on a synthetic broadcast."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.core.db import session_scope
from app.media.ffmpeg import probe
from app.metadata import factory as llm_factory
from app.models import AlignedSentence, Clip, ClipMetadata, Job, JobStatus, StepStatus, Thumbnail
from app.pipeline import handlers, tasks
from app.pipeline import steps as step_registry
from app.storage import JobFiles
from app.thumbnails import overlay
from app.thumbnails.candidates import (
    Candidate,
    anchor_end_for,
    candidate_times,
    extract_frame,
    measure,
    pick,
    score,
)
from tests.fixtures.synthetic import Segment, make_synthetic_video
from tests.test_align import aligned_job  # noqa: F401  (fixture)

# --- candidate times and picking ---


def test_candidates_follow_cuts_and_skip_blends():
    cands = candidate_times(10.0, 40.0, [5.0, 12.0, 20.0, 20.2, 35.0, 45.0], anchor_end=12.0, samples=6)
    times = [c.time for c in cands]
    assert 12.5 in times and 35.5 in times  # just after cuts inside the clip
    assert all(abs(t - c) >= 0.15 for t in times for c in (12.0, 20.0, 20.2, 35.0))
    assert all(10.0 <= t <= 40.0 for t in times)
    assert all(b - a >= 0.25 for a, b in zip(times, times[1:], strict=False))
    assert all(c.in_anchor == (c.time < 12.0) for c in cands)
    assert {c.shot for c in cands if c.time > 35} == {4}


def test_anchor_part_of_report_and_brief():
    cuts = [100.0, 115.2, 130.0]
    assert anchor_end_for("report", 100.0, 200.0, cuts, lead_end=114.8) == 115.2  # report starts on a cut
    assert anchor_end_for("report", 100.0, 200.0, cuts, lead_end=120.0) == 120.0  # no cut nearby
    assert anchor_end_for("brief", 100.04, 140.0, cuts, lead_end=None) == 115.2  # the first shot
    assert anchor_end_for("brief", 130.04, 140.0, cuts, lead_end=None) == 140.0  # anchor only


def test_pick_prefers_different_shots_and_penalizes_the_anchor():
    cands = [Candidate(t, shot, shot == 0) for t, shot in [(1, 0), (2, 0), (5, 1), (6, 1), (9, 2)]]
    good = {"laplacian": 500.0, "brightness": 120.0, "contrast": 50.0, "face_area": 0.0}
    dark = {"laplacian": 500.0, "brightness": 5.0, "contrast": 2.0, "face_area": 0.0}
    face = good | {"face_area": 0.05}
    score(cands, [face, face, good, good | {"laplacian": 100.0}, dark])
    top = pick(cands, 3)
    assert [c.time for c in top] == [5, 1, 9]  # one per shot first: report > anchor (with face) > dark
    assert top[0].details["anchor"] == 0.0 and top[1].details["anchor"] == 1.0
    assert [c.time for c in pick(cands, 5)][3:] == [6, 2]  # a softer report frame beats the anchor


# --- overlay (4.5) ---


@pytest.fixture
def frame(tmp_path: Path) -> Path:
    rng = np.random.default_rng(0)
    img = Image.fromarray(rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8))  # incompressible
    path = tmp_path / "frame.jpg"
    img.save(path, quality=95)
    return path


def test_overlay_is_1280x720_jpeg_under_2mb(frame: Path, tmp_path: Path):
    out = overlay.render_to(frame, "압해도 새 다리 개통", tmp_path / "thumb.jpg")
    assert out.stat().st_size <= overlay.MAX_BYTES
    with Image.open(out) as img:
        assert img.size == (1280, 720) and img.format == "JPEG"


def test_overlay_wraps_long_text_and_draws_at_the_bottom(tmp_path: Path):
    grey = tmp_path / "grey.jpg"
    Image.new("RGB", (1280, 720), (128, 128, 128)).save(grey)
    font, lines = overlay.layout("섬마을 응급 의료 공백 여전히 심각한 상황", overlay.template_with_defaults(None))
    assert 1 < len(lines) <= 2
    img = np.asarray(overlay.render(grey, "의료 공백"))
    assert np.abs(img[:200].astype(int) - 128).max() < 10  # top untouched
    assert img[600:].min() < 30 and img[600:].max() > 230  # white text with a black stroke


def test_overlay_template_top_left_and_logo(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(overlay, "branding_dir", lambda: tmp_path)
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(tmp_path / "logo.png")
    grey = tmp_path / "grey.jpg"
    Image.new("RGB", (1280, 720), (128, 128, 128)).save(grey)
    tpl = {"position": "top", "align": "left", "shade": 0, "logo": "logo.png", "logo_position": "bottom-right"}
    img = np.asarray(overlay.render(grey, "속보", tpl))
    assert img[660, 1180, 0] > 200 and img[660, 1180, 1] < 60  # red logo bottom right
    assert img[60:180, 40:300].min() < 30  # text top left
    assert np.abs(img[400:500, 600:700].astype(int) - 128).max() < 10


def test_empty_text_leaves_the_frame(tmp_path: Path):
    grey = tmp_path / "grey.jpg"
    Image.new("RGB", (1280, 720), (128, 128, 128)).save(grey)
    assert np.abs(np.asarray(overlay.render(grey, "")).astype(int) - 128).max() < 10


# --- frames from the original ---


@pytest.mark.parametrize("offset", [0.0, 1.4])
def test_frame_extraction_uses_the_file_start_axis(tmp_path: Path, offset: float):
    from tests.fixtures.synthetic import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not installed")
    segs = (Segment("black", 2.0, 440), Segment("white", 2.0, 440), Segment("gray", 2.0, None))
    video = make_synthetic_video(tmp_path / "v.ts", segs, start_offset=offset)
    p = probe(video.path)
    for t, expect in ((1.0, 0), (2.5, 255), (4.5, 128)):
        out = extract_frame(video.path, p, t, tmp_path / f"{t}.jpg")
        with Image.open(out) as img:
            assert img.size == (1280, 720)
            assert abs(np.asarray(img.convert("L"))[300:400, 600:700].mean() - expect) < 25, t
    m = measure(tmp_path / "1.0.jpg", None)
    assert m["brightness"] < 20  # black frame (pillarbox bars of the 4:3 test video are ignored)


# --- the steps on a synthetic broadcast ---


@pytest.fixture
def job_with_source(aligned_job, tmp_path_factory, monkeypatch):  # noqa: F811
    """aligned_job plus a source video as long as its timeline, with the real thumbnails step."""
    from tests.fixtures.synthetic import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not installed")
    job_id, b, provider = aligned_job
    monkeypatch.setitem(step_registry._HANDLERS, "thumbnails", handlers.thumbnails_step)
    colors = ["navy", "darkred", "darkgreen", "gray", "olive", "teal", "purple", "maroon"]
    total = b.lp.duration
    edges = [0.0, *b.cuts, total]
    segs = tuple(
        Segment(colors[i % len(colors)], round(e - a, 3), 440) for i, (a, e) in enumerate(zip(edges, edges[1:], strict=False))
    )
    video = make_synthetic_video(tmp_path_factory.mktemp("src") / "source.mp4", segs)
    files = JobFiles(job_id)
    files.probe.parent.mkdir(parents=True, exist_ok=True)
    files.probe.write_text(probe(video.path).model_dump_json())
    with session_scope() as s:
        s.get(Job, job_id).source_path = str(video.path)
    llm_factory.set_provider(llm_factory.FakeLLMProvider())
    yield job_id, b
    llm_factory.set_provider()


def test_metadata_and_thumbnails_steps(job_with_source, client):
    job_id, b = job_with_source
    tasks.start_job(job_id)
    with session_scope() as s:
        job = s.get(Job, job_id)
        steps = {r.step: r.status for r in job.steps}
        assert steps["metadata"] == steps["thumbnails"] == StepStatus.SUCCEEDED
        assert job.status == JobStatus.REVIEW_READY
        clips = s.query(Clip).filter_by(job_id=job_id).all()
        ranged = [c for c in clips if not c.needs_manual_range]
        assert len(ranged) == 3
        for clip in ranged:
            meta = s.get(ClipMetadata, clip.id)
            assert meta.title == clip.rundown_item.title and meta.llm_status == "ok"
            assert meta.llm_provider == "fake"
            thumbs = s.query(Thumbnail).filter_by(clip_id=clip.id).order_by(Thumbnail.rank).all()
            assert 1 <= len(thumbs) <= 6 and [t.rank for t in thumbs] == list(range(1, len(thumbs) + 1))
            assert [t.selected for t in thumbs].count(True) == 1 and thumbs[0].selected
            assert all(clip.start_sec <= t.frame_sec <= clip.end_sec and Path(t.path).exists() for t in thumbs)
            assert thumbs[0].overlay_text == meta.thumbnail_text
            assert JobFiles(job_id).thumbnail(clip.id).exists()
        report = next(c for c in ranged if c.rundown_item.item_no == 1)
        report_id = report.id
        # The best frame is from the report part, not the anchor's lead.
        lead_end = max(x.end_sec for x in s.query(AlignedSentence).filter_by(
            rundown_item_id=report.rundown_item_id, section="lead"))
        best = s.query(Thumbnail).filter_by(clip_id=report_id, rank=1).one()
        assert best.details["anchor"] == 0.0 and best.frame_sec > lead_end

    clip = client.get(f"/api/clips/{report_id}").json()
    assert clip["metadata"]["title"] == "새 다리 개통"
    assert [c["source"] for c in clip["metadata"]["title_candidates"]][0] == "script"
    assert clip["thumbnail_url"].startswith(f"/media/clips/{report_id}/thumbnail.jpg")
    assert len(clip["thumbnails"]) >= 1
    r = client.get(clip["thumbnails"][0]["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = client.get(clip["thumbnail_url"])
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    listed = client.get(f"/api/jobs/{job_id}/clips").json()
    assert listed[0]["youtube_title"] == "새 다리 개통" and listed[0]["thumbnail_url"]
    assert client.get(f"/api/clips/{report_id}/thumbnails").json()[0]["rank"] == 1

    # 4.6: regenerate (now without LLM) keeps working and replaces the candidates.
    llm_factory.set_provider(None)
    r = client.post(f"/api/clips/{report_id}/metadata/regenerate")
    assert r.status_code == 202
    meta = client.get(f"/api/clips/{report_id}").json()["metadata"]
    assert meta["llm_status"] == "off" and len(meta["title_candidates"]) == 1
    assert client.post("/api/clips/9999/metadata/regenerate").status_code == 404


def test_rerunning_align_replaces_metadata_and_files(job_with_source):
    job_id, _ = job_with_source
    tasks.start_job(job_id)
    stale = JobFiles(job_id).clip_dir(9999)
    stale.mkdir()
    with session_scope() as s:
        before = {t.path: t.updated_at for t in s.query(Thumbnail)}
    tasks.retry_step(job_id, "align")
    with session_scope() as s:
        clips = s.query(Clip).filter_by(job_id=job_id).all()
        assert s.query(ClipMetadata).count() == s.query(Clip).filter(Clip.start_sec.is_not(None)).count() == 3
        after = {t.path: t.updated_at for t in s.query(Thumbnail)}
        assert all(after[p] > before.get(p, after[p]) or p not in before for p in after)
        assert {t.clip_id for t in s.query(Thumbnail)} == {c.id for c in clips if c.start_sec is not None}
    assert not stale.exists()


def test_program_metadata_settings_api(client):
    r = client.post("/api/programs", json={"name": "뉴스투데이", "default_tags": [" 뉴스 ", "뉴스", ""],
                                           "description_footer": "{program} {date}",
                                           "thumbnail_template": {"position": "top"}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["default_tags"] == ["뉴스"] and body["thumbnail_template"] == {"position": "top"}
    r = client.put(f"/api/programs/{body['id']}", json={"name": "뉴스투데이", "thumbnail_template": {"colour": 1}})
    assert r.status_code == 422
    r = client.put(f"/api/programs/{body['id']}", json={"name": "뉴스투데이", "description_footer": ""})
    assert r.json()["description_footer"] is None and r.json()["default_tags"] == ["뉴스"]
    assert json.dumps(r.json(), ensure_ascii=False)
