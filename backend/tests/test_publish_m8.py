"""M8: status checks with growing intervals, thumbnail retries, edits after the upload pushed to the
platform, and failure scenarios (fake platform with injected errors)."""
# ruff: noqa: F811  (pytest fixtures imported from other test modules)

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import session_scope
from app.models import Channel, ChannelStatus, Clip, PublishStatus, PublishTask
from app.publish import factory
from app.publish import service as svc
from app.publish.base import PublishError, RemoteStatus
from app.publish.fake import FakePublisher
from tests.test_align import aligned_job  # noqa: F401  (fixture)
from tests.test_publish import LATER, channel, detail, past_retry_time, schedule, task_of  # noqa: F401
from tests.test_review import A, reviewed  # noqa: F401  (fixture)
from tests.test_thumbnails import job_with_source  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _restore_factories():
    yield
    factory.set_publisher(None)


def aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


class Scripted(FakePublisher):
    """The fake platform, but get_status answers from a list (then the real fake state)."""

    def __init__(self, statuses: list[RemoteStatus] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.statuses = list(statuses or [])

    def get_status(self, remote_id: str) -> RemoteStatus:
        if self.statuses:
            self.calls.append(("get_status", remote_id))
            return self.statuses.pop(0)
        return super().get_status(remote_id)


PROCESSING = RemoteStatus(True, "uploaded", "private")


# --- 8.1 status checks ---


def test_checks_back_off_while_processing_then_follow_up_after_publication(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    # The upload answer and six checks say "processing", then "processed".
    fake = Scripted([PROCESSING] * 7 + [RemoteStatus(True, "processed", "public")] * 5)
    factory.set_publisher(lambda ch: fake)
    client.patch(f"/api/clips/{cid}", json={"channel_ids": [channel["id"]], "privacy": "public"}, headers=A)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    t = task_of(cid)
    assert t.status == PublishStatus.PROCESSING and t.check_count == 0
    first = aware(t.next_check_at) - datetime.now(UTC)
    assert timedelta(seconds=50) < first <= timedelta(minutes=1)  # the first check after 1 minute
    now = datetime(2030, 1, 1, tzinfo=UTC)
    gaps = []
    for _ in range(6):
        svc.check_status(t.id, now=now)
        nxt = aware(task_of(cid).next_check_at)
        gaps.append(round((nxt - now).total_seconds() / 60))
        now = nxt
    assert gaps == [2, 4, 8, 10, 10, 10] and task_of(cid).status == PublishStatus.PROCESSING
    # Processed: published, then a few follow-ups (10 min, 1 h, 6 h, 24 h after) and no more.
    svc.check_status(t.id, now=now)
    t = task_of(cid)
    assert t.status == PublishStatus.PUBLISHED and aware(t.published_at) == now
    follow = []
    while t.next_check_at is not None:
        at = aware(t.next_check_at)
        follow.append(round((at - now).total_seconds() / 60))
        svc.check_status(t.id, now=at)
        t = task_of(cid)
    assert follow == [10, 60, 360, 1440]
    assert detail(client, cid)["status"] == "PUBLISHED"


def test_a_follow_up_catches_a_rejection_after_publication(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    fake = Scripted([RemoteStatus(True, "rejected", "public", rejection_reason="duplicate")])
    client.patch(f"/api/clips/{cid}", json={"channel_ids": [channel["id"]]}, headers=A)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    t = task_of(cid)
    assert t.status == PublishStatus.PUBLISHED and t.next_check_at is not None
    factory.set_publisher(lambda ch: fake)
    svc.check_status(t.id, now=aware(t.next_check_at))
    d = detail(client, cid)
    assert d["status"] == "UPLOAD_FAILED" and "duplicate" in d["publish_tasks"][0]["last_error"]
    assert task_of(cid).next_check_at is None


def test_a_processed_scheduled_video_is_next_checked_at_its_publish_time(reviewed, client, channel):
    _, _, ids = reviewed
    schedule(client, ids[1], channel)
    client.post(f"/api/clips/{ids[1]}/approve", headers=A)
    t = task_of(ids[1])
    assert t.status == PublishStatus.SCHEDULED and aware(t.next_check_at) == LATER + timedelta(minutes=1)
    assert svc.sweep(now=LATER)["checks"] == 0
    factory.set_publisher(lambda ch: FakePublisher(now=lambda: LATER + timedelta(minutes=1)))
    assert svc.sweep(now=LATER + timedelta(minutes=1))["checks"] == 1
    assert task_of(ids[1]).status == PublishStatus.PUBLISHED


# --- 8.2 thumbnail retries ---


def test_transient_thumbnail_errors_are_retried_by_the_sweep(reviewed, client, channel):
    _, _, ids = reviewed
    fake = FakePublisher(fail={"set_thumbnail": [PublishError("YouTube 503", "server")]})
    factory.set_publisher(lambda ch: fake)
    schedule(client, ids[1], channel)
    client.post(f"/api/clips/{ids[1]}/approve", headers=A)
    t = task_of(ids[1])
    assert t.thumbnail_status == "failed" and t.thumbnail_attempts == 1
    retry_at = aware(t.thumbnail_next_at)
    assert timedelta(minutes=1) < retry_at - datetime.now(UTC) <= timedelta(minutes=2)
    assert svc.sweep(now=retry_at)["thumbnails"] == 1
    t = task_of(ids[1])
    assert t.thumbnail_status == "ok" and t.thumbnail_next_at is None and t.thumbnail_attempts == 0


def test_a_refused_thumbnail_is_not_retried_automatically(reviewed, client, channel):
    _, _, ids = reviewed
    factory.set_publisher(lambda ch: FakePublisher(fail={"set_thumbnail": [PublishError("403 forbidden", "invalid", reason="forbidden")]}))
    schedule(client, ids[1], channel)
    client.post(f"/api/clips/{ids[1]}/approve", headers=A)
    t = task_of(ids[1])
    assert t.thumbnail_status == "failed" and t.thumbnail_next_at is None


# --- 8.3 edits after the upload ---


def test_edits_after_the_upload_are_pushed_to_the_platform(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    fake = FakePublisher()
    factory.set_publisher(lambda ch: fake)
    schedule(client, cid, channel)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    remote = d["publish_tasks"][0]["remote_id"]
    # Title, description, tags: videos.update right away.
    r = client.patch(f"/api/clips/{cid}", headers=A, json={"title": "고친 제목[목포MBC 뉴스데스크]", "description": "고친 설명", "tags": ["목포"]})
    assert r.status_code == 200, r.text
    t = r.json()["publish_tasks"][0]
    assert t["sync_status"] == "ok" and t["synced_at"]
    video = fake.video(remote)
    assert video["title"] == "고친 제목[목포MBC 뉴스데스크]" and video["tags"] == ["목포"]
    assert video["description"].startswith("고친 설명")  # + the hashtags
    # A new schedule; the channels stay as they are.
    later = LATER + timedelta(days=1)
    r = client.patch(f"/api/clips/{cid}", headers=A, json={"publish_at": later.isoformat(), "privacy": "unlisted"})
    assert r.status_code == 200
    video = fake.video(remote)
    assert video["publish_at"].startswith("2030-01-02T09:00") and video["privacy"] == "unlisted"
    t = task_of(cid)
    assert aware(t.publish_at) == later and t.privacy == "unlisted"
    assert client.patch(f"/api/clips/{cid}", headers=A, json={"channel_ids": []}).status_code == 409
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    assert client.patch(f"/api/clips/{cid}", headers=A, json={"publish_at": past}).status_code == 409
    # The thumbnail text: the thumbnail is rendered again and set again.
    n_thumbs = sum(1 for c in fake.calls if c[0] == "set_thumbnail")
    r = client.patch(f"/api/clips/{cid}", headers=A, json={"thumbnail_text": "새 문구"})
    assert r.status_code == 200 and sum(1 for c in fake.calls if c[0] == "set_thumbnail") == n_thumbs + 1
    # Another thumbnail frame works too; the range does not (the video would change).
    other = next(x for x in r.json()["thumbnails"] if not x["selected"])
    assert client.post(f"/api/clips/{cid}/thumbnails/{other['id']}/select", headers=A).status_code == 200
    assert sum(1 for c in fake.calls if c[0] == "set_thumbnail") == n_thumbs + 2
    rng = client.patch(f"/api/clips/{cid}", headers=A, json={"start_sec": 1.0, "end_sec": 3.0})
    assert rng.status_code == 409
    assert "metadata" in [a["action"] for a in client.get(f"/api/clips/{cid}/audit").json()]


def test_a_published_video_keeps_its_time_but_can_change_visibility(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    fake = FakePublisher()
    factory.set_publisher(lambda ch: fake)
    client.patch(f"/api/clips/{cid}", json={"channel_ids": [channel["id"]], "privacy": "public"}, headers=A)
    d = client.post(f"/api/clips/{cid}/approve", headers=A).json()
    assert d["status"] == "PUBLISHED"
    r = client.patch(f"/api/clips/{cid}", headers=A, json={"publish_at": (LATER).isoformat()})
    assert r.status_code == 409 and "게시된" in r.json()["detail"]
    assert client.patch(f"/api/clips/{cid}", headers=A, json={"privacy": "private"}).status_code == 200
    assert fake.video(d["publish_tasks"][0]["remote_id"])["privacy"] == "private"


def test_sync_errors_retry_or_fail_and_can_be_retried_by_hand(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    fake = FakePublisher()
    factory.set_publisher(lambda ch: fake)
    schedule(client, cid, channel)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    # A network error: tried again by the sweep.
    fake.fail["update"] = [PublishError("연결 끊김", "network")]
    t = client.patch(f"/api/clips/{cid}", headers=A, json={"title": "첫 수정"}).json()["publish_tasks"][0]
    assert t["sync_status"] == "pending" and "연결 끊김" in t["sync_error"]
    at = aware(task_of(cid).sync_next_at)
    assert svc.sweep(now=at)["syncs"] == 1
    assert task_of(cid).sync_status == "ok" and fake.video(t["remote_id"])["title"] == "첫 수정"
    # A refused update: failed, shown, retried by hand.
    fake.fail["update"] = [PublishError("YouTube 400 invalidTitle", "invalid", reason="invalidTitle")]
    t = client.patch(f"/api/clips/{cid}", headers=A, json={"title": "둘째 수정"}).json()["publish_tasks"][0]
    assert t["sync_status"] == "failed" and "invalidTitle" in t["sync_error"]
    assert svc.sweep()["syncs"] == 0
    r = client.post(f"/api/publish-tasks/{t['id']}/retry", headers=A)
    assert r.status_code == 200 and r.json()["sync_status"] == "ok"
    assert fake.video(t["remote_id"])["title"] == "둘째 수정"


def test_an_edit_during_a_sync_is_pushed_after_it(reviewed, client, channel):
    """Versioning: a sync that finishes on older metadata leaves the task pending and runs again."""
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    edits = []

    class EditsMeanwhile(FakePublisher):
        def update(self, remote_id, meta):
            super().update(remote_id, meta)
            if not edits:  # someone saves again while the first update is on its way
                edits.append(meta.title)
                with session_scope() as s:
                    from app.models import ClipMetadata

                    s.get(ClipMetadata, cid).title = "그사이 고친 제목"
                    svc.mark_sync(s, s.get(Clip, cid))

    fake = EditsMeanwhile()
    factory.set_publisher(lambda ch: fake)
    client.patch(f"/api/clips/{cid}", headers=A, json={"title": "먼저 고친 제목"})
    t = task_of(cid)
    assert edits == ["먼저 고친 제목"]
    assert t.sync_status == "ok" and t.synced_version == t.sync_version
    assert fake.video(t.remote_id)["title"] == "그사이 고친 제목"


def test_an_edit_during_the_upload_is_pushed_when_it_finishes(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)

    class SlowUpload(FakePublisher):
        def upload(self, video, meta, **kw):
            with session_scope() as s:  # the reviewer saves while the file is going up
                from app.models import ClipMetadata

                s.get(ClipMetadata, cid).title = "업로드 중에 고친 제목"
                assert svc.mark_sync(s, s.get(Clip, cid)) == []  # not yet: nothing on the platform
            return super().upload(video, meta, **kw)

    fake = SlowUpload()
    factory.set_publisher(lambda ch: fake)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    t = task_of(cid)
    assert t.sync_status == "ok" and fake.video(t.remote_id)["title"] == "업로드 중에 고친 제목"


def test_bulk_reschedule_of_uploaded_clips_is_pushed(reviewed, client, channel):
    _, _, ids = reviewed
    fake = FakePublisher()
    factory.set_publisher(lambda ch: fake)
    for cid in (ids[1], ids[3]):
        schedule(client, cid, channel)
        client.post(f"/api/clips/{cid}/approve", headers=A)
    first = LATER + timedelta(days=2)
    r = client.post("/api/clips/bulk", headers=A, json={"clip_ids": [ids[1], ids[3]], "action": "schedule",
                                                        "first_at": first.isoformat(), "interval_minutes": 30})  # fmt: skip
    assert all(x["ok"] for x in r.json())
    times = sorted(fake.video(task_of(c).remote_id)["publish_at"] for c in (ids[1], ids[3]))
    assert times[0].startswith("2030-01-03T09:00") and times[1].startswith("2030-01-03T09:30")


# --- 8.5 failure scenarios ---


@pytest.mark.parametrize(
    ("error", "status", "kind", "retry_in"),
    [
        (PublishError("연결 끊김", "network"), PublishStatus.QUEUED, "network", timedelta(minutes=1)),
        (PublishError("YouTube 503", "server"), PublishStatus.QUEUED, "server", timedelta(minutes=1)),
        (PublishError("세션 만료", "server", reason="sessionExpired"), PublishStatus.QUEUED, "server", timedelta(minutes=1)),
        (PublishError("YouTube 401", "auth"), PublishStatus.FAILED, "auth", None),
        (PublishError("quotaExceeded", "quota", reason="quotaExceeded"), PublishStatus.QUEUED, "quota", "reset"),
        (PublishError("uploadLimitExceeded", "quota", reason="uploadLimitExceeded"), PublishStatus.QUEUED, "quota", "reset"),
        (PublishError("YouTube 400 invalidTitle", "invalid", reason="invalidTitle"), PublishStatus.FAILED, "invalid", None),
    ],
)  # fmt: skip
def test_upload_failure_scenarios(reviewed, client, channel, error, status, kind, retry_in):
    from app.publish import quota

    _, _, ids = reviewed
    cid = ids[1]
    factory.set_publisher(lambda ch: FakePublisher(fail={"upload": [error]}))
    schedule(client, cid, channel)
    before = datetime.now(UTC)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    t = task_of(cid)
    assert (t.status, t.error_kind) == (status, kind)
    clip_status = detail(client, cid)["status"]
    assert clip_status == ("UPLOAD_FAILED" if status == PublishStatus.FAILED else "UPLOADING")
    if retry_in == "reset":
        assert aware(t.next_retry_at) == quota.next_reset(before) + svc.QUOTA_GRACE
    elif retry_in is not None:
        assert retry_in - timedelta(seconds=15) < aware(t.next_retry_at) - before <= retry_in + timedelta(seconds=15)
    else:
        assert t.next_retry_at is None
    if error.reason == "sessionExpired":
        assert t.resumable_uri is None  # the next try starts a new session
    with session_scope() as s:
        ch = s.get(Channel, channel["id"])
        assert ch.status == (ChannelStatus.REAUTH_REQUIRED if kind == "auth" else ChannelStatus.ACTIVE)


def test_five_server_errors_in_a_row_fail_the_upload(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    fake = FakePublisher(fail={"upload": [PublishError("YouTube 500", "server") for _ in range(5)]})
    factory.set_publisher(lambda ch: fake)
    schedule(client, cid, channel)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    for _ in range(4):
        past_retry_time(task_of(cid).id)
        svc.sweep()
    t = task_of(cid)
    assert t.status == PublishStatus.FAILED and t.attempts == 5 and "5회" in t.last_error


def test_a_stalled_upload_is_started_again(reviewed, client, channel):
    """The worker died mid-upload: after 10 minutes without progress the sweep queues it again
    and it continues from the saved session."""
    _, _, ids = reviewed
    cid = ids[1]
    fake = FakePublisher(fail={"upload": [PublishError("연결 끊김", "network")]})
    factory.set_publisher(lambda ch: fake)
    schedule(client, cid, channel)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    with session_scope() as s:
        t = s.get(PublishTask, task_of(cid).id)
        t.status, t.next_retry_at = PublishStatus.UPLOADING, None
    later = datetime.now(UTC) + timedelta(minutes=11)
    svc.sweep(now=later)  # queued again and started in the same sweep
    t = task_of(cid)
    assert t.status == PublishStatus.SCHEDULED and fake.calls[-3][0] == "upload" and fake.calls[-3][1]


def test_a_rejected_video_is_uploaded_again_as_a_new_one(reviewed, client, channel):
    _, _, ids = reviewed
    cid = ids[1]
    schedule(client, cid, channel)
    client.post(f"/api/clips/{cid}/approve", headers=A)
    first = task_of(cid).remote_id
    factory.set_publisher(lambda ch: Scripted([RemoteStatus(True, "rejected", "private", rejection_reason="claim")]))
    svc.check_status(task_of(cid).id)
    assert detail(client, cid)["status"] == "UPLOAD_FAILED"
    factory.set_publisher(None)
    r = client.post(f"/api/publish-tasks/{task_of(cid).id}/retry", headers=A)
    assert r.status_code == 200 and r.json()["remote_id"] not in (None, first)
    assert r.json()["status"] == "SCHEDULED"
