from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

from .base import TimestampMixin, str_enum

if TYPE_CHECKING:
    from .metadata import ClipMetadata, Thumbnail
    from .rundown import RundownItem


class ClipStatus(StrEnum):
    """Spec 4.2 (later states arrive with M6/M7)."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RENDERING = "RENDERING"
    RENDERED = "RENDERED"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    NOT_FOUND = "NOT_FOUND"  # in the cue sheet but not found in the recording


class Clip(TimestampMixin, Base):
    """One upload candidate per own-station report or straight brief (spec 5.6 6-a, 7).

    start_sec/end_sec are None when the alignment was rejected (LOW_CONFIDENCE,
    DURATION_MISMATCH, NOT_FOUND): the reviewer sets the range by hand (spec 5-3-1).
    """

    __tablename__ = "clips"
    __table_args__ = (UniqueConstraint("job_id", "rundown_item_id", name="uq_clips_job_id_rundown_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    rundown_item_id: Mapped[int] = mapped_column(ForeignKey("rundown_items.id", ondelete="CASCADE"))
    status: Mapped[ClipStatus] = mapped_column(str_enum(ClipStatus), default=ClipStatus.DRAFT)
    start_sec: Mapped[float | None] = mapped_column(Float)
    end_sec: Mapped[float | None] = mapped_column(Float)
    start_snap: Mapped[str | None] = mapped_column(String(16))  # scene | silence | align | manual
    end_snap: Mapped[str | None] = mapped_column(String(16))  # next_item_cut | align | manual
    # Suggested range from the alignment, kept even when it was rejected (for the reviewer).
    suggested_start: Mapped[float | None] = mapped_column(Float)
    suggested_end: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)  # median sentence score of the item
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    render_path: Mapped[str | None] = mapped_column(String(1024))
    locked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rundown_item: Mapped[RundownItem] = relationship()
    # Rows are removed by the database (ON DELETE CASCADE) when clips are deleted in bulk.
    metadata_: Mapped[ClipMetadata | None] = relationship(passive_deletes=True)
    thumbnails: Mapped[list[Thumbnail]] = relationship(
        order_by="Thumbnail.rank", viewonly=True
    )

    @property
    def needs_manual_range(self) -> bool:
        return self.start_sec is None or self.end_sec is None


class AlignedSentence(Base):
    """A script sentence with its time in the recording (spec 7)."""

    __tablename__ = "aligned_sentences"

    id: Mapped[int] = mapped_column(primary_key=True)
    rundown_item_id: Mapped[int] = mapped_column(ForeignKey("rundown_items.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # index in the parsed script's sentences
    section: Mapped[str] = mapped_column(String(16))  # lead | report
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    start_sec: Mapped[float] = mapped_column(Float)  # first character
    end_sec: Mapped[float] = mapped_column(Float)  # last character
    score: Mapped[float] = mapped_column(Float)
