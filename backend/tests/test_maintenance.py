"""M9: retention cleanup, operating metrics, program profile edits."""
# ruff: noqa: F811  (pytest fixtures imported from other test modules)

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.db import session_scope
from app.maintenance import cleanup
from app.models import Clip, ClipStatus, Job
from app.storage import JobFiles
from tests.test_align import aligned_job  # noqa: F401  (fixture)
from tests.test_publish import channel  # noqa: F401  (fixture)
from tests.test_review import A, reviewed  # noqa: F401  (fixture)
from tests.test_thumbnails import job_with_source  # noqa: F401  (fixture)


def published_clip(client, cid: int, ch: dict) -> dict:
    client.patch(f"/api/clips/{cid}", json={"channel_ids": [ch["id"]], "privacy": "public"}, headers=A)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    assert d["status"] == "PUBLISHED"
    return d


def kinds(items) -> set[tuple[str, int | None]]:
    return {(i.kind, i.clip_id or i.job_id) for i in items}


def test_retention_plan_and_run(reviewed, client, channel):
    job_id, _, ids = reviewed
    published_clip(client, ids[1], channel)
    files = JobFiles(job_id)
    for work in (files.proxy, files.sprite, files.align_audio, files.logprobs):  # this fixture skips the media steps
        work.parent.mkdir(parents=True, exist_ok=True)
        work.write_bytes(b"x" * 1000)
    with session_scope() as s:
        source = Path(s.get(Job, job_id).source_path)
        render = Path(s.get(Clip, ids[1]).render_path)
    assert source.exists() and render.exists() and files.proxy.exists()
    now = datetime.now(UTC)
    with session_scope() as s:
        assert cleanup.plan(s, now) == []  # nothing is old yet
        later = kinds(cleanup.plan(s, now + timedelta(days=31)))
        assert later == {("source", job_id), ("render", ids[1])}
        assert ("work", job_id) in kinds(cleanup.plan(s, now + timedelta(days=91)))
    # The admin screen lists it (nothing is deleted by looking).
    r = client.get("/api/admin/storage").json()
    assert r["retention"]["enabled"] is False and r["usage"]["render"] > 0 and r["plan"] == []
    with session_scope() as s:
        result = cleanup.run(s, now + timedelta(days=91))
        assert {i.kind for i in result.deleted} == {"source", "work", "render"} and result.bytes > 0
    assert not source.exists() and not render.exists() and not files.proxy.exists()
    assert files.scenes.exists() and files.thumbnail(ids[1]).exists()  # small results stay
    job = client.get(f"/api/jobs/{job_id}").json()
    assert set(job["purged"]) == {"source", "work"}
    d = client.get(f"/api/clips/{ids[1]}").json()
    assert d["render_url"] is None and d["render_purged_at"] and d["status"] == "PUBLISHED"
    # A render needs the original, which is gone: approval says so instead of failing later.
    r = client.post(f"/api/clips/{ids[3]}/approve", headers=A)
    assert r.status_code == 422 and any("원본" in p for p in r.json()["detail"]["problems"])


def test_a_clip_waiting_for_its_render_keeps_the_original(reviewed, client):
    job_id, _, ids = reviewed
    with session_scope() as s:
        s.get(Clip, ids[1]).status = ClipStatus.APPROVED  # approved, render not done yet
    with session_scope() as s:
        assert ("source", job_id) not in kinds(cleanup.plan(s, datetime.now(UTC) + timedelta(days=40)))


def test_a_render_stays_while_an_upload_is_not_published(reviewed, client, channel):
    from app.models import PublishStatus, PublishTask

    _, _, ids = reviewed
    published_clip(client, ids[1], channel)
    with session_scope() as s:
        for t in s.query(PublishTask).filter(PublishTask.clip_id == ids[1]):
            t.status = PublishStatus.FAILED
    with session_scope() as s:
        assert ("render", ids[1]) not in kinds(cleanup.plan(s, datetime.now(UTC) + timedelta(days=40)))


def test_stale_uploads_are_removed(client):
    r = client.post("/api/uploads", headers={"Tus-Resumable": "1.0.0", "Upload-Length": "100",
                                             "Upload-Metadata": "filename YS5tcDQ="})  # fmt: skip
    assert r.status_code == 201
    upload_id = r.headers["Location"].rstrip("/").split("/")[-1]
    from app.storage import uploads_dir

    old = time.time() - 49 * 3600
    for p in uploads_dir().glob(f"{upload_id}*"):
        os.utime(p, (old, old))
    r = client.get("/api/admin/storage").json()
    assert [i["upload_id"] for i in r["plan"]] == [upload_id]
    r = client.post("/api/admin/cleanup", headers=A).json()
    assert [i["kind"] for i in r["deleted"]] == ["upload"]
    assert not list(uploads_dir().glob(f"{upload_id}*"))


def test_the_daily_task_respects_the_switch(app_env, monkeypatch):
    from app.core.config import get_settings
    from app.maintenance.tasks import run_cleanup

    assert run_cleanup() == {"skipped": "retention disabled"}
    monkeypatch.setattr(get_settings(), "retention_enabled", True)
    assert run_cleanup()["items"] == 0


def test_metrics_summary(reviewed, client, channel):
    job_id, _, ids = reviewed
    # One clip: a reviewer moves its start and edits its title, then it goes up.
    d = client.get(f"/api/clips/{ids[1]}").json()
    client.patch(f"/api/clips/{ids[1]}", json={"start_sec": d["start_sec"] + 0.2, "end_sec": d["end_sec"]}, headers=A)
    client.patch(f"/api/clips/{ids[1]}", json={"title": "고친 제목"}, headers=A)
    published_clip(client, ids[1], channel)
    client.post(f"/api/clips/{ids[3]}/reject", json={"reason": "중복"}, headers=A)
    m = client.get("/api/admin/metrics", params={"days": 7}).json()
    t = m["totals"]
    assert t["jobs"] == 1 and t["clips"] == 3
    assert t["auto"] == 3 and t["auto_moved"] == 1 and t["detection_rate"] == 1.0
    assert t["titles_edited"] == 1 and t["approved"] == 1 and t["rejected"] == 1
    assert t["uploads"] == 1 and t["published"] == 1 and t["upload_success_rate"] == 1.0 and t["on_platform"] == 0
    assert "align" in t["median_step_seconds"] and t["median_pipeline_seconds"] is not None
    (row,) = m["jobs"]
    assert row["job_id"] == job_id and row["llm_input_tokens"] >= 0


def test_program_patch_changes_only_what_is_sent(client):
    p = client.post("/api/programs", json={"name": "뉴스데스크", "broadcast_time": "19:30:00", "audio_track": 1}).json()
    r = client.patch(f"/api/programs/{p['id']}", json={"title_template": "{title} | {station}", "default_tags": ["목포"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["title_template"] == "{title} | {station}" and out["default_tags"] == ["목포"]
    assert out["broadcast_time"] == "19:30:00" and out["audio_track"] == 1 and out["excluded_sources"] == ["광주", "여수"]
    out = client.patch(f"/api/programs/{p['id']}", json={"audio_track": None, "pronunciations": {"KIA": "기아", "": "x"}}).json()
    assert out["audio_track"] is None and out["pronunciations"] == {"KIA": "기아"} and out["broadcast_time"] == "19:30:00"
    client.post("/api/programs", json={"name": "뉴스투데이"})
    assert client.patch(f"/api/programs/{p['id']}", json={"name": "뉴스투데이"}).status_code == 409
    bad = client.patch(f"/api/programs/{p['id']}", json={"thumbnail_template": {"nonsense": 1}})
    assert bad.status_code == 422


@pytest.mark.parametrize("path", ["/api/admin/storage", "/api/admin/metrics"])
def test_admin_endpoints_answer_on_an_empty_system(client, path):
    assert client.get(path).status_code == 200
