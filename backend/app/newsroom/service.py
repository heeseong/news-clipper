"""Fetch a job's cue sheet, parse it and store it (pipeline step 4, spec 5.4).

Used by the `newsroom` pipeline step, the upload-form preview and the manual refresh.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, ProgramProfile, Rundown, RundownItem
from app.models.base import utcnow
from app.storage import JobFiles

from . import rundown as rd
from .matching import MatchResult, match_rundown, version_warnings
from .models import ItemType
from .models import RundownItem as ParsedItem
from .provider import NewsroomNotFound, NewsroomProvider, RundownSummary

log = logging.getLogger(__name__)


class RundownNotFound(Exception):
    """No cue sheet could be matched yet (job waits: WAITING_RUNDOWN)."""


@dataclass
class ProgramInfo:
    name: str | None
    broadcast_time: time | None
    excluded_sources: list[str] | None  # None = default (광주, 여수)

    @classmethod
    def of(cls, program: ProgramProfile | None) -> ProgramInfo:
        if program is None:
            return cls(None, None, None)
        return cls(program.name, program.broadcast_time, list(program.excluded_sources or []))


@dataclass
class FetchedRundown:
    summary: RundownSummary
    raw_items: list[dict[str, Any]]
    items: list[ParsedItem]
    match: MatchResult
    warnings: list[str] = field(default_factory=list)


def find_rundown(
    provider: NewsroomProvider,
    broadcast_date: date,
    program: ProgramInfo,
    cue_sheet_id: int | None = None,
) -> MatchResult:
    """Pick the cue sheet. A cue sheet chosen by the user is fetched directly by id."""
    if cue_sheet_id is not None:
        try:
            chosen = provider.get_rundown(cue_sheet_id)
        except NewsroomNotFound:
            return MatchResult(None, [], f"선택한 큐시트(#{cue_sheet_id})를 찾을 수 없습니다")
        return MatchResult(chosen, [chosen], "사용자가 선택", version_warnings(chosen))
    day = provider.list_rundowns(broadcast_date, broadcast_date)
    return match_rundown(day, program.name, program.broadcast_time)


def parse_items(raw_items: list[dict[str, Any]], excluded_sources: list[str] | None) -> list[ParsedItem]:
    return [rd.build_item(raw, excluded_sources=excluded_sources) for raw in raw_items]


def item_warnings(items: list[ParsedItem]) -> list[str]:
    warnings = []
    orders = [i.order for i in items]
    if len(set(orders)) != len(orders):
        warnings.append("큐시트 아이템 순서 번호가 겹칩니다")
    if not items:
        warnings.append("큐시트에 아이템이 없습니다")
    elif not any(i.upload for i in items):
        warnings.append("업로드 대상 리포트가 없습니다")
    return warnings


def fetch(
    provider: NewsroomProvider,
    broadcast_date: date,
    program: ProgramInfo,
    cue_sheet_id: int | None = None,
) -> FetchedRundown:
    match = find_rundown(provider, broadcast_date, program, cue_sheet_id)
    if match.chosen is None:
        raise RundownNotFound(match.reason)
    # The list entry can be stale; IF_MARSL_002 has the current edition.
    summary = provider.get_rundown(match.chosen.cue_sheet_id) if cue_sheet_id is None else match.chosen
    raw_items = provider.get_items(summary.cue_sheet_id)
    items = parse_items(raw_items, program.excluded_sources)
    warnings = [*version_warnings(summary), *item_warnings(items)]
    if summary.broadcast_date and summary.broadcast_date != broadcast_date:
        warnings.append(
            f"큐시트 방송일({summary.broadcast_date.isoformat()})이 작업의 방송일({broadcast_date.isoformat()})과 다릅니다"
        )
    return FetchedRundown(summary, raw_items, items, match, warnings)


# --- storing -------------------------------------------------------------------------------------


def _key(item_id: int | None, article_id: int | None, title: str) -> str:
    if item_id is not None:
        return f"item:{item_id}"
    if article_id is not None:
        return f"article:{article_id}"
    return f"title:{title}"


def _script_text(script: dict[str, Any] | None) -> str:
    return " ".join(s["text"] for s in (script or {}).get("sentences", []))


def diff_items(old: list[RundownItem], new: list[ParsedItem]) -> dict[str, list[dict[str, Any]]]:
    """What changed between two editions: added / removed / moved / edited items."""
    old_by = {_key(i.newsroom_item_id, i.article_id, i.title): i for i in old}
    new_by = {_key(i.item_id, i.article_id, i.title.clean): i for i in new}
    added = [{"item_no": i.order, "title": i.title.clean} for k, i in new_by.items() if k not in old_by]
    removed = [{"item_no": i.item_no, "title": i.title} for k, i in old_by.items() if k not in new_by]
    moved, edited = [], []
    for k, n in new_by.items():
        o = old_by.get(k)
        if o is None:
            continue
        if o.item_no != n.order:
            moved.append({"title": n.title.clean, "from": o.item_no, "to": n.order})
        n_script = n.script.model_dump(mode="json") if n.script else None
        if o.title != n.title.clean or _script_text(o.parsed_script) != _script_text(n_script):
            edited.append({"item_no": n.order, "title": n.title.clean})
    return {"added": added, "removed": removed, "moved": moved, "edited": edited}


def _db_item(item: ParsedItem) -> RundownItem:
    return RundownItem(
        item_no=item.order,
        newsroom_item_id=item.item_id,
        article_id=item.article_id,
        type=str(item.item_type),
        type_reason=item.type_reason[:255],
        source=item.source,
        source_reason=(item.source_reason or None),
        upload=item.upload,
        title=item.title.clean,
        title_raw=item.title.raw,
        series=item.title.series,
        reporter=item.reporter,
        planned_duration=item.planned_seconds,
        estimated_duration=item.estimated_seconds,
        remark=item.remark,
        parsed_script=item.script.model_dump(mode="json") if item.script else None,
        parse_warnings=list(item.script.warnings) if item.script else [],
    )


def write_files(job_id: int, fetched: FetchedRundown) -> None:
    files = JobFiles(job_id)
    files.rundown.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": utcnow().isoformat(),
        "rundown": fetched.summary.raw,
        "items": fetched.raw_items,
    }
    tmp = files.rundown.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(files.rundown)
    shutil.rmtree(files.scripts_dir, ignore_errors=True)
    files.scripts_dir.mkdir(parents=True)
    for item in fetched.items:
        files.script(item.order).write_text(item.model_dump_json(indent=1), encoding="utf-8")


def store(session: Session, job: Job, fetched: FetchedRundown) -> Rundown:
    """Save the fetched cue sheet for the job, replacing the items of an earlier fetch.

    When the cue sheet or its edition changed since the last fetch, the difference is recorded.
    """
    s = fetched.summary
    rundown = session.scalar(select(Rundown).where(Rundown.job_id == job.id))
    if rundown is None:
        rundown = Rundown(job_id=job.id, changes=[])
        session.add(rundown)
    elif rundown.newsroom_rundown_id != s.cue_sheet_id or rundown.edition != s.version:
        change = {
            "at": utcnow().isoformat(),
            "from": rundown.edition,
            "to": s.version,
            **diff_items(list(rundown.items), fetched.items),
        }
        if rundown.newsroom_rundown_id != s.cue_sheet_id:
            change["cue_sheet"] = {"from": rundown.newsroom_rundown_id, "to": s.cue_sheet_id}
        rundown.changes = [*(rundown.changes or []), change]
    rundown.newsroom_rundown_id = s.cue_sheet_id
    rundown.program_name = s.program_name
    rundown.broadcast_date = s.broadcast_date
    rundown.broadcast_start_time = s.broadcast_start_time
    rundown.edition = s.version
    rundown.confirmed_at = s.updated_at
    rundown.fetched_at = utcnow()
    rundown.match_reason = fetched.match.reason[:255]
    rundown.raw = {"rundown": s.raw, "items": fetched.raw_items}
    rundown.warnings = list(fetched.warnings)
    rundown.items.clear()
    session.flush()  # delete old items before inserting rows with the same item_no
    rundown.items.extend(_db_item(i) for i in fetched.items)
    session.flush()
    log.info(
        "rundown stored",
        extra={
            "job_id": job.id,
            "cue_sheet_id": s.cue_sheet_id,
            "edition": s.version,
            "items": len(fetched.items),
            "uploads": sum(i.upload for i in fetched.items),
        },
    )
    return rundown


def summarize(items: list[ParsedItem]) -> dict[str, Any]:
    planned = [i.planned_seconds for i in items if i.planned_seconds]
    reports = [i for i in items if i.item_type == ItemType.REPORT]
    return {
        "items": len(items),
        "reports": len(reports),
        "uploads": sum(i.upload for i in items),
        "excluded": sum(1 for i in reports if not i.upload),
        "planned_total_seconds": sum(planned) if planned else None,
    }
