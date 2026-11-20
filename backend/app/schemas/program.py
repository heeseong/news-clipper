from __future__ import annotations

from datetime import time

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


class ProgramOut(ProgramIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dedupe_program: str | None
    excluded_sources: list[str]
    pronunciations: dict[str, str]
