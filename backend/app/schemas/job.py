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
    is_dummy: bool
    created_at: datetime
    updated_at: datetime


class JobDetailOut(JobOut):
    steps: list[JobStepOut]


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
