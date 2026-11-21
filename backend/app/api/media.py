"""Proxy video, sprite, waveform and thumbnails for the browser (HTTP Range supported by FileResponse)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.api.deps import DbSession
from app.models import Clip, Thumbnail
from app.storage import PUBLIC_MEDIA, JobFiles

router = APIRouter(prefix="/media", tags=["media"])

# Outputs are rewritten when a step is retried, so let the browser revalidate.
NO_CACHE = {"Cache-Control": "no-cache"}


def _file(path: Path) -> FileResponse:
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "아직 만들어지지 않았습니다")
    return FileResponse(path, headers=NO_CACHE)


@router.get("/jobs/{job_id}/{name}")
def job_media(job_id: int, name: str) -> FileResponse:
    attr = PUBLIC_MEDIA.get(name)
    if attr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return _file(getattr(JobFiles(job_id), attr))


@router.get("/clips/{clip_id}/thumbnail.jpg")
def clip_thumbnail(clip_id: int, db: DbSession) -> FileResponse:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return _file(JobFiles(clip.job_id).thumbnail(clip_id))


@router.get("/thumbnails/{thumbnail_id}")
def thumbnail_frame(thumbnail_id: int, db: DbSession) -> FileResponse:
    thumb = db.get(Thumbnail, thumbnail_id)
    if thumb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return _file(Path(thumb.path))


@router.get("/clips/{clip_id}/final.mp4")
def clip_final(clip_id: int, db: DbSession, download: bool = False) -> FileResponse:
    """The rendered clip (M6): plays in the browser, or downloads for manual posting (?download=1)."""
    clip = db.get(Clip, clip_id)
    if clip is None or not clip.render_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "렌더된 클립이 없습니다")
    path = Path(clip.render_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "렌더 파일이 없습니다")
    if not download:
        return FileResponse(path, media_type="video/mp4", headers=NO_CACHE)
    item = clip.rundown_item
    name = f"{item.item_no:02d}_{item.title}"[:80].replace("/", "_") + ".mp4"
    return FileResponse(path, media_type="video/mp4", filename=name, headers=NO_CACHE)
