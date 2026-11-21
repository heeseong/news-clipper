"""Publisher interface (spec 5.9). One Publisher per connected channel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

ErrorKind = Literal["network", "server", "auth", "quota", "invalid", "remote"]


class PublishError(Exception):
    """An upload/API failure. `kind` decides what happens next (spec 5.10):

    network, server  retry later (the resumable session continues)
    auth             the channel must be connected again
    quota            wait for the quota day to reset (midnight Pacific time)
    invalid          bad request / metadata: fix it, then retry by hand
    remote           the platform refused or failed to process the video
    """

    def __init__(self, message: str, kind: ErrorKind = "server", *, reason: str | None = None) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.reason = reason

    @property
    def retryable(self) -> bool:
        return self.kind in ("network", "server")


@dataclass
class PublishMeta:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    privacy: str = "private"  # visibility once published: private | unlisted | public
    publish_at: datetime | None = None  # scheduled: private until then (UTC)
    category_id: str = "25"
    language: str = "ko"
    made_for_kids: bool = False
    notify_subscribers: bool = True


@dataclass
class PublishResult:
    remote_id: str
    url: str
    status: RemoteStatus | None = None


@dataclass
class RemoteStatus:
    exists: bool = True
    upload_status: str | None = None  # uploaded (processing) | processed | failed | rejected | deleted
    privacy: str | None = None
    publish_at: datetime | None = None
    failure_reason: str | None = None
    rejection_reason: str | None = None
    raw: dict = field(default_factory=dict)


Progress = Callable[[int, int], None]  # bytes sent, total


class Publisher(Protocol):
    platform: str

    def upload(
        self,
        video: Path,
        meta: PublishMeta,
        *,
        resume_uri: str | None = None,
        on_session: Callable[[str], None] | None = None,
        on_progress: Progress | None = None,
    ) -> PublishResult:
        """Upload the file. `on_session` receives the resumable session URI as soon as it exists
        (save it: a later call with `resume_uri` continues from where the platform stopped)."""
        ...

    def set_thumbnail(self, remote_id: str, image: Path) -> None: ...

    def get_status(self, remote_id: str) -> RemoteStatus: ...

    def update(self, remote_id: str, meta: PublishMeta) -> None: ...

    def delete(self, remote_id: str) -> None: ...
