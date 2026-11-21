"""YouTube adapter and Google OAuth (M7) against a simulated API (httpx.MockTransport): resumable
upload, resuming, token refresh, error kinds, status mapping. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.publish.base import PublishError, PublishMeta
from app.publish.oauth import DevicePending, GoogleOAuth
from app.publish.youtube import YouTubePublisher, fit_tags, status_from_video, video_body

API = "https://yt.test"
SESSION = "https://yt.test/upload/youtube/v3/videos?uploadType=resumable&upload_id=abc"


class FakeYouTube:
    """Just enough of the resumable protocol: 308 + Range while incomplete, 200 + video at the end."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.received = 0
        self.inits: list[dict] = []
        self.chunks: list[tuple[int, int]] = []
        self.fail_next_chunk: list[Exception | int] = []  # an exception or an HTTP status
        self.tokens_seen: list[str] = []
        self.expired_tokens: set[str] = set()
        self.session_gone = False
        self.thumbnail: bytes | None = None
        self.videos = {"vid123": {"id": "vid123", "status": {"uploadStatus": "processed", "privacyStatus": "private",
                                                             "publishAt": "2030-01-01T09:00:00Z"}}}  # fmt: skip

    def handler(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        self.tokens_seen.append(token)
        if token in self.expired_tokens:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials", "errors": [{"reason": "authError"}]}})
        url = str(request.url)
        if request.method == "POST" and "/upload/youtube/v3/videos" in url:
            self.inits.append({"params": dict(request.url.params), "body": json.loads(request.content),
                               "length": request.headers["x-upload-content-length"]})  # fmt: skip
            return httpx.Response(200, headers={"Location": SESSION})
        if request.method == "PUT" and url == SESSION:
            if self.session_gone:
                return httpx.Response(404)
            rng = request.headers["content-range"]
            if rng.startswith("bytes */"):
                return self._state()
            if self.fail_next_chunk:
                err = self.fail_next_chunk.pop(0)
                if isinstance(err, Exception):
                    raise err
                return httpx.Response(err, json={"error": {"code": err, "message": "boom", "errors": [{"reason": "backendError"}]}})
            span, total = rng.removeprefix("bytes ").split("/")
            start, end = (int(x) for x in span.split("-"))
            assert int(total) == self.size and start == self.received and len(request.content) == end - start + 1
            self.chunks.append((start, end))
            self.received = end + 1
            return self._state()
        if "/thumbnails/set" in url:
            assert request.url.params["videoId"] == "vid123"
            self.thumbnail = request.content
            return httpx.Response(200, json={"items": []})
        if request.method == "GET" and "/youtube/v3/videos" in url:
            item = self.videos.get(request.url.params["id"])
            return httpx.Response(200, json={"items": [item] if item else []})
        if request.method == "DELETE":
            self.videos.pop(request.url.params["id"], None)
            return httpx.Response(204)
        return httpx.Response(500)

    def _state(self) -> httpx.Response:
        if self.received >= self.size:
            return httpx.Response(200, json={"id": "vid123", "status": {"uploadStatus": "uploaded", "privacyStatus": "private"}})
        if self.received == 0:
            return httpx.Response(308)
        return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"})


class Tokens:
    def __init__(self) -> None:
        self.refreshes = 0
        self.current = "tok-1"

    def get(self, force_refresh: bool = False) -> str:
        if force_refresh:
            self.refreshes += 1
            self.current = f"tok-{self.refreshes + 1}"
        return self.current


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "final.mp4"
    p.write_bytes(bytes(range(256)) * 4000)  # 1,024,000 bytes
    return p


def publisher(yt: FakeYouTube, tokens: Tokens | None = None) -> YouTubePublisher:
    pub = YouTubePublisher(tokens or Tokens(), api_url=API, transport=httpx.MockTransport(yt.handler), sleep=lambda s: None)
    pub.chunk = 256 * 1024  # small chunks for the test (real ones are multiples of 8 MB)
    return pub


META = PublishMeta(title="<제목>[목포MBC 뉴스데스크]", description="설명", tags=["목포", "뉴스 데스크"], privacy="public",
                   publish_at=datetime(2030, 1, 1, 18, 0, tzinfo=UTC))  # fmt: skip


def test_resumable_upload_in_chunks_with_schedule_and_metadata(video: Path):
    yt = FakeYouTube(video.stat().st_size)
    sessions, progress = [], []
    result = publisher(yt).upload(video, META, on_session=sessions.append, on_progress=lambda a, b: progress.append(a))
    assert result.remote_id == "vid123" and result.url == "https://www.youtube.com/watch?v=vid123"
    assert sessions == [SESSION] and len(yt.chunks) == 4 and yt.received == yt.size
    assert progress[-1] == yt.size
    init = yt.inits[0]
    assert init["params"]["part"] == "snippet,status" and init["length"] == str(yt.size)
    sn, st = init["body"]["snippet"], init["body"]["status"]
    assert sn["title"] == "＜제목＞[목포MBC 뉴스데스크]" and sn["categoryId"] == "25" and sn["defaultLanguage"] == "ko"
    assert sn["tags"] == ["목포", "뉴스 데스크"]
    assert st == {"privacyStatus": "private", "publishAt": "2030-01-01T18:00:00Z", "selfDeclaredMadeForKids": False}
    assert video_body(PublishMeta(title="t", privacy="unlisted"))["status"]["privacyStatus"] == "unlisted"


def test_interrupted_chunk_is_resumed_in_place(video: Path):
    yt = FakeYouTube(video.stat().st_size)
    yt.fail_next_chunk = [httpx.ConnectError("reset"), 503]
    result = publisher(yt).upload(video, META)
    assert result.remote_id == "vid123" and yt.received == yt.size and len(yt.inits) == 1


def test_a_saved_session_continues_where_the_platform_stopped(video: Path):
    yt = FakeYouTube(video.stat().st_size)
    yt.fail_next_chunk = [httpx.ConnectError("down")] * 10  # the network stays down
    sessions = []
    with pytest.raises(PublishError) as e:
        publisher(yt).upload(video, META, on_session=sessions.append)
    assert e.value.kind == "network" and e.value.retryable
    yt.received, yt.fail_next_chunk = 512 * 1024, []  # the platform had kept two chunks
    publisher(yt).upload(video, META, resume_uri=sessions[0])
    assert len(yt.inits) == 1 and yt.chunks[0] == (512 * 1024, 768 * 1024 - 1)  # no new session, no re-sent bytes


def test_an_expired_session_starts_over(video: Path):
    yt = FakeYouTube(video.stat().st_size)
    yt.session_gone = True
    pub = publisher(yt)
    with pytest.raises(PublishError):  # the new session (same URI in the fake) is gone too
        pub.upload(video, META, resume_uri=SESSION)
    assert len(yt.inits) == 1


def test_401_refreshes_the_token_once(video: Path):
    yt = FakeYouTube(video.stat().st_size)
    yt.expired_tokens = {"tok-1"}
    tokens = Tokens()
    publisher(yt, tokens).set_thumbnail("vid123", video)
    assert tokens.refreshes == 1 and yt.tokens_seen == ["tok-1", "tok-2"] and yt.thumbnail
    yt.expired_tokens = {"tok-2", "tok-3"}
    with pytest.raises(PublishError) as e:
        publisher(yt, tokens).get_status("vid123")
    assert e.value.kind == "auth"


@pytest.mark.parametrize(
    ("status", "reason", "kind"),
    [(403, "quotaExceeded", "quota"), (403, "uploadLimitExceeded", "quota"), (403, "rateLimitExceeded", "server"),
     (400, "invalidTitle", "invalid"), (403, "forbidden", "invalid"), (500, "backendError", "server"), (429, None, "server")],
)  # fmt: skip
def test_error_kinds(video: Path, status: int, reason: str | None, kind: str):
    body = {"error": {"code": status, "message": "x", "errors": [{"reason": reason}] if reason else []}}
    pub = YouTubePublisher(Tokens(), api_url=API, transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)))
    with pytest.raises(PublishError) as e:
        pub.upload(video, META)
    assert e.value.kind == kind and (e.value.reason == reason or reason is None)


def test_status_and_delete(video: Path):
    yt = FakeYouTube(1)
    pub = publisher(yt)
    st = pub.get_status("vid123")
    assert st.exists and st.privacy == "private" and st.publish_at == datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    pub.delete("vid123")
    assert not pub.get_status("vid123").exists
    pub.delete("vid123")  # already gone: fine
    assert status_from_video({"id": "x", "status": {"uploadStatus": "rejected", "rejectionReason": "duplicate"}}).rejection_reason == "duplicate"


def test_tags_fit_youtube_limit():
    tags = fit_tags(["가" * 100, "나 다" * 50, "라" * 300, "마"])
    assert tags[:1] == ["가" * 100] and sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags) - 1 <= 500
    assert fit_tags(["a,b", " ", "<x>"]) == ["a b", "＜x＞"]


# --- OAuth ---


class FakeGoogle:
    def __init__(self) -> None:
        self.device_polls = 0
        self.approve_after = 2
        self.requests: list[tuple[str, dict, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        self.requests.append((request.url.path, form, request.headers.get("authorization")))
        if request.url.path == "/device/code":
            return httpx.Response(200, json={"device_code": "DEV", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device",
                                             "expires_in": 1800, "interval": 5})  # fmt: skip
        grant = form.get("grant_type")
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            self.device_polls += 1
            if self.device_polls < self.approve_after:
                return httpx.Response(428, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "acc", "expires_in": 3599, "refresh_token": "ref", "scope": "youtube"})
        if grant == "authorization_code":
            return httpx.Response(200, json={"access_token": "acc-web", "expires_in": 3599, "refresh_token": "ref-web"})
        if grant == "refresh_token":
            if form["refresh_token"] == "revoked":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": "acc-new", "expires_in": 3599})
        return httpx.Response(400, json={"error": "unsupported_grant_type"})


def test_device_flow_and_refresh_send_the_secret_as_basic_auth():
    g = FakeGoogle()
    oauth = GoogleOAuth("cid.apps.googleusercontent.com", "s3cret", token_base="https://oauth.test", transport=httpx.MockTransport(g.handler))
    dc = oauth.device_code()
    assert (dc.user_code, dc.verification_url, dc.interval) == ("ABCD-EFGH", "https://www.google.com/device", 5)
    with pytest.raises(DevicePending):
        oauth.poll_device("DEV")
    tokens = oauth.poll_device("DEV")
    assert tokens.refresh_token == "ref" and tokens.access_token == "acc"
    assert oauth.refresh("ref").access_token == "acc-new"
    with pytest.raises(PublishError) as e:
        oauth.refresh("revoked")
    assert e.value.kind == "auth" and "다시 연결" in str(e.value)
    token_calls = [r for r in g.requests if r[0] == "/token"]
    assert all(auth and auth.startswith("Basic ") for _, _, auth in token_calls)
    assert all("client_secret" not in form for _, form, _ in token_calls)
    url = oauth.authorization_url("https://clipper.test/api/channels/oauth/callback", "st8")
    assert "access_type=offline" in url and "state=st8" in url and "prompt=consent" in url and "auth%2Fyoutube" in url
