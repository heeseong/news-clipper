"""Cue sheet preview for the upload form (spec 8: GET /api/newsroom/rundowns)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession
from app.models import ProgramProfile
from app.newsroom import service
from app.newsroom.factory import get_provider
from app.newsroom.matching import match_rundown
from app.newsroom.provider import NewsroomError, RundownSummary
from app.schemas.newsroom import RundownCounts, RundownPreviewOut, RundownSummaryOut

router = APIRouter(prefix="/api/newsroom", tags=["newsroom"])


def summary_out(s: RundownSummary) -> RundownSummaryOut:
    return RundownSummaryOut.model_validate(s.model_dump(exclude={"raw"}))


@router.get("/rundowns", response_model=RundownPreviewOut)
def preview_rundown(
    db: DbSession,
    broadcast_date: Annotated[date, Query(alias="date")],
    program_id: int | None = None,
    cue_sheet_id: int | None = None,
) -> RundownPreviewOut:
    program = None
    if program_id is not None:
        program = db.get(ProgramProfile, program_id)
        if program is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "프로그램을 찾을 수 없습니다")
    info = service.ProgramInfo.of(program)
    provider = get_provider()
    try:
        day = provider.list_rundowns(broadcast_date, broadcast_date)
        match = match_rundown(day, info.name, info.broadcast_time, cue_sheet_id)
        counts, warnings = None, list(match.warnings)
        if match.chosen is not None:
            items = service.parse_items(provider.get_items(match.chosen.cue_sheet_id), info.excluded_sources)
            counts = RundownCounts(**service.summarize(items))
            warnings += service.item_warnings(items)
    except NewsroomError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"큐시트를 조회하지 못했습니다: {e}") from e
    return RundownPreviewOut(
        broadcast_date=broadcast_date,
        program_name=info.name,
        matched=summary_out(match.chosen) if match.chosen else None,
        reason=match.reason,
        ambiguous=match.ambiguous,
        candidates=[summary_out(s) for s in match.candidates],
        day_rundowns=[summary_out(s) for s in day if s.is_tv],
        counts=counts,
        warnings=warnings,
    )
