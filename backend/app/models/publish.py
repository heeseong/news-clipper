"""Channels and uploads (spec 5.9, 7; plan M7)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

from .base import TimestampMixin, str_enum


class ChannelStatus(StrEnum):
    ACTIVE = "active"
    REAUTH_REQUIRED = "reauth_required"  # the refresh token was revoked or expired: connect again
    DISCONNECTED = "disconnected"  # tokens removed; kept for the upload history


class Channel(TimestampMixin, Base):
    """A connected upload destination. `platform` picks the Publisher (youtube | fake).

    Tokens are encrypted with TOKEN_ENCRYPTION_KEY (app/publish/crypto.py) and never leave the server.
    """

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("platform", "remote_channel_id", name="uq_channels_platform_remote_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    remote_channel_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[ChannelStatus] = mapped_column(str_enum(ChannelStatus), default=ChannelStatus.ACTIVE)
    refresh_token: Mapped[str | None] = mapped_column(Text)  # encrypted
    access_token: Mapped[str | None] = mapped_column(Text)  # encrypted
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(String(512))
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthSession(TimestampMixin, Base):
    """A channel connection in progress: the browser redirect (`web`) or a device code (`device`)."""

    __tablename__ = "oauth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # also the OAuth `state`
    kind: Mapped[str] = mapped_column(String(16))  # web | device
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | connected | failed
    device_code: Mapped[str | None] = mapped_column(Text)  # encrypted
    user_code: Mapped[str | None] = mapped_column(String(32))
    verification_url: Mapped[str | None] = mapped_column(String(255))
    interval: Mapped[int] = mapped_column(Integer, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PublishStatus(StrEnum):
    QUEUED = "QUEUED"  # waiting for the publish worker (or for next_retry_at)
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"  # the file is in; the platform's state is not known yet
    PROCESSING = "PROCESSING"  # the platform is processing the video
    SCHEDULED = "SCHEDULED"  # private until publish_at
    PUBLISHED = "PUBLISHED"  # in its final visibility (public, unlisted or private)
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"  # removed by a user (the remote video is deleted)


ACTIVE_TASK = (
    PublishStatus.QUEUED,
    PublishStatus.UPLOADING,
    PublishStatus.UPLOADED,
    PublishStatus.PROCESSING,
    PublishStatus.SCHEDULED,
    PublishStatus.PUBLISHED,
    PublishStatus.FAILED,
)


class PublishTask(TimestampMixin, Base):
    """One upload of one clip to one channel (spec 7). A cancelled task is kept for the history."""

    __tablename__ = "publish_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    status: Mapped[PublishStatus] = mapped_column(str_enum(PublishStatus), default=PublishStatus.QUEUED, index=True)
    # Publishing settings at upload time (the clip's metadata may change afterwards).
    privacy: Mapped[str] = mapped_column(String(16), default="private")
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_id: Mapped[str | None] = mapped_column(String(64))
    remote_url: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(16))  # network | server | auth | quota | invalid | remote
    resumable_uri: Mapped[str | None] = mapped_column(Text)  # resume an interrupted upload
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0)
    thumbnail_status: Mapped[str | None] = mapped_column(String(16))  # ok | failed | none
    thumbnail_error: Mapped[str | None] = mapped_column(Text)
    remote_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # last status check
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_units: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    channel: Mapped[Channel] = relationship()


class QuotaUsage(Base):
    """API units used per platform and quota day (YouTube resets at midnight Pacific time).

    The YouTube quota belongs to the Google Cloud project, so it is shared by every channel."""

    __tablename__ = "quota_usage"

    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
