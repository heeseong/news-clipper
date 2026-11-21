"""Clips (spec 8): ranges, sentences, metadata and thumbnails (read). Review actions: api/review.py."""

from __future__ import annotations

from datetime import UTC, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import object_session

from app.api.deps import DbSession
from app.models import AlignedSentence, Clip, Job, RundownItem
from app.review.service import EDITABLE, LOCK_SECONDS, approval_problems, lock_holder
from app.schemas.newsroom import AlignedSentenceOut, ClipDetailOut, ClipMetadataOut, ClipOut, ThumbnailOut
from app.storage import JobFiles
from app.thumbnails.service import clip_kind

router = APIRouter(prefix="/api", tags=["clips"])


def _version(dt) -> str:
    return str(int(dt.timestamp())) if dt else "0"


def thumbnail_url(clip: Clip) -> str | None:
    """The rendered thumbnail (selected frame + text); ?v= changes when it is re-rendered."""
    path = JobFiles(clip.job_id).thumbnail(clip.id)
    if not path.exists():
        return None
    return f"/media/clips/{clip.id}/thumbnail.jpg?v={int(path.stat().st_mtime)}"


def clip_out(clip: Clip, model: type[ClipOut] = ClipOut) -> ClipOut:
    # Not model.model_validate(clip): `Clip.metadata` is SQLAlchemy's table MetaData.
    out = model.model_validate(ClipOut.model_validate(clip).model_dump())
    item: RundownItem = clip.rundown_item
    out.item_no, out.title, out.reporter = item.item_no, item.title, item.reporter
    out.planned_duration = item.planned_duration
    out.youtube_title = clip.metadata_.title if clip.metadata_ else None
    out.thumbnail_url = thumbnail_url(clip)
    out.kind = clip_kind(clip)
    holder = lock_holder(clip)
    out.locked_by_name = holder.name if holder else None
    return out


def clip_detail(clip: Clip) -> ClipDetailOut:
    out = clip_out(clip, ClipDetailOut)
    assert isinstance(out, ClipDetailOut)
    out.metadata = ClipMetadataOut.model_validate(clip.metadata_) if clip.metadata_ else None
    out.thumbnails = [
        ThumbnailOut.model_validate(t).model_copy(update={"url": f"/media/thumbnails/{t.id}?v={_version(t.updated_at)}"})
        for t in sorted(clip.thumbnails, key=lambda t: (t.source == "candidate", t.rank, t.id))
    ]
    session = object_session(clip)
    out.approval_problems = approval_problems(session, clip) if clip.status in EDITABLE else []
    if out.locked_by_name and clip.locked_at:
        locked_at = clip.locked_at if clip.locked_at.tzinfo else clip.locked_at.replace(tzinfo=UTC)
        out.lock_expires_at = locked_at + timedelta(seconds=LOCK_SECONDS)
    return out


def _clip(db, clip_id: int) -> Clip:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "클립을 찾을 수 없습니다")
    return clip


@router.get("/jobs/{job_id}/clips", response_model=list[ClipOut])
def list_clips(job_id: int, db: DbSession) -> list[ClipOut]:
    if db.get(Job, job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    clips = db.scalars(
        select(Clip).join(RundownItem).where(Clip.job_id == job_id).order_by(RundownItem.item_no)
    ).all()
    return [clip_out(c) for c in clips]


@router.get("/clips/{clip_id}", response_model=ClipDetailOut)
def get_clip(clip_id: int, db: DbSession) -> ClipDetailOut:
    return clip_detail(_clip(db, clip_id))


@router.get("/clips/{clip_id}/sentences", response_model=list[AlignedSentenceOut])
def clip_sentences(clip_id: int, db: DbSession) -> list[AlignedSentence]:
    clip = _clip(db, clip_id)
    return list(
        db.scalars(
            select(AlignedSentence)
            .where(AlignedSentence.rundown_item_id == clip.rundown_item_id)
            .order_by(AlignedSentence.seq)
        )
    )


@router.get("/clips/{clip_id}/thumbnails", response_model=list[ThumbnailOut])
def clip_thumbnails(clip_id: int, db: DbSession) -> list[ThumbnailOut]:
    return clip_detail(_clip(db, clip_id)).thumbnails


@router.post("/clips/{clip_id}/metadata/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate_metadata(clip_id: int, db: DbSession) -> dict:
    """Script defaults and LLM candidates again (plan 4.6). Runs on the `ai` queue; the job's
    event stream announces the result. Publishing settings (privacy, schedule) are kept."""
    from app.pipeline.tasks import regenerate_clip_metadata

    clip = _clip(db, clip_id)
    regenerate_clip_metadata.apply_async(args=(clip.id,), queue="ai")
    return {"clip_id": clip.id, "queued": True}
