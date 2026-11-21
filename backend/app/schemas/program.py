from __future__ import annotations

from datetime import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProgramIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    broadcast_time: time | None = None
    audio_track: int | None = Field(None, ge=0, description="Audio stream to use (0-based); none = default")
    pronunciations: dict[str, str] | None = Field(
        None, description="Alignment pronunciation dictionary, e.g. {\"KIA\": \"기아\"}; none = unchanged"
    )
    dedupe_program: str | None = Field(
        None, description="Leave out items this program aired the previous day (e.g. 뉴스데스크); '' clears"
    )
    excluded_sources: list[str] | None = Field(
        None, description="Stations whose reports are not uploaded; none = default (광주, 여수) / unchanged"
    )
    title_template: str | None = Field(
        None,
        description="YouTube title; placeholders {title} {station} {program} {date} {reporter}. "
        "'' = default \"{title}[{station} {program}]\"; none = unchanged",
    )
    description_footer: str | None = Field(
        None,
        description="YouTube description footer; placeholders {station} {program} {date} {reporter} "
        "{reporter_credit} {title}. '' = built-in default; none = unchanged",
    )
    default_tags: list[str] | None = Field(None, description="Tags on every clip; none = unchanged")
    default_channels: list[int] | None = Field(
        None, description="Channels new clips upload to (ids from /api/channels); none = unchanged"
    )
    thumbnail_template: dict[str, Any] | None = Field(
        None, description="Thumbnail overlay design (app/thumbnails/overlay.py); none = unchanged"
    )


class ProgramOut(ProgramIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dedupe_program: str | None
    excluded_sources: list[str]
    pronunciations: dict[str, str]
    title_template: str | None
    description_footer: str | None
    default_tags: list[str]
    default_channels: list[int]
    thumbnail_template: dict[str, Any]


class ProgramPatchIn(BaseModel):
    """Partial update (the admin screen, M9.1): only the fields sent change."""

    name: str | None = Field(None, min_length=1, max_length=100)
    broadcast_time: time | None = None
    audio_track: int | None = Field(None, ge=0)
    pronunciations: dict[str, str] | None = None
    dedupe_program: str | None = None
    excluded_sources: list[str] | None = None
    title_template: str | None = None
    description_footer: str | None = None
    default_tags: list[str] | None = None
    default_channels: list[int] | None = None
    thumbnail_template: dict[str, Any] | None = None
