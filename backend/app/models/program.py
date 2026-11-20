from __future__ import annotations

from datetime import time
from typing import Any

from sqlalchemy import JSON, String, Text, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

from .base import TimestampMixin

DEFAULT_EXCLUDED_SOURCES = ("광주", "여수")  # spec 3.2
# The morning news repeats the previous evening's items: leave those out (2026-11-20).
DEFAULT_DEDUPE_PROGRAM = {"뉴스투데이": "뉴스데스크"}


class ProgramProfile(TimestampMixin, Base):
    """Per-program settings (spec 7). Edited in the admin screen (M9)."""

    __tablename__ = "program_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)  # matches MARS-L programName (trimmed)
    newsroom_program_id: Mapped[str | None] = mapped_column(String(100))
    # Scheduled start, used to pick the right cue sheet when several match (spec 5.4).
    broadcast_time: Mapped[time | None] = mapped_column(Time)
    audio_track: Mapped[int | None] = mapped_column()  # audio stream index to align against
    signoff_pattern: Mapped[str | None] = mapped_column(String(255))
    title_cleanup_rules: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Stations whose reports are not uploaded (e.g. ["광주", "여수"]).
    # Words read differently from their spelling, for alignment (e.g. {"KIA": "기아"}); added to
    # the built-in dictionary in app/align/korean_text.py.
    pronunciations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    # Leave out items that aired in this program the previous day (e.g. 뉴스투데이 → "뉴스데스크").
    dedupe_program: Mapped[str | None] = mapped_column(String(100))
    excluded_sources: Mapped[list[str]] = mapped_column(JSON, default=lambda: list(DEFAULT_EXCLUDED_SOURCES))
    description_footer: Mapped[str | None] = mapped_column(Text)
    default_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    thumbnail_template: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_channels: Mapped[list[int]] = mapped_column(JSON, default=list)
