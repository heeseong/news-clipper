"""Proxy video, sprite and waveform for the browser (HTTP Range supported by FileResponse)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.storage import PUBLIC_MEDIA, JobFiles

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/jobs/{job_id}/{name}")
def job_media(job_id: int, name: str) -> FileResponse:
    attr = PUBLIC_MEDIA.get(name)
    if attr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    path = getattr(JobFiles(job_id), attr)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "아직 만들어지지 않았습니다")
    # Outputs are rewritten when a step is retried, so let the browser revalidate.
    return FileResponse(path, headers={"Cache-Control": "no-cache"})
