from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import JobStatus, StepStatus


class JobStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: str
    stage: int
    queue: str
    status: StepStatus
    attempts: int
    progress: float | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    program_id: int | None
    program_name: str | None = None
    broadcast_date: date | None
    status: JobStatus
    current_step: str | None
    error: str | None
    duration: float | None
    fps: float | None
    width: int | None
    height: int | None
    source_name: str | None
    source_size: int | None
    warnings: list[str]
    is_dummy: bool
    cue_sheet_id: int | None = None  # picked by the user (upload form or refresh)
    created_at: datetime
    updated_at: datetime


class ArtifactsOut(BaseModel):
    """Which outputs exist (the browser fetches them from /media/jobs/{id}/...)."""

    proxy: bool = False
    sprite: bool = False
    waveform: bool = False
    scenes: bool = False
    silences: bool = False


class JobDetailOut(JobOut):
    steps: list[JobStepOut]
    artifacts: ArtifactsOut = ArtifactsOut()


class JobCreateIn(BaseModel):
    upload_id: str
    program_id: int | None = None
    broadcast_date: date
    # The cue sheet picked in the upload form when several match (spec 5.4); None = match by name.
    cue_sheet_id: int | None = None


class SceneCut(BaseModel):
    time: float
    frame: int
    score: float


class SilenceRange(BaseModel):
    start: float
    end: float


class AnalysisOut(BaseModel):
    scenes: list[SceneCut] | None = None  # None until the analyze step has produced them
    silences: list[SilenceRange] | None = None
    scene_threshold: float | None = None


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int


class StageOut(BaseModel):
    name: str
    status: JobStatus
    steps: list[str]


class DummyJobIn(BaseModel):
    seconds: float | None = Field(None, ge=0, le=60, description="Sleep per step (default from settings)")
    fail_steps: list[str] = Field(default_factory=list, description="Fail on the first attempt")
    flaky_steps: list[str] = Field(default_factory=list, description="Transient error on the first attempt")
