from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

from .base import TimestampMixin


class ClipMetadata(TimestampMixin, Base):
    """YouTube title, description, tags and thumbnail text of a clip (spec 5.7, 7).

    The defaults come from the script; the LLM only adds candidates (alternative titles, a summary,
    keywords, hashtags, a thumbnail line). Deleted with the clip (align re-runs recreate clips).
    """

    __tablename__ = "clip_metadata"

    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    # [{"text": ..., "source": "script" | "llm", "warnings": [...]}], the script title first
    title_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str | None] = mapped_column(Text)  # LLM summary of the report body (a candidate)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    thumbnail_text: Mapped[str] = mapped_column(String(255), default="")  # may have a line break
    thumbnail_text_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    privacy: Mapped[str] = mapped_column(String(16), default="private")  # private | unlisted | public
    # Scheduled publication (the video stays private until then, then gets `privacy`).
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_ids: Mapped[list[int]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    # LLM run (spec 9: token usage per job). llm_status: ok | failed | off
    llm_status: Mapped[str] = mapped_column(String(16), default="off")
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Fact check and LLM problems, shown to the reviewer (spec 5.7)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Thumbnail(TimestampMixin, Base):
    """A thumbnail candidate frame of a clip (spec 5.7, 7). `selected` marks the one to upload."""

    __tablename__ = "thumbnails"

    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate | frame | upload
    rank: Mapped[int] = mapped_column(Integer, default=0)  # 1 = best candidate
    frame_sec: Mapped[float | None] = mapped_column(Float)  # time in the recording (file start)
    path: Mapped[str] = mapped_column(String(1024))  # 1280x720 JPG without text
    score: Mapped[float | None] = mapped_column(Float)
    # Parts of the score, for tuning: {"sharpness": .., "exposure": .., "face": .., "anchor": ..}
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    overlay_text: Mapped[str | None] = mapped_column(String(255))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
