"""Turn a finished upload into a job (spec 5.1, plan 1.3).

ffprobe validation happens here, before the job exists, so the uploader gets an immediate error
for a file the pipeline cannot use. Problems that do not block processing become warnings.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import uploads
from app.media.ffmpeg import FfmpegError, ProbeResult, probe
from app.models import Job, JobStatus, ProgramProfile
from app.pipeline.orchestrator import create_steps
from app.storage import JobFiles

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# 20261104 in names such as CT_V0001_20261104_1930.mxf (not part of a longer digit run)
_FILENAME_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")


class IntakeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def date_in_filename(name: str) -> date | None:
    for m in _FILENAME_DATE_RE.finditer(name):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
    return None


def date_in_metadata(p: ProbeResult) -> date | None:
    if not p.creation_time:
        return None
    try:
        dt = datetime.fromisoformat(p.creation_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(KST).date()


def check_warnings(
    session: Session, name: str, p: ProbeResult, broadcast_date: date, fingerprint: str
) -> list[str]:
    warnings = []
    if (d := date_in_filename(name)) and d != broadcast_date:
        warnings.append(
            f"파일 이름의 날짜({d.isoformat()})가 방송일({broadcast_date.isoformat()})과 다릅니다"
        )
    elif (d := date_in_metadata(p)) and d != broadcast_date:
        warnings.append(
            f"영상 메타데이터의 날짜({d.isoformat()})가 방송일({broadcast_date.isoformat()})과 다릅니다"
        )
    dupes = session.scalars(select(Job.id).where(Job.source_hash == fingerprint).order_by(Job.id)).all()
    if dupes:
        ids = ", ".join(f"#{i}" for i in dupes)
        warnings.append(f"같은 파일이 이미 올라와 있습니다 (작업 {ids})")
    if p.interlaced:
        log.info("interlaced source", extra={"field_order": p.field_order})
    return warnings


def create_job_from_upload(
    session: Session,
    upload_id: str,
    program_id: int | None,
    broadcast_date: date,
    cue_sheet_id: int | None = None,
) -> Job:
    try:
        info = uploads.load(upload_id)
    except uploads.UploadError as e:
        raise IntakeError(e.status, str(e)) from e
    if not info.complete:
        raise IntakeError(409, f"업로드가 끝나지 않았습니다 ({info.offset}/{info.length} bytes)")
    if program_id is not None and session.get(ProgramProfile, program_id) is None:
        raise IntakeError(422, "프로그램을 찾을 수 없습니다")

    data = uploads.data_path(upload_id)
    try:
        p = probe(data)
    except FfmpegError as e:
        raise IntakeError(422, f"영상 파일을 확인할 수 없습니다: {e}") from e
    if not p.audio:
        raise IntakeError(422, "오디오 트랙이 없습니다. 정렬에는 방송 오디오가 필요합니다")

    fp = uploads.fingerprint(data)
    job = Job(
        status=JobStatus.UPLOADED,
        program_id=program_id,
        broadcast_date=broadcast_date,
        source_name=info.filename,
        source_size=info.length,
        source_hash=fp,
        duration=p.duration,
        fps=p.fps,
        width=p.width,
        height=p.height,
        warnings=check_warnings(session, info.filename, p, broadcast_date, fp),
        params={"cue_sheet_id": cue_sheet_id} if cue_sheet_id is not None else {},
    )
    create_steps(job)
    session.add(job)
    session.flush()  # assigns job.id

    files = JobFiles(job.id)
    dest = files.source(info.extension)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data.replace(dest)
    files.probe.write_text(p.model_dump_json(indent=1))
    job.source_path = str(dest)
    uploads.forget(upload_id)
    log.info(
        "job created from upload",
        extra={"job_id": job.id, "upload_id": upload_id, "duration": p.duration, "warnings": job.warnings},
    )
    return job
