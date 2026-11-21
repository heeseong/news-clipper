"""Upload flow (M7): channels, approve → render → upload → scheduled → published, retries, quota,
cancelling, OAuth connections and encrypted tokens. Uses the fake platform (app/publish/fake.py)."""
# ruff: noqa: F811  (pytest fixtures imported from other test modules)

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.db import session_scope
from app.models import Channel, ChannelStatus, ClipMetadata, PublishStatus, PublishTask
from app.publish import factory, quota
from app.publish import service as svc
from app.publish.base import PublishError
from app.publish.fake import FakePublisher
from tests.test_align import aligned_job  # noqa: F401  (fixture)
from tests.test_review import A, reviewed  # noqa: F401  (fixture)
from tests.test_thumbnails import job_with_source  # noqa: F401  (fixture)
from tests.test_youtube import FakeGoogle

LATER = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _restore_factories():
    yield
    factory.set_publisher(None)
    factory.set_oauth(None)


@pytest.fixture
def channel(client) -> dict:
    r = client.post("/api/channels/connect", json={"mode": "fake"}, headers=A)
    assert r.status_code == 200, r.text
    return r.json()["channel"]


def detail(client, cid: int) -> dict:
    return client.get(f"/api/clips/{cid}").json()


def task_of(cid: int) -> PublishTask:
    with session_scope() as s:
        return s.scalars(select(PublishTask).where(PublishTask.clip_id == cid).order_by(PublishTask.id.desc())).first()


def past_retry_time(task_id: int) -> None:
    """The worker checks the real clock: move the retry time into the past instead."""
    with session_scope() as s:
        s.get(PublishTask, task_id).next_retry_at = datetime.now(UTC) - timedelta(seconds=1)


def schedule(client, cid: int, ch: dict, at: datetime = LATER, privacy: str = "public") -> None:
    r = client.patch(f"/api/clips/{cid}", json={"channel_ids": [ch["id"]], "publish_at": at.isoformat(), "privacy": privacy}, headers=A)
    assert r.status_code == 200, r.text


def test_approve_render_upload_schedule_then_published(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    # Celery runs in-process: render, then the upload to the chosen channel.
    assert d["status"] == "SCHEDULED", d
    (t,) = d["publish_tasks"]
    assert t["status"] == "SCHEDULED" and t["channel_name"] == channel["name"] and t["remote_id"]
    assert t["thumbnail_status"] == "ok" and t["privacy"] == "public" and t["attempts"] == 1
    assert d["remote_urls"] == [t["remote_url"]]
    video = FakePublisher().video(t["remote_id"])
    meta = d["metadata"]
    assert video["title"] == meta["title"] and video["privacy"] == "public" and video["thumbnail"]
    assert video["publish_at"].startswith("2030-01-01T09:00") and video["category_id"] == "25" and video["language"] == "ko"
    assert all(h in video["description"] for h in meta["hashtags"])
    # Uploaded: no reopening, no changing the schedule (cancel first).
    assert client.post(f"/api/clips/{cid}/reopen", headers=A).status_code == 409
    r = client.patch(f"/api/clips/{cid}", json={"privacy": "private"}, headers=A)
    assert r.status_code == 409 and "업로드" in r.json()["detail"]
    counts = client.get("/api/jobs").json()["items"][0]["clip_counts"]
    assert counts["approved"] == 1 and counts["uploaded"] == 1
    # The sweep checks the video once publish_at has passed: the platform made it public.
    assert svc.sweep(now=LATER - timedelta(hours=1)) == {"uploads": 0, "checks": 0}  # processed, not due yet
    with session_scope() as s:
        s.get(PublishTask, t["id"]).checked_at = None
    factory.set_publisher(lambda ch: FakePublisher(ch.remote_channel_id, now=lambda: LATER + timedelta(minutes=1)))
    assert svc.sweep(now=LATER + timedelta(minutes=1))["checks"] == 1
    d = detail(client, cid)
    assert d["status"] == "PUBLISHED" and d["publish_tasks"][0]["published_at"]
    tasks = client.get("/api/publish-tasks").json()
    assert tasks[0]["title"] and tasks[0]["item_no"] == 1 and tasks[0]["job_id"]
    actions = [a["action"] for a in client.get(f"/api/clips/{cid}/audit").json()]
    assert "approve" in actions


def test_without_a_channel_the_clip_stays_rendered_until_published_by_hand(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    assert d["status"] == "RENDERED" and d["publish_tasks"] == []
    assert client.post(f"/api/clips/{cid}/publish", headers=A).status_code == 409  # no channel chosen
    # Publishing settings can still change after the render; then upload by hand (no schedule → processing).
    assert client.patch(f"/api/clips/{cid}", json={"channel_ids": [channel["id"]], "privacy": "unlisted"}, headers=A).status_code == 200
    d = client.post(f"/api/clips/{cid}/publish", headers=A).json()
    assert d["status"] == "PUBLISHED", d  # the fake platform has processed it right away
    assert d["publish_tasks"][0]["privacy"] == "unlisted"
    assert client.post(f"/api/clips/{cid}/publish", headers=A).status_code == 409  # nothing new to upload
    assert client.patch(f"/api/clips/{cid}", json={"channel_ids": [999]}, headers=A).status_code == 409


def test_network_errors_back_off_and_resume_the_session(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    fake = FakePublisher(fail={"upload": [PublishError("연결 끊김", "network")]})
    factory.set_publisher(lambda ch: fake)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    assert d["status"] == "UPLOADING"
    t = task_of(cid)
    assert t.status == PublishStatus.QUEUED and t.error_kind == "network" and t.resumable_uri
    retry_at = t.next_retry_at.replace(tzinfo=UTC) if t.next_retry_at.tzinfo is None else t.next_retry_at
    assert timedelta(seconds=50) < retry_at - datetime.now(UTC) <= timedelta(minutes=1)
    assert svc.sweep(now=datetime.now(UTC))["uploads"] == 0  # not yet
    past_retry_time(t.id)
    assert svc.sweep()["uploads"] == 1
    t = task_of(cid)
    assert t.status == PublishStatus.SCHEDULED and t.attempts == 2 and t.resumable_uri is None
    assert fake.calls[0] == ("upload", None) and fake.calls[1][0] == "upload" and fake.calls[1][1].startswith("fake-session-")


def test_too_many_failures_or_bad_metadata_fail_and_can_be_retried(reviewed, client, channel, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "publish_max_attempts", 2)
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    fake = FakePublisher(fail={"upload": [PublishError("5xx", "server"), PublishError("5xx", "server")]})
    factory.set_publisher(lambda ch: fake)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    past_retry_time(task_of(cid).id)
    svc.sweep()
    t = task_of(cid)
    d = detail(client, cid)
    assert d["status"] == "UPLOAD_FAILED" and "2회" in d["publish_tasks"][0]["last_error"]
    assert client.get("/api/jobs").json()["items"][0]["clip_counts"]["upload_failed"] == 1
    r = client.post(f"/api/publish-tasks/{t.id}/retry", headers=A)
    assert r.status_code == 200 and r.json()["status"] == "SCHEDULED" and r.json()["attempts"] == 1
    assert "publish_retry" in [a["action"] for a in client.get(f"/api/clips/{cid}/audit").json()]

    # 400-type errors are not retried automatically.
    cid2 = ids[3]
    schedule(client, cid2, channel)
    fake.fail["upload"] = [PublishError("YouTube 400 invalidTitle", "invalid", reason="invalidTitle")]
    d = client.post(f"/api/clips/{cid2}/approve", headers=A).json()
    assert d["status"] == "UPLOAD_FAILED" and d["publish_tasks"][0]["error_kind"] == "invalid"
    assert d["publish_tasks"][0]["next_retry_at"] is None
    # Fix the settings (allowed while failed), then retry from the editor's "업로드".
    assert client.patch(f"/api/clips/{cid2}", json={"privacy": "private"}, headers=A).status_code == 200
    d = client.post(f"/api/clips/{cid2}/publish", headers=A).json()
    assert d["status"] == "SCHEDULED" and d["publish_tasks"][0]["privacy"] == "private"


def test_auth_failure_marks_the_channel_for_reconnection(reviewed, client, channel):
    _, _, ids = reviewed
    factory.set_publisher(lambda ch: FakePublisher(fail={"upload": [PublishError("YouTube 401", "auth")]}))
    schedule(client, ids[1], channel)
    d = client.post(f"/api/clips/{ids[1]}/approve", headers=A).json()
    assert d["status"] == "UPLOAD_FAILED"
    chans = client.get("/api/channels").json()["channels"]
    assert chans[0]["status"] == "reauth_required"
    r = client.post(f"/api/publish-tasks/{task_of(ids[1]).id}/retry", headers=A)
    assert r.status_code == 409 and "다시 연결" in r.json()["detail"]


def test_quota_defers_to_the_next_pacific_day(reviewed, client, monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "youtube_daily_quota", 2000)  # room for one upload (1600 + 50)
    _, _, ids = reviewed
    with session_scope() as db:  # a "youtube" channel served by the fake platform
        ch = Channel(platform="youtube", name="유튜브", remote_channel_id="UCtest", status=ChannelStatus.ACTIVE)
        db.add(ch)
        db.flush()
        chan = {"id": ch.id}
    factory.set_publisher(lambda c: FakePublisher(c.remote_channel_id))
    schedule(client, ids[1], chan)
    schedule(client, ids[3], chan)
    assert client.post(f"/api/clips/{ids[1]}/approve", headers=A).json()["status"] == "SCHEDULED"
    d = client.post(f"/api/clips/{ids[3]}/approve", headers=A).json()
    assert d["status"] == "UPLOADING"
    t = task_of(ids[3])
    reset = quota.next_reset()
    assert t.error_kind == "quota" and "할당량" in t.last_error and t.attempts == 0
    assert t.next_retry_at.replace(tzinfo=UTC) == (reset + svc.QUOTA_GRACE).astimezone(UTC)
    summary = client.get("/api/publish/summary").json()
    assert summary["quota"][0]["used"] == 1650 and summary["quota"][0]["uploads_left"] == 0
    assert summary["channels"][0]["queued"] == 1
    # The next quota day: uploaded.
    tomorrow = reset + svc.QUOTA_GRACE + timedelta(seconds=1)
    assert svc.sweep(now=tomorrow)["uploads"] == 1
    assert svc.run_upload(t.id, now=tomorrow) == "uploaded"  # the worker, on the next quota day
    assert task_of(ids[3]).status == PublishStatus.SCHEDULED
    with session_scope() as db:
        assert quota.used(db, "youtube", tomorrow) == 1650
    # A 403 quotaExceeded from the platform (our count was lower) also waits for the reset.
    monkeypatch.setattr(s, "youtube_daily_quota", 10_000)
    factory.set_publisher(lambda c: FakePublisher(fail={"upload": [PublishError("quotaExceeded", "quota", reason="quotaExceeded")]}))
    with session_scope() as db:
        t = db.get(PublishTask, task_of(ids[3]).id)
        t.status, t.remote_id = PublishStatus.FAILED, None
    client.post(f"/api/publish-tasks/{task_of(ids[3]).id}/retry", headers=A)
    t = task_of(ids[3])
    assert t.status == PublishStatus.QUEUED and t.error_kind == "quota" and "quotaExceeded" in t.last_error
    with session_scope() as db:
        assert quota.used(db, "youtube") == s.youtube_daily_quota


def test_quota_day_is_pacific_midnight():
    # 2026-11-21 07:30 UTC is 23:30 on the 20th in Los Angeles (PST, UTC-8).
    now = datetime(2026, 11, 21, 7, 30, tzinfo=UTC)
    assert quota.quota_day(now).isoformat() == "2026-11-20"
    assert quota.next_reset(now).astimezone(UTC) == datetime(2026, 11, 21, 8, 0, tzinfo=UTC)


def test_cancel_deletes_the_video_and_allows_reopening(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    t = d["publish_tasks"][0]
    r = client.post(f"/api/publish-tasks/{t['id']}/cancel", headers=A)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert FakePublisher().video(t["remote_id"]) is None
    d = detail(client, cid)
    assert d["status"] == "RENDERED" and d["publish_tasks"] == []
    assert client.post(f"/api/clips/{cid}/reopen", headers=A).json()["status"] == "DRAFT"
    assert client.get("/api/publish-tasks", params={"include_cancelled": True}).json()[0]["status"] == "CANCELLED"


def test_thumbnail_failure_keeps_the_video(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    fake = FakePublisher(fail={"set_thumbnail": [PublishError("YouTube 403 forbidden", "invalid", reason="forbidden")]})
    factory.set_publisher(lambda ch: fake)
    schedule(client, cid, channel)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    t = d["publish_tasks"][0]
    assert d["status"] == "SCHEDULED" and t["thumbnail_status"] == "failed" and "전화번호" in t["thumbnail_error"]
    r = client.post(f"/api/publish-tasks/{t['id']}/retry", headers=A)
    assert r.status_code == 200 and r.json()["thumbnail_status"] == "ok"


def test_still_private_after_publish_time_is_reported(reviewed, client, channel):
    """Unverified API projects: YouTube keeps the video private even after publishAt."""
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    t = task_of(cid)

    class Locked(FakePublisher):
        def get_status(self, remote_id):
            st = super().get_status(remote_id)
            st.privacy, st.publish_at = "private", None
            return st

    factory.set_publisher(lambda ch: Locked())
    svc.check_status(t.id, now=LATER + timedelta(minutes=5))
    assert task_of(cid).status == PublishStatus.SCHEDULED  # may simply be late
    svc.check_status(t.id, now=LATER + timedelta(minutes=30))
    d = detail(client, cid)
    assert d["status"] == "UPLOAD_FAILED" and "감사" in d["publish_tasks"][0]["last_error"]


def test_reopening_a_failed_clip_cancels_its_pending_tasks(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    factory.set_publisher(lambda ch: FakePublisher(fail={"upload": [PublishError("bad", "invalid")]}))
    schedule(client, cid, channel)
    assert client.post(f"/api/clips/{cid}/approve", headers=A).json()["status"] == "UPLOAD_FAILED"
    d = client.post(f"/api/clips/{cid}/reopen", headers=A).json()
    assert d["status"] == "DRAFT" and d["publish_tasks"] == []


def test_program_default_channels_fill_new_metadata(job_with_source, client, channel):  # noqa: F811
    from app.pipeline import tasks

    job_id, _ = job_with_source
    with session_scope() as s:
        from app.models import Job

        program_id = s.get(Job, job_id).program_id
    prog = client.get("/api/programs").json()
    body = next(p for p in prog if p["id"] == program_id)
    r = client.put(f"/api/programs/{program_id}", json={**body, "default_channels": [channel["id"]]})
    assert r.status_code == 200 and r.json()["default_channels"] == [channel["id"]]
    assert client.put(f"/api/programs/{program_id}", json={**body, "default_channels": [12345]}).status_code == 422
    tasks.start_job(job_id)
    with session_scope() as s:
        metas = list(s.scalars(select(ClipMetadata)))
        assert metas and all(m.channel_ids == [channel["id"]] for m in metas)


# --- connecting YouTube channels ---


@pytest.fixture
def youtube_settings(monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "publisher", "youtube")
    monkeypatch.setattr(s, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_oauth_redirect_uri", "https://clipper.test/api/channels/oauth/callback")
    from pydantic import SecretStr

    monkeypatch.setattr(s, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    g = FakeGoogle()
    from app.publish.oauth import GoogleOAuth

    factory.set_oauth(GoogleOAuth("cid.apps.googleusercontent.com", "s3cret", token_base="https://oauth.test",
                                  transport=httpx.MockTransport(g.handler)))  # fmt: skip
    from app.publish import channels

    monkeypatch.setattr(channels, "fetch_channel_info", lambda token: {
        "id": "UCxyz", "title": f"목포MBC 테스트 ({token})", "url": "https://www.youtube.com/@test", "avatar_url": None})  # fmt: skip
    return g


def test_device_flow_connects_and_stores_tokens_encrypted(client, youtube_settings):
    assert client.get("/api/channels").json()["modes"] == ["web", "device"]
    assert client.post("/api/channels/connect", json={"mode": "fake"}, headers=A).status_code == 409
    r = client.post("/api/channels/connect", json={"mode": "device"}, headers=A).json()
    assert r["user_code"] == "ABCD-EFGH" and r["verification_url"] == "https://www.google.com/device" and r["status"] == "pending"
    sid = r["session_id"]
    assert client.post(f"/api/channels/connect/{sid}/poll").json()["status"] == "pending"  # too early: Google not asked
    assert youtube_settings.device_polls == 0
    from app.models import OAuthSession

    for _ in range(2):
        with session_scope() as s:
            s.get(OAuthSession, sid).next_poll_at = None
        r = client.post(f"/api/channels/connect/{sid}/poll").json()
    assert r["status"] == "connected" and r["channel"]["name"] == "목포MBC 테스트 (acc)"
    with session_scope() as s:
        ch = s.get(Channel, r["channel"]["id"])
        assert ch.refresh_token and "ref" not in ch.refresh_token and ch.platform == "youtube"
        assert factory.ChannelTokens(ch.id).get() == "acc"  # stored access token, still valid
        assert factory.ChannelTokens(ch.id).get(force_refresh=True) == "acc-new"
        ch_id = ch.id
    assert "refresh_token" not in client.get("/api/channels").text
    # A revoked refresh token: the channel must be connected again.
    from app.publish import crypto

    with session_scope() as s:
        s.get(Channel, ch_id).refresh_token = crypto.encrypt("revoked")
    with pytest.raises(PublishError):
        factory.ChannelTokens(ch_id).get(force_refresh=True)
    assert client.get("/api/channels").json()["channels"][0]["status"] == "reauth_required"
    assert client.delete(f"/api/channels/{ch_id}", headers=A).json()["status"] == "disconnected"


def test_web_flow_callback(client, youtube_settings):
    r = client.post("/api/channels/connect", json={"mode": "web"}, headers=A).json()
    assert r["auth_url"].startswith("https://accounts.google.com/") and "redirect_uri=https%3A%2F%2Fclipper.test" in r["auth_url"]
    state = r["session_id"]
    bad = client.get("/api/channels/oauth/callback", params={"state": "nope", "code": "x"}, follow_redirects=False)
    assert bad.status_code == 303 and bad.headers["location"].startswith("/channels?error=")
    ok = client.get("/api/channels/oauth/callback", params={"state": state, "code": "c0de"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"].startswith("/channels?connected=")
    assert client.get("/api/channels").json()["channels"][0]["name"] == "목포MBC 테스트 (acc-web)"
    denied = client.post("/api/channels/connect", json={"mode": "web"}, headers=A).json()["session_id"]
    r = client.get("/api/channels/oauth/callback", params={"state": denied, "error": "access_denied"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_connecting_needs_the_encryption_key(client, youtube_settings, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "token_encryption_key", None)
    r = client.post("/api/channels/connect", json={"mode": "device"}, headers=A)
    assert r.status_code == 409 and "TOKEN_ENCRYPTION_KEY" in r.json()["detail"]


def test_youtube_publisher_is_built_for_youtube_channels(app_env, youtube_settings):
    from app.publish.youtube import YouTubePublisher

    ch = Channel(id=1, platform="youtube", name="x", remote_channel_id="UC")
    assert isinstance(factory.publisher_for(ch), YouTubePublisher)
    assert isinstance(factory.publisher_for(Channel(id=2, platform="fake", name="y", remote_channel_id="f")), FakePublisher)


def test_schedule_in_kst_is_stored_as_utc(reviewed, client):
    _, _, ids = reviewed
    client.patch(f"/api/clips/{ids[1]}", json={"publish_at": "2030-01-01T09:00:00+09:00"}, headers=A)
    with session_scope() as s:
        at = s.get(ClipMetadata, ids[1]).publish_at
    assert at.replace(tzinfo=at.tzinfo or UTC) == datetime(2030, 1, 1, 0, 0, tzinfo=UTC)


def test_channel_upload_limit_does_not_use_up_the_project_quota(reviewed, client, channel):
    """400 uploadLimitExceeded is the channel's daily upload count (2026-11-21, 11th upload)."""
    _, _, ids = reviewed
    with session_scope() as db:
        ch = Channel(platform="youtube", name="유튜브", remote_channel_id="UClimit", status=ChannelStatus.ACTIVE)
        db.add(ch)
        db.flush()
        chan = {"id": ch.id}
    factory.set_publisher(lambda c: FakePublisher(fail={"upload": [
        PublishError("YouTube 400 uploadLimitExceeded", "quota", reason="uploadLimitExceeded")]}))  # fmt: skip
    schedule(client, ids[1], chan)
    assert client.post(f"/api/clips/{ids[1]}/approve", headers=A).json()["status"] == "UPLOADING"
    t = task_of(ids[1])
    assert t.status == PublishStatus.QUEUED and t.error_kind == "quota" and "업로드 개수 한도" in t.last_error
    with session_scope() as db:
        assert quota.used(db, "youtube") == 1650  # only our reservation, not the whole quota


def test_description_is_the_newsroom_script(reviewed, client):
    """2026-11-21: the YouTube description is the cue sheet script as written (+ footer)."""
    _, _, ids = reviewed
    desc = detail(client, ids[1])["metadata"]["description"]
    assert desc.startswith("◀ 앵 커 ▶\n새 다리가 오늘 개통했습니다.\n홍길동 기자입니다.\n◀ END ▶\n◀ 리포트 ▶")
    assert "엠비씨 뉴스 홍길동입니다." in desc and desc.endswith("목포MBC 뉴스데스크 2026년 11월 4일 방송")
