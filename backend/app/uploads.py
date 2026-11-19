"""Resumable upload storage for the tus 1.0 protocol (core + creation + termination).

An upload is two files in storage/uploads/: `{id}.bin` (bytes received so far) and `{id}.json`
(declared length, current offset, metadata). When complete, job creation moves the .bin into
the job's source folder.
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app import storage

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
FINGERPRINT_SAMPLE = 64 * 1024 * 1024


class UploadInfo(BaseModel):
    id: str
    length: int
    offset: int = 0
    metadata: dict[str, str] = {}
    created_at: datetime

    @property
    def filename(self) -> str:
        return self.metadata.get("filename") or f"{self.id}.bin"

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower().lstrip(".")

    @property
    def complete(self) -> bool:
        return self.offset >= self.length


class UploadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_metadata(header: str | None) -> dict[str, str]:
    """`Upload-Metadata: filename d29ybGQ=,filetype dmlkZW8vbXA0` → {"filename": ..., ...}"""
    out: dict[str, str] = {}
    for pair in (header or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, value = pair.partition(" ")
        try:
            out[key] = base64.b64decode(value).decode("utf-8") if value else ""
        except (ValueError, UnicodeDecodeError) as e:
            raise UploadError(400, f"Upload-Metadata 값을 읽을 수 없습니다: {key}") from e
    return out


def _paths(upload_id: str) -> tuple[Path, Path]:
    if not _ID_RE.match(upload_id):
        raise UploadError(404, "업로드를 찾을 수 없습니다")
    d = storage.uploads_dir()
    return d / f"{upload_id}.bin", d / f"{upload_id}.json"


def data_path(upload_id: str) -> Path:
    return _paths(upload_id)[0]


def create(length: int, metadata: dict[str, str]) -> UploadInfo:
    storage.uploads_dir().mkdir(parents=True, exist_ok=True)
    info = UploadInfo(id=uuid.uuid4().hex, length=length, metadata=metadata, created_at=datetime.now(UTC))
    data, meta = _paths(info.id)
    data.touch()
    save(info)
    return info


def load(upload_id: str) -> UploadInfo:
    data, meta = _paths(upload_id)
    if not meta.exists():
        raise UploadError(404, "업로드를 찾을 수 없습니다")
    info = UploadInfo.model_validate_json(meta.read_text())
    # The data file is the source of truth for the offset (a crash may leave the JSON behind).
    info.offset = data.stat().st_size if data.exists() else 0
    return info


def save(info: UploadInfo) -> None:
    _, meta = _paths(info.id)
    tmp = meta.with_suffix(".json.tmp")
    tmp.write_text(info.model_dump_json())
    tmp.replace(meta)


def delete(upload_id: str) -> None:
    data, meta = _paths(upload_id)
    if not meta.exists():
        raise UploadError(404, "업로드를 찾을 수 없습니다")
    data.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)


def forget(upload_id: str) -> None:
    """Drop the upload record after its data file was moved into a job."""
    _, meta = _paths(upload_id)
    meta.unlink(missing_ok=True)


def fingerprint(path: Path) -> str:
    """SHA-256 of the size plus the first and last 64 MB: catches re-uploads of the same file
    without reading tens of GB."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(FINGERPRINT_SAMPLE))
        if size > 2 * FINGERPRINT_SAMPLE:
            f.seek(size - FINGERPRINT_SAMPLE)
            h.update(f.read(FINGERPRINT_SAMPLE))
        elif size > FINGERPRINT_SAMPLE:
            h.update(f.read())
    return h.hexdigest()
