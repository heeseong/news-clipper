from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

from .base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from .job import Job


class Rundown(TimestampMixin, Base):
    """The cue sheet matched to a job (spec 7). One per job; re-fetching updates it in place."""

    __tablename__ = "rundowns"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    newsroom_rundown_id: Mapped[int] = mapped_column(Integer)  # MARS-L cueSheetId
    program_name: Mapped[str] = mapped_column(String(100))
    broadcast_date: Mapped[date | None] = mapped_column(Date)
    broadcast_start_time: Mapped[time | None] = mapped_column(Time)
    edition: Mapped[int] = mapped_column(Integer, default=0)  # cueSheetVersion (판 수)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)  # updateDatetime (KST, naive)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    match_reason: Mapped[str | None] = mapped_column(String(255))
    # IF_MARSL_002 `data` and IF_MARSL_003 `data`, unchanged ({"rundown": ..., "items": [...]}).
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Edition changes seen on re-fetch: [{"from": 2, "to": 3, "at": ..., "added": [...], ...}]
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    job: Mapped[Job] = relationship()
    items: Mapped[list[RundownItem]] = relationship(
        back_populates="rundown",
        cascade="all, delete-orphan",
        order_by=lambda: RundownItem.item_no,
    )


class RundownItem(Base):
    """One cue sheet item with its parsed script (spec 7)."""

    __tablename__ = "rundown_items"
    __table_args__ = (UniqueConstraint("rundown_id", "item_no", name="uq_rundown_items_rundown_id_item_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rundown_id: Mapped[int] = mapped_column(ForeignKey("rundowns.id", ondelete="CASCADE"), index=True)
    item_no: Mapped[int] = mapped_column(Integer)  # cueSheetItemOrderNumber
    newsroom_item_id: Mapped[int | None] = mapped_column(Integer)  # cueSheetItemId
    article_id: Mapped[int | None] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(8))  # R / S / OTHER
    type_reason: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(32))  # other station, None = own
    source_reason: Mapped[str | None] = mapped_column(String(255))
    upload: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_note: Mapped[str | None] = mapped_column(String(255))  # already aired / re-edited
    title: Mapped[str] = mapped_column(Text)  # cleaned
    title_raw: Mapped[str | None] = mapped_column(Text)
    series: Mapped[str | None] = mapped_column(String(32))
    reporter: Mapped[str | None] = mapped_column(String(50))  # sign-off reporter
    cue_sheet_reporter: Mapped[str | None] = mapped_column(String(50))  # reporterName when different
    planned_duration: Mapped[int | None] = mapped_column(Integer)  # seconds (cue sheet 시간)
    estimated_duration: Mapped[float | None] = mapped_column(Float)  # reading-time estimate
    remark: Mapped[str | None] = mapped_column(String(255))
    parsed_script: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # ParsedScript
    parse_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Alignment (align step): ALIGNED / NOT_FOUND / NO_SCRIPT, None until aligned.
    align_status: Mapped[str | None] = mapped_column(String(16))
    aligned_start: Mapped[float | None] = mapped_column(Float)  # first sentence onset
    aligned_end: Mapped[float | None] = mapped_column(Float)  # last sentence offset
    align_score: Mapped[float | None] = mapped_column(Float)  # median sentence score
    align_flags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))

    rundown: Mapped[Rundown] = relationship(back_populates="items")
