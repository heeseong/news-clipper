"""Storage layout (spec 6.2). All paths derive from settings.storage_root.

uploads/{upload_id}            tus upload in progress (+ .json info)
jobs/{job_id}/source/original.{ext}
jobs/{job_id}/source/probe.json
jobs/{job_id}/proxy/proxy.mp4, sprite.jpg, sprite.json, waveform.json
jobs/{job_id}/audio/align.wav
jobs/{job_id}/analysis/scenes.json, silences.json, logprobs.npz, alignment.json
jobs/{job_id}/newsroom/rundown.json, scripts/{item_no}.json
jobs/{job_id}/clips/{clip_id}/thumb_candidates/{rank}.jpg, thumbnail.jpg
branding/                      logos and fonts for thumbnail templates
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

    @property
    def logprobs(self) -> Path:
        """Acoustic model output for align.wav (cache: computing it takes minutes)."""
        return self.dir / "analysis" / "logprobs.npz"

    @property
    def alignment(self) -> Path:
        """Item alignments and clip boundaries (debugging, eval/tools/score_alignment.py)."""
        return self.dir / "analysis" / "alignment.json"

    @property
    def rundown(self) -> Path:
        """Raw MARS-L responses (002 + 003 `data`) as fetched."""
        return self.dir / "newsroom" / "rundown.json"

    @property
    def scripts_dir(self) -> Path:
        return self.dir / "newsroom" / "scripts"

    def script(self, item_no: int) -> Path:
        """Parsed item (RundownItem with its ParsedScript)."""
        return self.scripts_dir / f"{item_no}.json"


    @property
    def clips_dir(self) -> Path:
        return self.dir / "clips"

    def clip_dir(self, clip_id: int) -> Path:
        return self.clips_dir / str(clip_id)

    def thumb_candidates_dir(self, clip_id: int) -> Path:
        """Candidate frames (1280x720, no text)."""
        return self.clip_dir(clip_id) / "thumb_candidates"

    def thumbnail(self, clip_id: int) -> Path:
        """The selected candidate with the text overlay (what is uploaded)."""
        return self.clip_dir(clip_id) / "thumbnail.jpg"


# Files the browser may fetch via /media/jobs/{id}/{name}
PUBLIC_MEDIA = {
    "proxy.mp4": "proxy",
    "sprite.jpg": "sprite",
    "sprite.json": "sprite_info",
    "waveform.json": "waveform",
}
