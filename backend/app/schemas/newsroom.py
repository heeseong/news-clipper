from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RundownSummaryOut(BaseModel):
    cue_sheet_id: int
    program_name: str
    program_type: str | None
    broadcast_date: date | None
    broadcast_start_time: time | None
    version: int
    anchors: list[str]
    updated_at: datetime | None


class RundownCounts(BaseModel):
    items: int = 0
    reports: int = 0  # 형식 R
    uploads: int = 0  # own-station reports (upload targets)
    excluded: int = 0  # reports from other stations
    planned_total_seconds: int | None = None


class RundownPreviewOut(BaseModel):
    """Upload form: which cue sheet the recording will be matched to (spec 8)."""

    broadcast_date: date
    program_name: str | None
    matched: RundownSummaryOut | None
    reason: str
    ambiguous: bool  # several cue sheets match: the user must pick one
    candidates: list[RundownSummaryOut]  # cue sheets with the program's name
    day_rundowns: list[RundownSummaryOut]  # every TV cue sheet of the day (to pick by hand)
    counts: RundownCounts | None = None
    warnings: list[str] = Field(default_factory=list)


class RundownItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_no: int
    type: str
    type_reason: str | None
    source: str | None
    source_reason: str | None
    upload: bool
    title: str
    title_raw: str | None
    series: str | None
    reporter: str | None
    cue_sheet_reporter: str | None
    planned_duration: int | None
    estimated_duration: float | None
    remark: str | None
    parsed_script: dict[str, Any] | None
    parse_warnings: list[str]
    align_status: str | None = None  # ALIGNED / NOT_FOUND / NO_SCRIPT
    aligned_start: float | None = None
    aligned_end: float | None = None
    align_score: float | None = None
    align_flags: list[str] = []


class RundownOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    newsroom_rundown_id: int
    program_name: str
    broadcast_date: date | None
    broadcast_start_time: time | None
    edition: int
    confirmed_at: datetime | None
    fetched_at: datetime
    match_reason: str | None
    changes: list[dict[str, Any]]
    warnings: list[str]
    items: list[RundownItemOut]
    counts: RundownCounts = RundownCounts()


class RundownRefreshIn(BaseModel):
    cue_sheet_id: int | None = Field(None, description="Use this cue sheet instead of matching by name")


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    rundown_item_id: int
    item_no: int = 0
    title: str = ""
    reporter: str | None = None
    planned_duration: int | None = None
    status: str
    start_sec: float | None
    end_sec: float | None
    start_snap: str | None
    end_snap: str | None
    suggested_start: float | None
    suggested_end: float | None
    confidence: float | None
    flags: list[str]
    needs_manual_range: bool


class AlignedSentenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    section: str
    kind: str
    text: str
    start_sec: float
    end_sec: float
    score: float
