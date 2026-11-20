"""Report clips (spec 8). Review actions (edit, approve) come with M5."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import AlignedSentence, Clip, Job, RundownItem
from app.schemas.newsroom import AlignedSentenceOut, ClipOut

router = APIRouter(prefix="/api", tags=["clips"])


def clip_out(clip: Clip) -> ClipOut:
    out = ClipOut.model_validate(clip)
    item: RundownItem = clip.rundown_item
    out.item_no, out.title, out.reporter = item.item_no, item.title, item.reporter
    out.planned_duration = item.planned_duration
    return out


@router.get("/jobs/{job_id}/clips", response_model=list[ClipOut])
def list_clips(job_id: int, db: DbSession) -> list[ClipOut]:
    if db.get(Job, job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    clips = db.scalars(
        select(Clip).join(RundownItem).where(Clip.job_id == job_id).order_by(RundownItem.item_no)
    ).all()
    return [clip_out(c) for c in clips]


@router.get("/clips/{clip_id}", response_model=ClipOut)
def get_clip(clip_id: int, db: DbSession) -> ClipOut:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "클립을 찾을 수 없습니다")
    return clip_out(clip)


@router.get("/clips/{clip_id}/sentences", response_model=list[AlignedSentenceOut])
def clip_sentences(clip_id: int, db: DbSession) -> list[AlignedSentence]:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "클립을 찾을 수 없습니다")
    return list(
        db.scalars(
            select(AlignedSentence)
            .where(AlignedSentence.rundown_item_id == clip.rundown_item_id)
            .order_by(AlignedSentence.seq)
        )
    )
