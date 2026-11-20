"""The thumbnails step (pipeline 7): candidates per clip and the selected one with its text.

The text comes from the metadata step, which runs in parallel. Both steps call `render_selected`
after committing their part, so whichever finishes second renders thumbnail.jpg.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.media.ffmpeg import ProbeResult
from app.models import AlignedSentence, Clip, ClipMetadata, Job, Thumbnail
from app.newsroom.models import ItemType, ParsedScript
from app.storage import JobFiles

from . import overlay
from .candidates import anchor_end_for, candidate_times, find_candidates

log = logging.getLogger(__name__)


@dataclass
class ClipRange:
    clip_id: int
    start: float
    end: float
    kind: str  # report | brief
    lead_end: float | None


def clip_kind(clip: Clip) -> str:
    """report: a report script with a reporter package; brief: anything else (straight)."""
    item = clip.rundown_item
    script = ParsedScript.model_validate(item.parsed_script) if item.parsed_script else None
    return "report" if item.type == ItemType.REPORT and script is not None and script.report else "brief"


def clip_ranges(session: Session, job_id: int, clip_ids: list[int] | None = None) -> list[ClipRange]:
    q = select(Clip).where(Clip.job_id == job_id, Clip.start_sec.is_not(None), Clip.end_sec.is_not(None))
    if clip_ids is not None:
        q = q.where(Clip.id.in_(clip_ids))
    out = []
    for clip in session.scalars(q):
        lead_end = session.scalar(
            select(func.max(AlignedSentence.end_sec)).where(
                AlignedSentence.rundown_item_id == clip.rundown_item_id, AlignedSentence.section == "lead"
            )
        )
        out.append(ClipRange(clip.id, clip.start_sec, clip.end_sec, clip_kind(clip), lead_end))
    return out


def make_candidates(
    job_id: int,
    r: ClipRange,
    src: Path,
    probe: ProbeResult,
    cuts: list[float],
    *,
    count: int = 6,
    samples: int = 12,
    on_progress=None,
) -> list[dict]:
    """Extract, score and keep the best `count` frames as thumb_candidates/{rank}.jpg."""
    files = JobFiles(job_id)
    out_dir = files.thumb_candidates_dir(r.clip_id)
    work = out_dir.parent / "thumb_work"
    shutil.rmtree(work, ignore_errors=True)
    anchor_end = anchor_end_for(r.kind, r.start, r.end, cuts, r.lead_end)
    times = candidate_times(r.start, r.end, cuts, anchor_end, samples)
    picked = find_candidates(src, probe, times, work, count, kind=r.kind, on_progress=on_progress)
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    rows = []
    for rank, c in enumerate(picked, 1):
        path = out_dir / f"{rank}.jpg"
        c.path.replace(path)
        rows.append({"rank": rank, "frame_sec": c.time, "path": str(path), "score": c.score, "details": c.details})
    shutil.rmtree(work, ignore_errors=True)
    return rows


def store(session: Session, clip_id: int, rows: list[dict]) -> None:
    session.execute(delete(Thumbnail).where(Thumbnail.clip_id == clip_id))
    session.add_all(
        Thumbnail(clip_id=clip_id, source="candidate", selected=row["rank"] == 1, **row) for row in rows
    )
    session.flush()


def render_selected(session: Session, clip_id: int) -> Path | None:
    """thumbnail.jpg = selected frame + thumbnail text + program template. None if either is missing."""
    clip = session.get(Clip, clip_id)
    meta = session.get(ClipMetadata, clip_id)
    thumb = session.scalar(select(Thumbnail).where(Thumbnail.clip_id == clip_id, Thumbnail.selected))
    if clip is None or meta is None or thumb is None or not Path(thumb.path).exists():
        return None
    job = session.get(Job, clip.job_id)
    template = overlay.template_for(clip_kind(clip), job.program.thumbnail_template if job.program else None)
    out = overlay.render_to(Path(thumb.path), meta.thumbnail_text, JobFiles(clip.job_id).thumbnail(clip_id), template)
    thumb.overlay_text = meta.thumbnail_text
    session.flush()
    return out


def remove_stale_dirs(session: Session, job_id: int) -> None:
    """Clip folders of clips that no longer exist (align re-runs make new clips)."""
    files = JobFiles(job_id)
    if not files.clips_dir.exists():
        return
    current = {str(i) for i in session.scalars(select(Clip.id).where(Clip.job_id == job_id))}
    for d in files.clips_dir.iterdir():
        if d.is_dir() and d.name not in current:
            shutil.rmtree(d, ignore_errors=True)
