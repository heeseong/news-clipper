"""YouTube Data API v3 Publisher (spec 5.9), plain HTTP (httpx).

- videos.insert resumable upload: the session URI is handed to `on_session` (stored on the task),
  chunks are multiples of 8 MB, an interrupted chunk is resumed in place a few times and, if the
  network stays down, the next attempt continues from the platform's offset.
- Scheduling: privacyStatus=private + publishAt (UTC); the platform switches it to `privacy`.
- thumbnails.set, categoryId 25 (News & Politics), defaultLanguage ko.
- A 401 refreshes the access token once and repeats the request.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from .base import Progress, PublishError, PublishMeta, PublishResult, RemoteStatus

log = logging.getLogger(__name__)

MB = 1024 * 1024
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"}
RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX = 500


class TokenSource(Protocol):
    def get(self, force_refresh: bool = False) -> str: ...


def error_from_response(r: httpx.Response, action: str) -> PublishError:
    reason, message = None, ""
    try:
        err = r.json().get("error", {})
        message = err.get("message", "")
        errors = err.get("errors") or []
        reason = errors[0].get("reason") if errors else err.get("status")
    except (ValueError, AttributeError):
        message = r.text[:200]
    code = r.status_code
    text = f"{action}: YouTube {code} {reason or ''} {message}".strip()
    if code == 401:
        return PublishError(text, "auth", reason=reason)
    if reason in QUOTA_REASONS:
        return PublishError(text, "quota", reason=reason)
    if code == 429 or code >= 500 or reason in RATE_REASONS:
        return PublishError(text, "server", reason=reason)
    return PublishError(text, "invalid", reason=reason)


def _rfc3339(dt: datetime) -> str:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text: str) -> str:
    return text.replace("<", "＜").replace(">", "＞")  # YouTube refuses angle brackets in titles


def fit_tags(tags: list[str], limit: int = TAGS_MAX) -> list[str]:
    """YouTube counts tags with commas between them and quotes around tags with spaces."""
    out, total = [], 0
    for t in tags:
        t = _clean(t).strip().replace(",", " ")
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if total + cost > limit:
            break
        out.append(t)
        total += cost
    return out


def _truncate_bytes(text: str, limit: int) -> str:
    data = text.encode()
    return text if len(data) <= limit else data[:limit].decode(errors="ignore")


def video_body(meta: PublishMeta, remote_id: str | None = None) -> dict:
    status: dict = {"selfDeclaredMadeForKids": meta.made_for_kids}
    if meta.publish_at is not None:
        status |= {"privacyStatus": "private", "publishAt": _rfc3339(meta.publish_at)}
    else:
        status["privacyStatus"] = meta.privacy
    body = {
        "snippet": {
            "title": _clean(meta.title)[:TITLE_MAX],
            "description": _truncate_bytes(_clean(meta.description), DESCRIPTION_MAX_BYTES),
            "tags": fit_tags(meta.tags),
            "categoryId": meta.category_id,
            "defaultLanguage": meta.language,
            "defaultAudioLanguage": meta.language,
        },
        "status": status,
    }
    if remote_id:
        body["id"] = remote_id
    return body


def watch_url(remote_id: str) -> str:
    return f"https://www.youtube.com/watch?v={remote_id}"


def status_from_video(item: dict | None) -> RemoteStatus:
    if not item:
        return RemoteStatus(exists=False, upload_status="deleted")
    st = item.get("status", {})
    publish_at = st.get("publishAt")
    return RemoteStatus(
        exists=True,
        upload_status=st.get("uploadStatus"),
        privacy=st.get("privacyStatus"),
        publish_at=datetime.fromisoformat(publish_at.replace("Z", "+00:00")) if publish_at else None,
        failure_reason=st.get("failureReason"),
        rejection_reason=st.get("rejectionReason"),
        raw={"status": st, "processingDetails": item.get("processingDetails", {})},
    )


class YouTubePublisher:
    platform = "youtube"

    def __init__(self, tokens: TokenSource, *, api_url: str = "https://www.googleapis.com", chunk_mb: int = 16,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 in_place_resumes: int = 3) -> None:  # fmt: skip
        if chunk_mb % 8:
            raise ValueError("chunk_mb must be a multiple of 8")
        self.tokens = tokens
        self.api = api_url.rstrip("/")
        self.chunk = chunk_mb * MB
        self.sleep = sleep
        self.in_place_resumes = in_place_resumes
        self._http = httpx.Client(transport=transport, timeout=httpx.Timeout(30.0, read=300.0, write=300.0))

    # --- HTTP ---

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Authorized request; a 401 refreshes the token once. Network errors → PublishError(network)."""
        for attempt in (0, 1):
            headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {self.tokens.get(force_refresh=attempt == 1)}"}
            try:
                r = self._http.request(method, url, headers=headers, **kw)
            except httpx.TransportError as e:
                raise PublishError(f"YouTube에 연결하지 못했습니다: {e}", "network") from e
            if r.status_code != 401:
                return r
            kw["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
        return r

    def channel_info(self) -> dict:
        """The authorized account's channel: {id, title, url, avatar_url}."""
        r = self._request("GET", f"{self.api}/youtube/v3/channels", params={"part": "snippet", "mine": "true"})
        if r.status_code != 200:
            raise error_from_response(r, "채널 조회")
        items = r.json().get("items") or []
        if not items:
            raise PublishError("이 Google 계정에는 YouTube 채널이 없습니다 (채널을 고르는 화면에서 채널을 선택하세요)", "invalid")
        ch = items[0]
        sn = ch.get("snippet", {})
        custom = sn.get("customUrl")
        return {
            "id": ch["id"],
            "title": sn.get("title") or ch["id"],
            "url": f"https://www.youtube.com/{custom}" if custom else f"https://www.youtube.com/channel/{ch['id']}",
            "avatar_url": (sn.get("thumbnails", {}).get("default") or {}).get("url"),
        }

    # --- upload ---

    def _start_session(self, size: int, meta: PublishMeta) -> str:
        params = {"uploadType": "resumable", "part": "snippet,status",
                  "notifySubscribers": "true" if meta.notify_subscribers else "false"}  # fmt: skip
        r = self._request(
            "POST", f"{self.api}/upload/youtube/v3/videos", params=params, json=video_body(meta),
            headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
        )  # fmt: skip
        if r.status_code != 200 or "location" not in r.headers:
            raise error_from_response(r, "업로드 시작")
        return r.headers["location"]

    @staticmethod
    def _next_offset(r: httpx.Response) -> int:
        rng = r.headers.get("range")  # "bytes=0-1048575"
        return int(rng.rsplit("-", 1)[1]) + 1 if rng else 0

    def _query_offset(self, uri: str, size: int) -> int | dict:
        """Where the session stands: the next byte to send, or the video resource when done."""
        r = self._request("PUT", uri, headers={"Content-Range": f"bytes */{size}", "Content-Length": "0"})
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 308:
            return self._next_offset(r)
        if r.status_code in (404, 410):
            raise _SessionGone()
        raise error_from_response(r, "업로드 재개")

    def upload(self, video: Path, meta: PublishMeta, *, resume_uri: str | None = None,
               on_session: Callable[[str], None] | None = None, on_progress: Progress | None = None) -> PublishResult:  # fmt: skip
        size = video.stat().st_size
        uri, offset = resume_uri, 0
        if uri:
            try:
                state = self._query_offset(uri, size)
                if isinstance(state, dict):
                    return self._result(state)
                offset = state
                log.info("resuming upload", extra={"offset": offset, "size": size})
            except _SessionGone:
                uri = None
        if not uri:
            uri, offset = self._start_session(size, meta), 0
            if on_session:
                on_session(uri)
        resumes = 0
        with video.open("rb") as f:
            while True:
                f.seek(offset)
                data = f.read(self.chunk)
                end = offset + len(data) - 1
                try:
                    r = self._request("PUT", uri, content=data,
                                      headers={"Content-Range": f"bytes {offset}-{end}/{size}", "Content-Type": "video/mp4"})  # fmt: skip
                    if r.status_code in (200, 201):
                        if on_progress:
                            on_progress(size, size)
                        return self._result(r.json())
                    if r.status_code == 308:
                        offset = self._next_offset(r)
                        if on_progress:
                            on_progress(offset, size)
                        continue
                    if r.status_code in (404, 410):
                        raise PublishError("업로드 세션이 만료됐습니다 (다음 시도에서 처음부터)", "server", reason="sessionExpired")
                    raise error_from_response(r, "업로드")
                except PublishError as e:
                    if not e.retryable or resumes >= self.in_place_resumes:
                        raise
                    resumes += 1
                    self.sleep(min(30, 2**resumes))
                    try:
                        state = self._query_offset(uri, size)
                    except _SessionGone:
                        raise PublishError("업로드 세션이 만료됐습니다 (다음 시도에서 처음부터)", "server",
                                           reason="sessionExpired") from e  # fmt: skip
                    except PublishError:
                        continue  # still down: try the same chunk again
                    if isinstance(state, dict):
                        return self._result(state)
                    offset = state

    def _result(self, item: dict) -> PublishResult:
        return PublishResult(item["id"], watch_url(item["id"]), status_from_video(item))

    # --- other calls ---

    def set_thumbnail(self, remote_id: str, image: Path) -> None:
        r = self._request("POST", f"{self.api}/upload/youtube/v3/thumbnails/set",
                          params={"videoId": remote_id, "uploadType": "media"},
                          content=image.read_bytes(), headers={"Content-Type": "image/jpeg"})  # fmt: skip
        if r.status_code != 200:
            raise error_from_response(r, "썸네일")

    def get_status(self, remote_id: str) -> RemoteStatus:
        r = self._request("GET", f"{self.api}/youtube/v3/videos", params={"part": "status,processingDetails", "id": remote_id})
        if r.status_code != 200:
            raise error_from_response(r, "상태 확인")
        items = r.json().get("items") or []
        return status_from_video(items[0] if items else None)

    def update(self, remote_id: str, meta: PublishMeta) -> None:
        r = self._request("PUT", f"{self.api}/youtube/v3/videos", params={"part": "snippet,status"},
                          json=video_body(meta, remote_id))  # fmt: skip
        if r.status_code != 200:
            raise error_from_response(r, "메타데이터 수정")

    def delete(self, remote_id: str) -> None:
        r = self._request("DELETE", f"{self.api}/youtube/v3/videos", params={"id": remote_id})
        if r.status_code not in (200, 204, 404):
            raise error_from_response(r, "영상 삭제")


class _SessionGone(Exception):
    pass
