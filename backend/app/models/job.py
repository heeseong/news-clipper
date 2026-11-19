from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

from .base import TimestampMixin, str_enum

if TYPE_CHECKING:
    from .program import ProgramProfile


class JobStatus(StrEnum):
    """Spec 4.1."""

    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    WAITING_RUNDOWN = "WAITING_RUNDOWN"
    PROCESSING = "PROCESSING"  # proxy, analysis and newsroom steps in parallel
    ALIGNING = "ALIGNING"
    GENERATING = "GENERATING"
    REVIEW_READY = "REVIEW_READY"
    DONE = "DONE"
    FAILED = "FAILED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"  # failed with a transient error; automatic retry scheduled
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"  # no handler yet (later milestone); counts as done
    FAILED = "FAILED"


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int | None] = mapped_column(ForeignKey("program_profiles.id", ondelete="SET NULL"))
    broadcast_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.UPLOADING, index=True)
    current_step: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(String(1024))
    source_name: Mapped[str | None] = mapped_column(String(255))  # file name as uploaded
    source_size: Mapped[int | None] = mapped_column(BigInteger)
    source_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    duration: Mapped[float | None] = mapped_column(Float)
    fps: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Job-level options (e.g. {"dummy": {...}} for development jobs).
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Non-blocking problems found at upload (date mismatch, duplicate file, ...)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))

    program: Mapped[ProgramProfile | None] = relationship(lazy="joined")
    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by=lambda: (JobStep.stage, JobStep.id),
    )

    @property
    def is_dummy(self) -> bool:
        return "dummy" in (self.params or {})


class JobStep(Base):
    __tablename__ = "job_steps"
    __table_args__ = (UniqueConstraint("job_id", "step", name="uq_job_steps_job_id_step"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[int] = mapped_column(Integer)  # index in the pipeline; steps in a stage run in parallel
    step: Mapped[str] = mapped_column(String(32))
    queue: Mapped[str] = mapped_column(String(16))
    status: Mapped[StepStatus] = mapped_column(str_enum(StepStatus), default=StepStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float | None] = mapped_column(Float)  # 0..1 while running
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="steps")
