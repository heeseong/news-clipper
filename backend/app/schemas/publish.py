"""Channels and uploads (spec 8: /api/channels, /api/publish-tasks)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    name: str
    remote_channel_id: str
    url: str | None
    avatar_url: str | None
    status: str
    last_error: str | None
    connected_at: datetime | None


class ChannelsOut(BaseModel):
    channels: list[ChannelOut]
    modes: list[str]  # connect methods available here: fake | web | device


class ConnectIn(BaseModel):
    mode: str = Field(pattern="^(fake|web|device)$")


class ConnectOut(BaseModel):
    """A connection in progress (web/device) or done (fake: `channel` right away)."""

    session_id: str | None = None
    kind: str
    status: str  # pending | connected | failed
    auth_url: str | None = None  # web: open this
    user_code: str | None = None  # device: enter this at verification_url
    verification_url: str | None = None
    interval: int = 5
    expires_at: datetime | None = None
    error: str | None = None
    channel: ChannelOut | None = None


class PublishTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clip_id: int
    channel_id: int
    channel_name: str = ""
    status: str
    privacy: str
    publish_at: datetime | None
    remote_id: str | None
    remote_url: str | None
    attempts: int
    next_retry_at: datetime | None
    last_error: str | None
    error_kind: str | None
    file_size: int | None
    bytes_sent: int
    thumbnail_status: str | None
    thumbnail_error: str | None
    remote_status: dict[str, Any]
    checked_at: datetime | None
    uploaded_at: datetime | None
    published_at: datetime | None
    next_check_at: datetime | None = None
    thumbnail_next_at: datetime | None = None
    sync_status: str | None = None  # pending | syncing | ok | failed (edits after the upload)
    sync_error: str | None = None
    synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Dashboard rows
    job_id: int | None = None
    item_no: int | None = None
    title: str | None = None


class ChannelSummaryOut(BaseModel):
    channel: ChannelOut
    queued: int = 0
    uploading: int = 0
    scheduled_today: int = 0  # publish_at today (KST)
    published_today: int = 0
    failed: int = 0


class QuotaOut(BaseModel):
    platform: str
    day: str  # quota day (Pacific time)
    used: int
    limit: int
    resets_at: datetime
    uploads_left: int


class PublishSummaryOut(BaseModel):
    channels: list[ChannelSummaryOut]
    quota: list[QuotaOut]
