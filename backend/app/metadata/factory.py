"""Pick the LLM provider from settings (None = no LLM: script-based defaults only)."""

from __future__ import annotations

from app.core.config import Settings, get_settings

from .llm import FakeLLMProvider, LLMProvider

_override: object
_UNSET = object()
_override = _UNSET


def set_provider(provider: LLMProvider | None | object = _UNSET) -> None:
    """Tests: use this provider (None = no LLM) instead of the configured one; no argument restores it."""
    global _override
    _override = provider  # type: ignore[assignment]


def get_llm_provider(settings: Settings | None = None) -> LLMProvider | None:
    if _override is not _UNSET:
        return _override  # type: ignore[return-value]
    s = settings or get_settings()
    if s.llm_provider == "fake":
        return FakeLLMProvider()
    if s.llm_provider == "claude":
        if s.anthropic_api_key is None:
            return None  # not configured: carry on with the script-based defaults (plan M4 DoD)
        from .claude import ClaudeProvider

        return ClaudeProvider(
            s.anthropic_api_key.get_secret_value(),
            s.llm_model,
            effort=s.llm_effort,
            timeout=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
            refusal_fallback=s.llm_refusal_fallback,
        )
    return None
