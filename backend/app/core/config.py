"""Application settings, read from environment variables (and `.env` if present)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
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

    # Newsroom (MARS-L, spec 3.1). "fake" serves synthetic fixtures (app/newsroom/fixtures).
    newsroom_provider: Literal["fake", "marsl"] = "fake"
    marsl_base_url: str = "https://api.marsl.co.kr"
    marsl_api_key: SecretStr | None = None  # sent as the Mars-Secret header; never logged
    marsl_timeout_seconds: float = 10.0
    marsl_max_retries: int = 3
    # Fake only: a fixture directory other than the built-in one, and whether every fixture cue
    # sheet is served for any requested date (handy for synthetic test recordings).
    fake_newsroom_dir: Path | None = None
    fake_newsroom_any_date: bool = True

    # Alignment (spec 5.5). The model runs locally on CPU (needs the `model` extra).
    align_model: str = "kresnik/wav2vec2-large-xlsr-korean"
    align_threads: int = 4
    align_chunk_seconds: float = 20.0

    # Metadata (spec 5.7). The LLM only adds candidates; without it the script-based defaults are
    # used. "off" = no LLM, "fake" = deterministic suggestions from the script (dev, tests),
    # "claude" = Claude API (only the script text is sent).
    llm_provider: Literal["off", "fake", "claude"] = "fake"
    anthropic_api_key: SecretStr | None = None  # sent as x-api-key; never logged
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high"] = "medium"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3  # SDK retries for 429 / 5xx / timeouts (exponential backoff)
    # Re-run a refused request on another model (server-side fallback, Claude API only).
    llm_refusal_fallback: bool = True
    # Station name in the description footer ({station}).
    station_name: str = "목포MBC"

    # Thumbnails (spec 5.7)
    thumbnail_candidates: int = 6
    thumbnail_samples: int = 12  # evenly spaced frames per clip, besides frames after scene cuts

    # Final render (spec 5.6 6-b). Fades are off by default (the first frame stays the reviewed one).
    render_max_height: int = 1080
    render_crf: int = 18
    render_preset: str = "fast"
    render_loudness: float = -14.0  # LUFS, EBU R128 loudnorm 2-pass
    render_fade_in: float = 0.0
    render_fade_out: float = 0.0

    # Publishing (spec 5.9, plan M7). "fake" connects pretend channels (dev, E2E); "youtube" connects
    # real channels with Google OAuth. Each channel's platform picks its Publisher.
    publisher: Literal["fake", "youtube"] = "fake"
    # Upload as soon as an approved clip is rendered (when it has channels); otherwise "업로드" button.
    publish_auto: bool = True
    # Fernet key for channel tokens: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: SecretStr | None = None
    google_client_id: str | None = None
    # Sent as HTTP Basic auth to the token endpoint; "proxy-injected" when a proxy adds it.
    google_client_secret: SecretStr | None = None
    # Browser redirect flow (needs https or localhost, registered on the OAuth client), e.g.
    # https://clipper.example.com/api/channels/oauth/callback. Empty: device-code flow only.
    google_oauth_redirect_uri: str | None = None
    google_oauth_url: str = "https://oauth2.googleapis.com"
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    youtube_api_url: str = "https://www.googleapis.com"
    youtube_daily_quota: int = 10_000  # units per Google Cloud project per day (Pacific time)
    youtube_quota_insert: int = 1600
    youtube_quota_thumbnail: int = 50
    youtube_quota_delete: int = 50
    youtube_quota_update: int = 51  # videos.list (read before) + videos.update, edits after the upload (M8)
    youtube_chunk_mb: int = 16  # resumable upload chunk (a multiple of 8 MB)
    youtube_category_id: str = "25"  # News & Politics
    youtube_language: str = "ko"
    youtube_notify_subscribers: bool = True
    publish_max_attempts: int = 5  # network / 5xx errors (1, 2, 4, 8, 16 minutes apart)

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
