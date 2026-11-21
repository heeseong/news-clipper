"""Publishers and OAuth clients from settings; tests swap them with set_publisher / set_oauth."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Channel, ChannelStatus
from app.models.base import utcnow

from . import crypto
from .base import Publisher, PublishError
from .fake import FakePublisher
from .oauth import GoogleOAuth

_publisher_override: Callable[[Channel], Publisher] | None = None
_oauth_override: GoogleOAuth | None = None


def set_publisher(factory: Callable[[Channel], Publisher] | None) -> None:
    """Tests: build publishers with this function instead (None restores the default)."""
    global _publisher_override
    _publisher_override = factory


def set_oauth(oauth: GoogleOAuth | None) -> None:
    global _oauth_override
    _oauth_override = oauth


def get_oauth() -> GoogleOAuth:
    if _oauth_override is not None:
        return _oauth_override
    s = get_settings()
    secret = s.google_client_secret.get_secret_value() if s.google_client_secret else ""
    return GoogleOAuth(s.google_client_id or "", secret, token_base=s.google_oauth_url, auth_url=s.google_auth_url)


class ChannelTokens:
    """Access tokens of a channel: the stored one while it is valid, else refreshed and stored."""

    def __init__(self, channel_id: int, oauth: Callable[[], GoogleOAuth] = get_oauth) -> None:
        self.channel_id = channel_id
        self._oauth = oauth

    def get(self, force_refresh: bool = False) -> str:
        with session_scope() as s:
            ch = s.get(Channel, self.channel_id)
            if ch is None or ch.status == ChannelStatus.DISCONNECTED:
                raise PublishError("채널 연결이 해제됐습니다", "auth")
            expires = ch.access_token_expires_at
            if expires is not None and expires.tzinfo is None:
                from datetime import UTC

                expires = expires.replace(tzinfo=UTC)
            if not force_refresh and ch.access_token and expires and expires > utcnow() + timedelta(seconds=60):
                return crypto.decrypt(ch.access_token)
            refresh = crypto.decrypt(ch.refresh_token)
        if not refresh:
            self._mark_reauth("저장된 refresh token이 없습니다")
            raise PublishError("채널을 다시 연결해야 합니다 (refresh token 없음)", "auth")
        try:
            tokens = self._oauth().refresh(refresh)
        except PublishError as e:
            if e.kind == "auth":
                self._mark_reauth(str(e))
            raise
        with session_scope() as s:
            ch = s.get(Channel, self.channel_id)
            ch.access_token = crypto.encrypt(tokens.access_token)
            ch.access_token_expires_at = utcnow() + timedelta(seconds=tokens.expires_in)
            if tokens.refresh_token:
                ch.refresh_token = crypto.encrypt(tokens.refresh_token)
        return tokens.access_token

    def _mark_reauth(self, error: str) -> None:
        with session_scope() as s:
            ch = s.get(Channel, self.channel_id)
            if ch is not None and ch.status == ChannelStatus.ACTIVE:
                ch.status, ch.last_error = ChannelStatus.REAUTH_REQUIRED, error


def publisher_for(channel: Channel) -> Publisher:
    if _publisher_override is not None:
        return _publisher_override(channel)
    if channel.platform == "fake":
        return FakePublisher(channel.remote_channel_id)
    if channel.platform == "youtube":
        from .youtube import YouTubePublisher

        s = get_settings()
        return YouTubePublisher(ChannelTokens(channel.id), api_url=s.youtube_api_url, chunk_mb=s.youtube_chunk_mb)
    raise PublishError(f"지원하지 않는 플랫폼입니다: {channel.platform}", "invalid")
