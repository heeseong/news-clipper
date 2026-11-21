"""Retention cleanup (spec 6.2, plan M9.2).

What goes, and when (settings, days counted from the job's creation unless noted):
- the original recording after RETENTION_SOURCE_DAYS, unless a clip is still waiting for its
  render (the render reads the original)
- working files after RETENTION_WORK_DAYS: proxy, sprite, waveform, alignment audio, acoustic model
  cache. The small JSON results (scenes, silences, alignment, cue sheet) and thumbnails stay
- a clip's final render RETENTION_RENDER_DAYS after the last of its uploads was published (only
  when every upload of it is published: a failed or pending one may still need the file)
- tus uploads that never became a job, after RETENTION_UPLOAD_HOURS

`plan()` lists what a run would delete (the admin screen shows it); `run()` deletes it and records
it on the job (`Job.purged`) and the clip (`Clip.render_purged_at`, `render_path` cleared).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Clip, ClipStatus, Job, PublishStatus, PublishTask
from app.models.base import utcnow
from app.storage import JobFiles, uploads_dir

log = logging.getLogger(__name__)

WAITING_FOR_RENDER = (ClipStatus.APPROVED, ClipStatus.RENDERING)


@dataclass
class Item:
    kind: str  # source | work | render | upload
    paths: list[Path]
    bytes: int
    reason: str
    job_id: int | None = None
    clip_id: int | None = None
    upload_id: str | None = None


@dataclass
class Result:
    deleted: list[Item] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(i.bytes for i in self.deleted)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def size_of(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def work_paths(files: JobFiles) -> list[Path]:
    return [files.dir / "proxy", files.dir / "audio", files.logprobs]


def source_paths(job: Job) -> list[Path]:
    paths = [Path(job.source_path)] if job.source_path else []
    files = JobFiles(job.id)
    paths += [p for p in (files.dir / "source").glob("original.*")]
    return list(dict.fromkeys(paths))


def plan(session: Session, now: datetime | None = None) -> list[Item]:
    now = now or utcnow()
    s = get_settings()
    items: list[Item] = []
    for job in session.scalars(select(Job).order_by(Job.id)):
        created = _aware(job.created_at)
        age = (now - created).days
        files = JobFiles(job.id)
        if created <= now - timedelta(days=s.retention_source_days):
            waiting = session.scalar(
                select(Clip.id).where(Clip.job_id == job.id, Clip.status.in_(list(WAITING_FOR_RENDER)))
            )
            paths = _existing(source_paths(job))
            if paths and not waiting:
                items.append(Item("source", paths, sum(size_of(p) for p in paths),
                                  f"원본 {age}일 지남 (보존 {s.retention_source_days}일)", job_id=job.id))  # fmt: skip
        if created <= now - timedelta(days=s.retention_work_days):
            paths = _existing(work_paths(files))
            if paths:
                items.append(Item("work", paths, sum(size_of(p) for p in paths),
                                  f"작업 파일 {age}일 지남 (보존 {s.retention_work_days}일)", job_id=job.id))  # fmt: skip
    render_cut = now - timedelta(days=s.retention_render_days)
    for clip in session.scalars(select(Clip).where(Clip.render_path.is_not(None)).order_by(Clip.id)):
        tasks = list(session.scalars(
            select(PublishTask).where(PublishTask.clip_id == clip.id, PublishTask.status != PublishStatus.CANCELLED)))  # fmt: skip
        if not tasks or any(t.status != PublishStatus.PUBLISHED or t.published_at is None for t in tasks):
            continue
        last = max(_aware(t.published_at) for t in tasks)
        path = Path(clip.render_path)
        if last <= render_cut and path.exists():
            days = (now - last).days
            items.append(Item("render", [path], size_of(path), f"게시 {days}일 지남 (보존 {s.retention_render_days}일)",
                              job_id=clip.job_id, clip_id=clip.id))  # fmt: skip
    up = uploads_dir()
    upload_cut = now - timedelta(hours=s.retention_upload_hours)
    if up.exists():
        for meta in sorted(up.glob("*.json")):
            upload_id = meta.stem
            data = up / f"{upload_id}.bin"  # app/uploads.py
            modified = datetime.fromtimestamp(max(meta.stat().st_mtime, data.stat().st_mtime if data.exists() else 0), UTC)
            if modified <= upload_cut:
                paths = _existing([data, meta])
                items.append(Item("upload", paths, sum(size_of(p) for p in paths),
                                  f"작업이 되지 않은 업로드 ({s.retention_upload_hours}시간 지남)", upload_id=upload_id))  # fmt: skip
    return items


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def run(session: Session, now: datetime | None = None) -> Result:
    """Delete what plan() lists. Each item is committed by the caller's session together."""
    now = now or utcnow()
    result = Result()
    for item in plan(session, now):
        try:
            for p in item.paths:
                _remove(p)
        except OSError as e:
            result.errors.append(f"{item.kind} {item.job_id or item.upload_id}: {e}")
            continue
        if item.kind in ("source", "work") and item.job_id is not None:
            job = session.get(Job, item.job_id)
            job.purged = {**(job.purged or {}), item.kind: now.isoformat()}
        elif item.kind == "render" and item.clip_id is not None:
            clip = session.get(Clip, item.clip_id)
            clip.render_path, clip.render_purged_at = None, now
        result.deleted.append(item)
        log.info("purged", extra={"kind": item.kind, "job_id": item.job_id, "clip_id": item.clip_id,
                                  "upload_id": item.upload_id, "bytes": item.bytes})  # fmt: skip
    session.flush()
    return result


def usage() -> dict[str, int]:
    """Bytes on disk by kind (storage root)."""
    from app.storage import root

    out = {"source": 0, "work": 0, "render": 0, "uploads": 0, "other": 0}
    jobs = root() / "jobs"
    if jobs.exists():
        for job_dir in jobs.iterdir():
            if not job_dir.is_dir():
                continue
            for p in job_dir.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(job_dir).parts
                size = p.stat().st_size
                if rel[0] == "source" and p.name.startswith("original."):
                    out["source"] += size
                elif rel[0] in ("proxy", "audio") or p.name == "logprobs.npz":
                    out["work"] += size
                elif p.name == "final.mp4":
                    out["render"] += size
                else:
                    out["other"] += size
    out["uploads"] = size_of(uploads_dir())
    return out
