"""Application settings, read from environment variables (and `.env` if present)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"

    database_url: str = "postgresql+psycopg://newsclipper:newsclipper@localhost:5432/newsclipper"
    redis_url: str = "redis://localhost:6379/0"
    # Root of the job storage tree (spec 6.2): storage/jobs/{job_id}/...
    storage_root: Path = Path("./storage")

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Comma-separated origins allowed by CORS (the Vite dev server in development).
    cors_origins: str = "http://localhost:5173"

    # Uploads (spec 5.1)
    max_upload_bytes: int = 50 * 1024**3
    allowed_extensions: str = "mp4,mov,mxf,mkv,ts"

    # Media processing (spec 5.2, 5.3)
    proxy_height: int = 360  # review only; final renders and thumbnails use the original
    proxy_crf: int = 28
    proxy_preset: str = "veryfast"
    scene_threshold: float = 0.1  # PoC: 0.3 missed 2 of 8 anchor-shot cuts
    silence_noise_db: float = -35.0
    silence_min_seconds: float = 0.25
    ffmpeg_timeout_seconds: int = 3 * 3600

    # Run Celery tasks in-process (tests, or running the API without workers).
    celery_always_eager: bool = False

    # Development helpers: POST /api/jobs/dummy creates a job whose steps only sleep.
    enable_dev_endpoints: bool = True
    dummy_step_seconds: float = 2.0

    @property
    def allowed_extension_set(self) -> set[str]:
        return {e.strip().lower().lstrip(".") for e in self.allowed_extensions.split(",") if e.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
