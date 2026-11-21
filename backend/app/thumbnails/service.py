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
from .candidates import choose

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
    picked = choose(src, probe, r.kind, r.start, r.end, cuts, r.lead_end, work, count, samples, on_progress)
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
    """Replace the candidates. Frames the reviewer added (current frame, upload) stay; the best
    candidate is selected unless one of those is."""
    session.execute(delete(Thumbnail).where(Thumbnail.clip_id == clip_id, Thumbnail.source == "candidate"))
    manual_selected = session.scalar(
        select(Thumbnail.id).where(Thumbnail.clip_id == clip_id, Thumbnail.selected)
    )
    session.add_all(
        Thumbnail(clip_id=clip_id, source="candidate", selected=row["rank"] == 1 and manual_selected is None, **row)
        for row in rows
    )
    session.flush()


def manual_path(job_id: int, clip_id: int, name: str) -> Path:
    """Frames the reviewer picked or uploaded (kept when candidates are made again)."""
    return JobFiles(job_id).clip_dir(clip_id) / "thumb_manual" / name


def save_uploaded(data: bytes, out: Path) -> Path:
    """An uploaded image as a 1280x720 JPG: cropped to 16:9 around the centre, then scaled."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
    img = img.convert("RGB")
    w, h = img.size
    target = overlay.WIDTH / overlay.HEIGHT
    if w / h > target:
        nw = round(h * target)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / target)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    img = img.resize((overlay.WIDTH, overlay.HEIGHT), Image.LANCZOS)
    return overlay.save_jpeg(img, out)


def template_of(session: Session, clip: Clip) -> dict:
    job = session.get(Job, clip.job_id)
    return overlay.template_for(clip_kind(clip), job.program.thumbnail_template if job.program else None)


def preview(session: Session, clip: Clip, text: str | None, thumbnail_id: int | None = None):
    """The thumbnail as it would look with `text` (the saved text when None), not saved."""
    q = select(Thumbnail).where(Thumbnail.clip_id == clip.id)
    q = q.where(Thumbnail.id == thumbnail_id) if thumbnail_id else q.where(Thumbnail.selected)
    thumb = session.scalar(q)
    if thumb is None or not Path(thumb.path).exists():
        return None
    if text is None:
        meta = session.get(ClipMetadata, clip.id)
        text = meta.thumbnail_text if meta else ""
    return overlay.render(Path(thumb.path), text, template_of(session, clip))


def render_selected(session: Session, clip_id: int) -> Path | None:
    """thumbnail.jpg = selected frame + thumbnail text + program template. None if either is missing."""
    clip = session.get(Clip, clip_id)
    meta = session.get(ClipMetadata, clip_id)
    thumb = session.scalar(select(Thumbnail).where(Thumbnail.clip_id == clip_id, Thumbnail.selected))
    if clip is None or meta is None or thumb is None or not Path(thumb.path).exists():
        return None
    out = overlay.render_to(
        Path(thumb.path), meta.thumbnail_text, JobFiles(clip.job_id).thumbnail(clip_id), template_of(session, clip)
    )
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
