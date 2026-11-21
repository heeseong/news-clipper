"""Connecting channels (spec 5.9, plan 7.2): OAuth sessions, token storage, disconnecting."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Channel, ChannelStatus, OAuthSession, PublishStatus, PublishTask, User
from app.models.base import utcnow

from . import crypto
from .base import PublishError
from .factory import get_oauth, publisher_for
from .oauth import DevicePending, Tokens

WEB_SESSION_MINUTES = 15
IN_FLIGHT = (PublishStatus.QUEUED, PublishStatus.UPLOADING)


class ChannelError(Exception):
    """A request that cannot be done (HTTP 409/422)."""


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


class _StaticToken:
    def __init__(self, token: str) -> None:
        self.token = token

    def get(self, force_refresh: bool = False) -> str:
        return self.token


def fetch_channel_info(access_token: str) -> dict:
    """The channel of the account that just authorized (YouTube channels.list mine=true)."""
    from .youtube import YouTubePublisher

    s = get_settings()
    return YouTubePublisher(_StaticToken(access_token), api_url=s.youtube_api_url, chunk_mb=s.youtube_chunk_mb).channel_info()


def modes() -> list[str]:
    s = get_settings()
    if s.publisher == "fake":
        return ["fake"]
    out = ["device"] if s.google_client_id else []
    if s.google_client_id and s.google_oauth_redirect_uri:
        out.insert(0, "web")
    return out


def connect_fake(session: Session, user: User | None) -> Channel:
    n = (session.scalar(select(func.count()).select_from(Channel).where(Channel.platform == "fake")) or 0) + 1
    ch = Channel(platform="fake", name=f"테스트 채널 {n}", remote_channel_id=f"fake-{uuid.uuid4().hex[:10]}",
                 url=None, status=ChannelStatus.ACTIVE, connected_by=user.id if user else None, connected_at=utcnow())  # fmt: skip
    session.add(ch)
    session.flush()
    return ch


def start(session: Session, mode: str, user: User | None) -> OAuthSession:
    if mode not in modes():
        raise ChannelError(f"이 방식으로는 연결할 수 없습니다: {mode} (가능: {', '.join(modes()) or '없음 — GOOGLE_CLIENT_ID 설정 필요'})")
    crypto.encrypt("check")  # fail early when TOKEN_ENCRYPTION_KEY is missing
    now = utcnow()
    if mode == "device":
        dc = get_oauth().device_code()
        row = OAuthSession(id=uuid.uuid4().hex, kind="device", device_code=crypto.encrypt(dc.device_code),
                           user_code=dc.user_code, verification_url=dc.verification_url, interval=dc.interval,
                           expires_at=now + timedelta(seconds=dc.expires_in), next_poll_at=now + timedelta(seconds=dc.interval),
                           created_by=user.id if user else None)  # fmt: skip
    else:
        row = OAuthSession(id=secrets.token_urlsafe(24), kind="web", expires_at=now + timedelta(minutes=WEB_SESSION_MINUTES),
                           created_by=user.id if user else None)  # fmt: skip
    session.add(row)
    session.flush()
    return row


def authorization_url(row: OAuthSession) -> str | None:
    if row.kind != "web":
        return None
    return get_oauth().authorization_url(get_settings().google_oauth_redirect_uri or "", row.id)


def _fail(row: OAuthSession, message: str) -> OAuthSession:
    row.status, row.error = "failed", message
    return row


def poll(session: Session, row: OAuthSession, now: datetime | None = None) -> OAuthSession:
    """Device flow: ask Google once whether the admin has approved (at most every `interval` s)."""
    now = now or utcnow()
    if row.status != "pending" or row.kind != "device":
        return row
    if _aware(row.expires_at) <= now:
        return _fail(row, "코드가 만료됐습니다. 다시 연결해 주세요")
    if row.next_poll_at and _aware(row.next_poll_at) > now:
        return row
    try:
        tokens = get_oauth().poll_device(crypto.decrypt(row.device_code))
    except DevicePending as e:
        if e.slow_down:
            row.interval += 5
        row.next_poll_at = now + timedelta(seconds=row.interval)
        return row
    except PublishError as e:
        if e.retryable:
            row.next_poll_at = now + timedelta(seconds=row.interval)
            row.error = str(e)
            return row
        return _fail(row, str(e))
    return _finish(session, row, tokens)


def finish_web(session: Session, state: str, code: str | None, error: str | None) -> OAuthSession:
    row = session.get(OAuthSession, state)
    if row is None or row.kind != "web":
        raise ChannelError("알 수 없는 연결 요청입니다 (state 불일치). 다시 연결해 주세요")
    if row.status != "pending":
        return row
    if _aware(row.expires_at) <= utcnow():
        return _fail(row, "연결 요청이 만료됐습니다. 다시 연결해 주세요")
    if error or not code:
        return _fail(row, "Google 계정에서 권한 요청을 거부했습니다" if error == "access_denied" else f"연결 실패: {error}")
    try:
        tokens = get_oauth().exchange_code(code, get_settings().google_oauth_redirect_uri or "")
    except PublishError as e:
        return _fail(row, str(e))
    return _finish(session, row, tokens)


def _finish(session: Session, row: OAuthSession, tokens: Tokens) -> OAuthSession:
    try:
        info = fetch_channel_info(tokens.access_token)
    except PublishError as e:
        return _fail(row, str(e))
    ch = session.scalar(select(Channel).where(Channel.platform == "youtube", Channel.remote_channel_id == info["id"]))
    if not tokens.refresh_token and (ch is None or not ch.refresh_token):
        return _fail(row, "refresh token을 받지 못했습니다. Google 계정의 앱 권한을 지운 뒤 다시 연결해 주세요")
    if ch is None:
        ch = Channel(platform="youtube", remote_channel_id=info["id"])
        session.add(ch)
    ch.name, ch.url, ch.avatar_url = info["title"], info["url"], info.get("avatar_url")
    if tokens.refresh_token:
        ch.refresh_token = crypto.encrypt(tokens.refresh_token)
    ch.access_token = crypto.encrypt(tokens.access_token)
    ch.access_token_expires_at = utcnow() + timedelta(seconds=tokens.expires_in)
    ch.scopes = tokens.scope
    ch.status, ch.last_error = ChannelStatus.ACTIVE, None
    ch.connected_by, ch.connected_at = row.created_by, utcnow()
    session.flush()
    row.status, row.channel_id, row.error, row.device_code = "connected", ch.id, None, None
    session.flush()
    return row


def disconnect(session: Session, ch: Channel) -> None:
    """Forget the tokens (and revoke them at Google). The channel stays for the upload history."""
    busy = session.scalar(
        select(func.count()).select_from(PublishTask).where(PublishTask.channel_id == ch.id, PublishTask.status.in_(IN_FLIGHT))
    )
    if busy:
        raise ChannelError(f"업로드 대기·진행 중인 작업이 {busy}건 있습니다. 끝나거나 취소한 뒤 해제하세요")
    if ch.platform == "youtube" and ch.refresh_token:
        try:
            get_oauth().revoke(crypto.decrypt(ch.refresh_token))
        except (PublishError, crypto.TokenKeyMissing):
            pass
    ch.refresh_token = ch.access_token = ch.access_token_expires_at = None
    ch.status = ChannelStatus.DISCONNECTED
    session.flush()


def active_channels(session: Session, ids: list[int]) -> list[Channel]:
    if not ids:
        return []
    found = {c.id: c for c in session.scalars(select(Channel).where(Channel.id.in_(ids)))}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ChannelError(f"없는 채널입니다: {missing}")
    inactive = [found[i].name for i in ids if found[i].status != ChannelStatus.ACTIVE]
    if inactive:
        raise ChannelError(f"연결되지 않은 채널입니다: {', '.join(inactive)}")
    return [found[i] for i in ids]


__all__ = ["publisher_for"]
