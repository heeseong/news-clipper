"""Clip review (M5): edits, locks, thumbnails, approval, bulk actions, audit, re-run protection.

Uses the synthetic broadcast with a source video from test_thumbnails (three clips with ranges).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from PIL import Image

from app.core.db import session_scope
from app.models import AlignedSentence, Clip, ClipMetadata, Thumbnail
from app.pipeline import tasks
from app.review import service as review
from app.storage import JobFiles
from tests.test_align import aligned_job  # noqa: F401  (fixture)
from tests.test_thumbnails import job_with_source  # noqa: F401  (fixture)

A = {"X-Reviewer": quote("김검수")}
B = {"X-Reviewer": quote("이편집")}


@pytest.fixture
def reviewed(job_with_source, client):  # noqa: F811
    job_id, b = job_with_source
    tasks.start_job(job_id)
    clips = client.get(f"/api/jobs/{job_id}/clips").json()
    return job_id, b, {c["item_no"]: c["id"] for c in clips}


def detail(client, clip_id: int) -> dict:
    return client.get(f"/api/clips/{clip_id}").json()


def test_reviewer_name_is_required(reviewed, client):
    _, _, ids = reviewed
    r = client.patch(f"/api/clips/{ids[1]}", json={"title": "x"})
    assert r.status_code == 400 and "검수자" in r.json()["detail"]


def test_lock_blocks_other_reviewers_until_it_expires(reviewed, client):
    _, _, ids = reviewed
    cid = ids[1]
    assert client.post(f"/api/clips/{cid}/lock", headers=A).status_code == 200
    assert detail(client, cid)["locked_by_name"] == "김검수"
    r = client.patch(f"/api/clips/{cid}", json={"title": "다른 사람"}, headers=B)
    assert r.status_code == 423 and "김검수" in r.json()["detail"]
    assert client.post(f"/api/clips/{cid}/lock", headers=B).status_code == 423
    assert client.patch(f"/api/clips/{cid}", json={"title": "새 제목"}, headers=A).status_code == 200
    with session_scope() as s:  # the editor closed without unlocking: the lock runs out
        s.get(Clip, cid).locked_at = datetime.now(UTC) - timedelta(seconds=review.LOCK_SECONDS + 1)
    assert client.post(f"/api/clips/{cid}/lock", headers=B).status_code == 200
    assert client.delete(f"/api/clips/{cid}/lock", headers=B).status_code == 204
    assert detail(client, cid)["locked_by_name"] is None


def test_range_edit_is_manual_audited_and_remakes_thumbnails(reviewed, client):
    _, _, ids = reviewed
    cid = ids[1]
    before = detail(client, cid)
    old_candidates = {t["id"] for t in before["thumbnails"]}
    start, end = before["start_sec"] + 0.5, before["end_sec"] - 0.5
    r = client.patch(f"/api/clips/{cid}", json={"start_sec": start, "end_sec": end}, headers=A)
    assert r.status_code == 200, r.text
    after = detail(client, cid)
    assert (after["start_sec"], after["end_sec"]) == (round(start, 3), round(end, 3))
    assert after["start_snap"] == after["end_snap"] == "manual" and after["edited_at"]
    thumbs = after["thumbnails"]
    assert thumbs and not old_candidates & {t["id"] for t in thumbs}  # made again (eager Celery)
    assert all(start <= t["frame_sec"] <= end for t in thumbs)
    audit = client.get(f"/api/clips/{cid}/audit").json()
    assert audit[0]["action"] == "range" and audit[0]["user_name"] == "김검수"
    for bad in ({"start_sec": end, "end_sec": start}, {"start_sec": -1, "end_sec": 5}, {"start_sec": 1}):
        assert client.patch(f"/api/clips/{cid}", json=bad, headers=A).status_code in (409, 422)


def test_manual_range_for_a_clip_the_alignment_could_not_place(reviewed, client):
    """A NOT_FOUND clip gets metadata and thumbnails once the reviewer sets its range."""
    job_id, b, ids = reviewed
    cid = ids[3]
    with session_scope() as s:
        clip = s.get(Clip, cid)
        start, end = clip.start_sec, clip.end_sec
        clip.start_sec = clip.end_sec = None
        clip.status = "NOT_FOUND"
        s.query(ClipMetadata).filter_by(clip_id=cid).delete()
        s.query(Thumbnail).filter_by(clip_id=cid).delete()
    assert detail(client, cid)["approval_problems"][0] == "구간이 정해지지 않았습니다"
    r = client.patch(f"/api/clips/{cid}", json={"start_sec": start, "end_sec": end}, headers=A)
    assert r.status_code == 200, r.text
    d = detail(client, cid)
    assert d["status"] == "DRAFT" and d["metadata"]["title"].startswith("섬마을 의료")
    assert d["thumbnails"] and d["thumbnail_url"]


def test_metadata_edits(reviewed, client):
    _, _, ids = reviewed
    cid = ids[1]
    thumb = JobFiles(reviewed[0]).thumbnail(cid)
    mtime = thumb.stat().st_mtime_ns
    body = {"title": "  고친   제목 ", "tags": ["목포", " 목포", "다리"], "hashtags": ["새 다리", "#목포"],
            "thumbnail_text": "새 문구\n둘째 줄", "privacy": "public"}
    r = client.patch(f"/api/clips/{cid}", json=body, headers=A)
    assert r.status_code == 200, r.text
    meta = r.json()["metadata"]
    assert meta["title"] == "고친 제목" and meta["tags"] == ["목포", "다리"]
    assert meta["hashtags"] == ["#새다리", "#목포"] and meta["privacy"] == "public"
    assert thumb.stat().st_mtime_ns != mtime  # the overlay is drawn again with the new text
    assert client.patch(f"/api/clips/{cid}", json={"privacy": "secret"}, headers=A).status_code == 409
    diff = client.get(f"/api/clips/{cid}/audit").json()[0]["diff"]
    assert diff["title"][1] == "고친 제목"


def test_thumbnail_choices(reviewed, client):
    job_id, _, ids = reviewed
    cid = ids[1]
    d = detail(client, cid)
    other = d["thumbnails"][-1]
    r = client.post(f"/api/clips/{cid}/thumbnails/{other['id']}/select", headers=A)
    assert [t["id"] for t in r.json()["thumbnails"] if t["selected"]] == [other["id"]]
    # The player's current frame, from the original.
    t = d["start_sec"] + 1.0
    r = client.post(f"/api/clips/{cid}/thumbnails", json={"frame_sec": t}, headers=A)
    assert r.status_code == 200, r.text
    frame = next(x for x in r.json()["thumbnails"] if x["selected"])
    assert frame["source"] == "frame" and frame["frame_sec"] == pytest.approx(t)
    # An uploaded picture (4:3) is cropped to 16:9.
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(buf, "PNG")
    r = client.put(f"/api/clips/{cid}/thumbnails/upload", content=buf.getvalue(), headers=A)
    assert r.status_code == 200, r.text
    up = next(x for x in r.json()["thumbnails"] if x["selected"])
    with session_scope() as s:
        with Image.open(s.get(Thumbnail, up["id"]).path) as img:
            assert img.size == (1280, 720)
    assert client.put(f"/api/clips/{cid}/thumbnails/upload", content=b"nope", headers=A).status_code == 422
    # Preview with another text, nothing saved.
    r = client.get(f"/api/clips/{cid}/thumbnail-preview", params={"text": "미리보기"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    # Remaking candidates keeps the reviewer's own pictures and their choice.
    client.post(f"/api/clips/{cid}/thumbnails/regenerate", headers=A)
    after = detail(client, cid)["thumbnails"]
    assert {x["source"] for x in after} == {"candidate", "frame", "upload"}
    assert next(x for x in after if x["selected"])["id"] == up["id"]


def test_approval_rules(reviewed, client):
    _, _, ids = reviewed
    cid = ids[1]
    with session_scope() as s:
        s.get(Clip, cid).flags = ["OVERLAP"]
    r = client.post(f"/api/clips/{cid}/approve", headers=A)
    assert r.status_code == 422 and r.json()["detail"]["problems"] == ["경고를 확인하지 않았습니다"]
    client.patch(f"/api/clips/{cid}", json={"warnings_acknowledged": True, "title": "가" * 101}, headers=A)
    r = client.post(f"/api/clips/{cid}/approve", headers=A)
    assert "제목이 100자를 넘습니다 (101자)" in r.json()["detail"]["problems"]
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    client.patch(f"/api/clips/{cid}", json={"title": "좋은 제목", "publish_at": past}, headers=A)
    assert client.post(f"/api/clips/{cid}/approve", headers=A).json()["detail"]["problems"] == ["예약 시각이 지났습니다"]
    client.patch(f"/api/clips/{cid}", json={"clear_publish_at": True}, headers=A)
    r = client.post(f"/api/clips/{cid}/approve", headers=A)
    # Approval starts the final render (Celery runs in-process here, so it is already done).
    assert r.status_code == 200 and r.json()["status"] == "RENDERED" and r.json()["reviewed_at"]
    assert client.patch(f"/api/clips/{cid}", json={"title": "또"}, headers=A).status_code == 409
    assert client.post(f"/api/clips/{cid}/reopen", headers=A).json()["status"] == "DRAFT"
    r = client.post(f"/api/clips/{cid}/reject", json={"reason": "중복 기사"}, headers=A)
    assert r.json()["status"] == "REJECTED" and r.json()["reject_reason"] == "중복 기사"
    actions = [a["action"] for a in client.get(f"/api/clips/{cid}/audit").json()]
    assert actions[:3] == ["reject", "reopen", "approve"]


def test_bulk_approve_and_schedule(reviewed, client):
    job_id, _, ids = reviewed
    all_ids = [ids[1], ids[2], ids[3]]
    first = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    r = client.post("/api/clips/bulk", headers=A, json={
        "clip_ids": list(reversed(all_ids)), "action": "schedule", "first_at": first.isoformat(),
        "interval_minutes": 10, "privacy": "public"})  # fmt: skip
    assert r.status_code == 200, r.text
    times = {x["clip_id"]: x["publish_at"] for x in r.json()}
    assert [datetime.fromisoformat(times[i]) for i in all_ids] == [first + timedelta(minutes=10 * k) for k in range(3)]
    with session_scope() as s:
        s.get(Clip, ids[2]).flags = ["NO_SIGNOFF"]
    r = client.post("/api/clips/bulk", headers=A, json={"clip_ids": all_ids, "action": "approve"})
    res = {x["clip_id"]: x for x in r.json()}
    assert res[ids[1]]["ok"] and res[ids[3]]["ok"]
    assert not res[ids[2]]["ok"] and res[ids[2]]["problems"] == ["경고를 확인하지 않았습니다"]
    counts = client.get("/api/jobs").json()["items"][0]["clip_counts"]
    assert counts == {"total": 3, "ranged": 3, "approved": 2, "rejected": 0}


def test_reruns_ask_before_dropping_reviewed_clips(reviewed, client):
    job_id, _, ids = reviewed
    assert client.post(f"/api/jobs/{job_id}/steps/align/retry").status_code == 200  # nothing reviewed yet
    ids = {c["item_no"]: c["id"] for c in client.get(f"/api/jobs/{job_id}/clips").json()}
    client.patch(f"/api/clips/{ids[1]}", json={"title": "검수자가 고친 제목"}, headers=A)
    r = client.post(f"/api/jobs/{job_id}/steps/align/retry")
    assert r.status_code == 409 and r.json()["detail"]["reviewed_clips"] == [ids[1]]
    assert client.post(f"/api/jobs/{job_id}/rundown/refresh", json={}).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/steps/align/retry?force=true").status_code == 200
    titles = [c["youtube_title"] for c in client.get(f"/api/jobs/{job_id}/clips").json()]
    assert "검수자가 고친 제목" not in titles
    with session_scope() as s:
        assert s.query(AlignedSentence).count() > 0
    assert Path(JobFiles(job_id).clips_dir).exists()


def test_approval_renders_and_reopening_discards_it(reviewed, client, monkeypatch):
    """M6: approve → RENDERED with a playable and downloadable file; a failure can be retried;
    reopening removes the render."""
    from app.render import render as render_mod

    job_id, _, ids = reviewed
    cid = ids[1]
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    assert d["status"] == "RENDERED" and d["render_url"] and d["rendered_at"] and d["render_error"] is None
    final = JobFiles(job_id).final(cid)
    assert final.exists()
    r = client.get(d["render_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    r = client.get(f"/media/clips/{cid}/final.mp4", params={"download": 1})
    assert "attachment" in r.headers["content-disposition"]
    with session_scope() as s:
        clip = s.get(Clip, cid)
        from app.media.ffmpeg import probe

        assert abs(probe(final).duration - (clip.end_sec - clip.start_sec)) <= 0.1
    assert client.get("/api/jobs").json()["items"][0]["clip_counts"]["approved"] == 1
    # Rendered clips are reviewed clips: a re-run asks first.
    assert client.post(f"/api/jobs/{job_id}/steps/align/retry").status_code == 409

    # A failed render goes back to APPROVED with the reason; "render" tries again.
    def broken(*a, **k):
        raise render_mod.RenderError("렌더 길이가 다릅니다")

    monkeypatch.setattr(render_mod, "render_clip", broken)
    d = client.post(f"/api/clips/{cid}/render", headers=A).json()
    assert d["status"] == "APPROVED" and "길이" in d["render_error"]
    monkeypatch.undo()
    d = client.post(f"/api/clips/{cid}/render", headers=A).json()
    assert d["status"] == "RENDERED" and d["render_error"] is None

    # Reopening (to change the range) throws the render away.
    d = client.post(f"/api/clips/{cid}/reopen", headers=A).json()
    assert d["status"] == "DRAFT" and d["render_url"] is None and not final.exists()
    assert client.get(f"/media/clips/{cid}/final.mp4").status_code == 404
    assert client.post(f"/api/clips/{cid}/render", headers=A).status_code == 409
