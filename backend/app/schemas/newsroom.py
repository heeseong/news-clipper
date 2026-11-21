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
    briefs: int = 0  # 형식 S (스트레이트)
    uploads: int = 0  # own-station reports and briefs (upload targets)
    aired: int = 0  # left out: already aired (e.g. the previous day's 뉴스데스크)
    excluded: int = 0  # reports and briefs from other stations
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
    dedupe_note: str | None = None
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
    youtube_title: str | None = None  # metadata title, once generated
    thumbnail_url: str | None = None  # selected frame with the text overlay
    warnings_acknowledged: bool = False
    edited_at: datetime | None = None
    reviewed_at: datetime | None = None
    reject_reason: str | None = None
    kind: str = "report"  # report | brief (thumbnail style)
    rendered_at: datetime | None = None
    render_error: str | None = None
    render_purged_at: datetime | None = None  # the render file was deleted (retention, M9.2)
    render_url: str | None = None  # the final render (M6), when RENDERED
    remote_urls: list[str] = []  # uploaded videos (M7)
    locked_by_name: str | None = None  # another reviewer editing it right now


class AlignedSentenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    section: str
    kind: str
    text: str
    start_sec: float
    end_sec: float
    score: float


class TextCandidateOut(BaseModel):
    text: str
    source: str  # script | title | llm
    warnings: list[str] = []


class ClipMetadataOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    title_candidates: list[TextCandidateOut]
    description: str
    summary: str | None
    tags: list[str]
    hashtags: list[str]
    thumbnail_text: str
    thumbnail_text_candidates: list[TextCandidateOut]
    privacy: str
    publish_at: datetime | None
    channel_ids: list[int] = []
    llm_status: str  # ok | failed | off
    llm_provider: str | None
    llm_model: str | None
    llm_input_tokens: int
    llm_output_tokens: int
    warnings: list[str]
    generated_at: datetime | None


class ThumbnailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    rank: int
    frame_sec: float | None
    score: float | None
    details: dict[str, Any]
    overlay_text: str | None
    selected: bool
    url: str = ""  # the frame without text


class ClipDetailOut(ClipOut):
    metadata: ClipMetadataOut | None = None
    publish_tasks: list[Any] = []  # PublishTaskOut (app/schemas/publish.py), cancelled ones left out
    thumbnails: list[ThumbnailOut] = []
    approval_problems: list[str] = []  # empty = can be approved
    lock_expires_at: datetime | None = None


class ClipPatchIn(BaseModel):
    """Reviewer edits (spec 8 PATCH /api/clips/{id}). Fields left out are unchanged."""

    start_sec: float | None = None
    end_sec: float | None = None
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    hashtags: list[str] | None = None
    thumbnail_text: str | None = Field(None, max_length=255)
    privacy: str | None = None
    publish_at: datetime | None = None
    clear_publish_at: bool = False  # publish when processed instead of at publish_at
    channel_ids: list[int] | None = None
    warnings_acknowledged: bool | None = None


class FrameThumbnailIn(BaseModel):
    frame_sec: float = Field(ge=0)


class RejectIn(BaseModel):
    reason: str | None = None


class BulkIn(BaseModel):
    clip_ids: list[int] = Field(min_length=1)
    action: str = Field(pattern="^(approve|schedule|publish)$")
    # schedule: the first clip (in cue sheet order) at first_at, then every interval_minutes
    first_at: datetime | None = None
    interval_minutes: int = Field(10, ge=1, le=24 * 60)
    privacy: str | None = None  # visibility once published


class BulkResultOut(BaseModel):
    clip_id: int
    ok: bool
    error: str | None = None
    problems: list[str] = []
    publish_at: datetime | None = None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_name: str | None = None
    action: str
    diff: dict[str, Any]
    created_at: datetime
