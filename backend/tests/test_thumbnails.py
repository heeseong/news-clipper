"""Thumbnails (M4): candidate times, scoring, frame extraction from the original, overlay, and the
metadata + thumbnails steps on a synthetic broadcast."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

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


REPORT_T = overlay.template_for("report", None)
BRIEF_T = overlay.template_for("brief", None)


@pytest.fixture
def grey(tmp_path: Path) -> Path:
    path = tmp_path / "grey.jpg"
    Image.new("RGB", (1280, 720), (128, 128, 128)).save(path)
    return path


def test_overlay_is_1280x720_jpeg_under_2mb(frame: Path, tmp_path: Path):
    for tpl in (REPORT_T, BRIEF_T):
        out = overlay.render_to(frame, "압해도 새 다리\n오늘 개통", tmp_path / "thumb.jpg", tpl)
        assert out.stat().st_size <= overlay.MAX_BYTES
        with Image.open(out) as img:
            assert img.size == (1280, 720) and img.format == "JPEG"


def test_report_headline_two_lines_yellow_then_white(grey: Path):
    img = np.asarray(overlay.render(grey, "'고병원성 AI' 잇단 확진\n한 달 빨라진 발생", REPORT_T)).astype(int)
    lower = img[430:700, 40:900]
    yellow = (lower[..., 0] > 200) & (lower[..., 1] > 170) & (lower[..., 2] < 90)
    white = (lower > 220).all(axis=-1)
    ys, ws = np.where(yellow)[0], np.where(white)[0]
    assert len(ys) > 500 and len(ws) > 500
    assert ys.mean() < ws.mean()  # the yellow line is above the white one
    corner = img[10:60, 1180:1270]
    assert corner[..., 2].mean() > corner[..., 0].mean() + 40  # blue band behind the logo
    assert np.abs(img[150:250, 300:500] - 128).max() < 10  # the middle of the picture is untouched


def test_brief_thumbnail_is_the_frame_as_it_is(frame: Path):
    out = np.asarray(overlay.render(frame, "아무 문구", BRIEF_T)).astype(int)
    assert np.abs(out - np.asarray(Image.open(frame).convert("RGB")).astype(int)).max() == 0


def test_profile_template_keys(tmp_path: Path, grey: Path, monkeypatch):
    monkeypatch.setattr(overlay, "branding_dir", lambda: tmp_path)
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(tmp_path / "logo.png")
    profile = {"logo": "logo.png", "report": {"line_colors": ["#FFFFFF"]}}
    rep = overlay.template_for("report", profile)
    assert rep["logo"] == "logo.png" and rep["line_colors"] == ["#FFFFFF"] and rep["style"] == "headline"
    img = np.asarray(overlay.render(grey, "", rep)).astype(int)
    assert img[60, 1180, 0] > 200 and img[60, 1180, 1] < 60  # placeholder design with the logo file
    assert overlay.unknown_keys(profile) == []
    assert overlay.unknown_keys({"colour": 1, "brief": {"size": 2}, "report": 3}) == ["brief.size", "colour", "report"]


def test_report_template_image_replaces_the_placeholder(grey: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(overlay, "branding_dir", lambda: tmp_path)
    tpl_img = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    tpl_img.paste(Image.new("RGBA", (200, 100), (255, 0, 0, 255)), (1050, 30))  # a red "logo"
    tpl_img.save(tmp_path / "tpl.png")
    profile = {"report": {"overlay_image": "tpl.png", "text_box": [100, 300, 900, 200]}}
    t = overlay.template_for("report", profile)
    assert t["shade"] == 0  # the template carries its own design
    img = np.asarray(overlay.render(grey, "첫 줄\n둘째 줄", t)).astype(int)
    assert img[80, 1150, 0] > 200 and img[80, 1150, 1] < 60  # template on top of the frame
    assert img[10, 1270, 2] < 160  # no placeholder blue corner band
    lit = (img[:, 100:1000] > 200).all(axis=-1) | ((img[:, 100:1000, 0] > 200) & (img[:, 100:1000, 2] < 90))
    text_rows = np.where(lit.any(axis=1))[0]
    assert text_rows.min() >= 300 and text_rows.max() <= 520  # the text stays in its box
    assert overlay.template_for("report", {"report": {**profile["report"], "shade": 0.3}})["shade"] == 0.3
    assert overlay.template_for("brief", profile)["style"] == "frame"


def test_brief_candidates_favour_the_on_air_caption(tmp_path: Path):
    """Two equally good pictures; the one with the title caption at the bottom wins for a brief."""
    from app.thumbnails.overlay import FONT

    rng = np.random.default_rng(1)
    base = Image.fromarray(rng.integers(60, 200, (72, 128, 3), dtype=np.uint8)).resize((1280, 720), Image.BICUBIC)
    plain, captioned = tmp_path / "plain.jpg", tmp_path / "cap.jpg"
    base.save(plain)
    img = base.copy()
    d = ImageDraw.Draw(img)
    d.rectangle([60, 560, 1220, 640], fill="white")
    d.text((90, 572), "출장비 부풀리기 의혹 1년 만에…시의원 해외연수", font=ImageFont.truetype(str(FONT), 50), fill="#10204A")
    img.save(captioned)
    m_plain, m_cap = measure(plain, None), measure(captioned, None)
    assert m_cap["caption"] > 0.3 and m_plain["caption"] < 0.1
    brief = [Candidate(1.0, 0, False), Candidate(2.0, 1, False)]
    score(brief, [m_plain, m_cap], "brief")
    assert brief[1].score > brief[0].score + 0.25 and brief[1].details["caption"] > 0.3
    report = [Candidate(1.0, 0, False), Candidate(2.0, 1, False)]
    score(report, [m_plain, m_cap], "report")
    assert abs(report[1].score - report[0].score) < brief[1].score - brief[0].score


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
                                           "thumbnail_template": {"report": {"font_size": 80}}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["default_tags"] == ["뉴스"] and body["thumbnail_template"] == {"report": {"font_size": 80}}
    r = client.put(f"/api/programs/{body['id']}", json={"name": "뉴스투데이", "thumbnail_template": {"colour": 1}})
    assert r.status_code == 422
    r = client.put(f"/api/programs/{body['id']}", json={"name": "뉴스투데이", "description_footer": ""})
    assert r.json()["description_footer"] is None and r.json()["default_tags"] == ["뉴스"]
    assert json.dumps(r.json(), ensure_ascii=False)


def test_report_template_upload_api(client):
    import io

    pid = client.post("/api/programs", json={"name": "뉴스데스크"}).json()["id"]

    def png(size, alpha=0):
        buf = io.BytesIO()
        Image.new("RGBA", size, (0, 0, 255, alpha)).save(buf, "PNG")
        return buf.getvalue()

    url = f"/api/programs/{pid}/thumbnail-overlay"
    assert client.put(url, content=png((1920, 1080), 255)).status_code == 422  # no transparency
    assert client.put(url, content=png((1000, 1000))).status_code == 422  # not 16:9
    assert client.put(url, content=b"not an image").status_code == 422
    r = client.put(url, content=png((1920, 1080)), headers={"content-type": "image/png"})
    assert r.status_code == 200, r.text
    rel = r.json()["thumbnail_template"]["report"]["overlay_image"]
    with Image.open(overlay.branding_dir() / rel) as saved:
        assert saved.size == (1280, 720)
    preview = client.get(f"/api/programs/{pid}/thumbnail-preview")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    r = client.delete(url)
    assert "overlay_image" not in r.json()["thumbnail_template"]["report"]
    assert not (overlay.branding_dir() / rel).exists()
