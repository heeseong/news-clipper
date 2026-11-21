"""A pretend platform for development, E2E and tests (no network).

Each "video" is a JSON file under storage/fake_publisher/, so the API and the workers see the same
state. A scheduled video becomes public by itself once publish_at has passed, like YouTube.
Failures can be injected per call: FakePublisher(fail={"upload": [PublishError(...)]}).
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.models.base import utcnow
from app.storage import root

from .base import Progress, PublishError, PublishMeta, PublishResult, RemoteStatus

CHUNK = 256 * 1024


def _dir() -> Path:
    return root() / "fake_publisher"


class FakePublisher:
    platform = "fake"

    def __init__(self, channel_id: str = "fake", *, fail: dict[str, list[PublishError]] | None = None,
                 now: Callable[[], datetime] = utcnow) -> None:  # fmt: skip
        self.channel_id = channel_id
        self.fail = {k: list(v) for k, v in (fail or {}).items()}
        self.now = now
        self.calls: list[tuple[str, str | None]] = []

    def _maybe_fail(self, op: str) -> None:
        errors = self.fail.get(op)
        if errors:
            raise errors.pop(0)

    def _path(self, remote_id: str) -> Path:
        return _dir() / f"{remote_id}.json"

    def _load(self, remote_id: str) -> dict | None:
        p = self._path(remote_id)
        return json.loads(p.read_text()) if p.exists() else None

    def _save(self, remote_id: str, data: dict) -> None:
        _dir().mkdir(parents=True, exist_ok=True)
        self._path(remote_id).write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def upload(self, video: Path, meta: PublishMeta, *, resume_uri: str | None = None,
               on_session: Callable[[str], None] | None = None, on_progress: Progress | None = None) -> PublishResult:  # fmt: skip
        self.calls.append(("upload", resume_uri))
        size = video.stat().st_size
        session = resume_uri or f"fake-session-{uuid.uuid4().hex[:12]}"
        if on_session and not resume_uri:
            on_session(session)
        self._maybe_fail("upload")
        for sent in range(min(size, CHUNK), size + 1, CHUNK):
            if on_progress:
                on_progress(sent, size)
        if on_progress:
            on_progress(size, size)
        remote_id = "fake" + uuid.uuid4().hex[:11]
        self._save(remote_id, {
            "channel": self.channel_id, "title": meta.title, "description": meta.description, "tags": meta.tags,
            "privacy": meta.privacy, "publish_at": meta.publish_at.isoformat() if meta.publish_at else None,
            "category_id": meta.category_id, "language": meta.language, "size": size, "thumbnail": None,
            "uploaded_at": self.now().isoformat(),
        })  # fmt: skip
        return PublishResult(remote_id, f"https://example.invalid/watch?v={remote_id}", self.get_status(remote_id))

    def set_thumbnail(self, remote_id: str, image: Path) -> None:
        self.calls.append(("set_thumbnail", remote_id))
        self._maybe_fail("set_thumbnail")
        data = self._load(remote_id)
        if data is None:
            raise PublishError("영상을 찾을 수 없습니다", "invalid", reason="videoNotFound")
        shutil.copyfile(image, _dir() / f"{remote_id}.jpg")
        data["thumbnail"] = f"{remote_id}.jpg"
        self._save(remote_id, data)

    def get_status(self, remote_id: str) -> RemoteStatus:
        self.calls.append(("get_status", remote_id))
        self._maybe_fail("get_status")
        data = self._load(remote_id)
        if data is None:
            return RemoteStatus(exists=False, upload_status="deleted")
        publish_at = datetime.fromisoformat(data["publish_at"]) if data["publish_at"] else None
        if publish_at is not None and publish_at <= self.now():
            privacy, publish_at = data["privacy"], None  # the platform published it
        elif publish_at is not None:
            privacy = "private"
        else:
            privacy = data["privacy"]
        return RemoteStatus(True, "processed", privacy, publish_at, raw=data)

    def update(self, remote_id: str, meta: PublishMeta) -> None:
        self.calls.append(("update", remote_id))
        self._maybe_fail("update")
        data = self._load(remote_id)
        if data is None:
            raise PublishError("영상을 찾을 수 없습니다", "invalid", reason="videoNotFound")
        data.update(title=meta.title, description=meta.description, tags=meta.tags, privacy=meta.privacy,
                    publish_at=meta.publish_at.isoformat() if meta.publish_at else None)  # fmt: skip
        self._save(remote_id, data)

    def delete(self, remote_id: str) -> None:
        self.calls.append(("delete", remote_id))
        self._maybe_fail("delete")
        self._path(remote_id).unlink(missing_ok=True)
        (_dir() / f"{remote_id}.jpg").unlink(missing_ok=True)

    def video(self, remote_id: str) -> dict | None:
        """Tests: what the platform has."""
        return self._load(remote_id)
