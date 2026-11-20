"""Pick the newsroom provider from settings."""

from __future__ import annotations

from app.core.config import Settings, get_settings

from .fake import FIXTURES, FakeNewsroomProvider
from .marsl import MarsLProvider
from .provider import NewsroomProvider

_override: NewsroomProvider | None = None


def set_provider(provider: NewsroomProvider | None) -> None:
    """Tests: use this provider instead of the configured one (None restores the default)."""
    global _override
    _override = provider


def get_provider(settings: Settings | None = None) -> NewsroomProvider:
    if _override is not None:
        return _override
    s = settings or get_settings()
    if s.newsroom_provider == "marsl":
        key = s.marsl_api_key.get_secret_value() if s.marsl_api_key else ""
        return MarsLProvider(
            s.marsl_base_url, key, timeout=s.marsl_timeout_seconds, max_retries=s.marsl_max_retries
        )
    return FakeNewsroomProvider.from_dir(s.fake_newsroom_dir or FIXTURES, any_date=s.fake_newsroom_any_date)
