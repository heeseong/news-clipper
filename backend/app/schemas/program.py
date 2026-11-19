from __future__ import annotations

from datetime import time

from pydantic import BaseModel, ConfigDict, Field


class ProgramIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    broadcast_time: time | None = None
    audio_track: int | None = Field(None, ge=0, description="Audio stream to use (0-based); none = default")


class ProgramOut(ProgramIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
