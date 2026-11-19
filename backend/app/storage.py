"""Storage layout (spec 6.2). All paths derive from settings.storage_root.

uploads/{upload_id}            tus upload in progress (+ .json info)
jobs/{job_id}/source/original.{ext}
jobs/{job_id}/source/probe.json
jobs/{job_id}/proxy/proxy.mp4, sprite.jpg, sprite.json, waveform.json
jobs/{job_id}/audio/align.wav
jobs/{job_id}/analysis/scenes.json, silences.json
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def root() -> Path:
    return get_settings().storage_root


def uploads_dir() -> Path:
    return root() / "uploads"


def job_dir(job_id: int) -> Path:
    return root() / "jobs" / str(job_id)


class JobFiles:
    """Well-known files of one job."""

    def __init__(self, job_id: int) -> None:
        self.dir = job_dir(job_id)

    def source(self, ext: str) -> Path:
        return self.dir / "source" / f"original.{ext.lower().lstrip('.')}"

    @property
    def probe(self) -> Path:
        return self.dir / "source" / "probe.json"

    @property
    def proxy(self) -> Path:
        return self.dir / "proxy" / "proxy.mp4"

    @property
    def sprite(self) -> Path:
        return self.dir / "proxy" / "sprite.jpg"

    @property
    def sprite_info(self) -> Path:
        return self.dir / "proxy" / "sprite.json"

    @property
    def waveform(self) -> Path:
        return self.dir / "proxy" / "waveform.json"

    @property
    def align_audio(self) -> Path:
        return self.dir / "audio" / "align.wav"

    @property
    def scenes(self) -> Path:
        return self.dir / "analysis" / "scenes.json"

    @property
    def silences(self) -> Path:
        return self.dir / "analysis" / "silences.json"


# Files the browser may fetch via /media/jobs/{id}/{name}
PUBLIC_MEDIA = {
    "proxy.mp4": "proxy",
    "sprite.jpg": "sprite",
    "sprite.json": "sprite_info",
    "waveform.json": "waveform",
}
