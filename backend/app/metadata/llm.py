"""LLM suggestions for clip metadata (spec 5.7, plan 4.2).

`LLMProvider.suggest()` turns the script of one clip into candidates: two alternative titles, a
summary of the report body, keywords, hashtags and a short thumbnail line. Only script text is
sent (spec 9). Implementations: `FakeLLMProvider` (deterministic, from the script; dev and tests)
and `ClaudeProvider` (app/metadata/claude.py).

A response that does not fit `Suggestions` is asked for once more with the validation error; if it
still does not fit, `LLMError` is raised and the caller keeps the script-based defaults (spec 10).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

from .script import ClipScript

TITLE_MAX = 100  # YouTube limit
THUMBNAIL_TEXT_MAX = 16  # characters per line, without spaces (two lines at most)
SUMMARY_MAX = 400


class LLMError(Exception):
    """No usable suggestions (API error after retries, refusal, invalid response twice)."""

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens  # spent before giving up (spec 9)
        self.output_tokens = output_tokens


def thumbnail_length(text: str) -> int:
    """Characters of the longest line, spaces not counted."""
    return max((len(re.sub(r"\s+", "", line)) for line in text.splitlines()), default=0)


class Suggestions(BaseModel):
    titles: list[str] = Field(description="Two alternative YouTube titles")
    summary: str = Field(description="2–3 sentence summary of the report body; empty for a brief")
    keywords: list[str]
    hashtags: list[str]
    thumbnail_text: str

    @field_validator("titles")
    @classmethod
    def _titles(cls, v: list[str]) -> list[str]:
        v = [t.strip() for t in v if t.strip()]
        if len(v) != 2:
            raise ValueError("titles must have exactly 2 non-empty items")
        for t in v:
            if len(t) > TITLE_MAX:
                raise ValueError(f"title longer than {TITLE_MAX} characters: {t}")
        return v

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        v = v.strip()
        if len(v) > SUMMARY_MAX:
            raise ValueError(f"summary longer than {SUMMARY_MAX} characters")
        return v

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        v = list(dict.fromkeys(k.strip() for k in v if k.strip()))
        if len(v) > 10:
            raise ValueError("at most 10 keywords")
        return v

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, v: list[str]) -> list[str]:
        v = list(dict.fromkeys("#" + re.sub(r"\s+", "", h).lstrip("#") for h in v if h.strip().lstrip("#")))
        if len(v) > 3:
            raise ValueError("at most 3 hashtags")
        return v

    @field_validator("thumbnail_text")
    @classmethod
    def _thumbnail_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("thumbnail_text is empty")
        v = "\n".join(line.strip() for line in v.splitlines() if line.strip())
        if len(v.splitlines()) > 2:
            raise ValueError("thumbnail_text has more than 2 lines")
        if thumbnail_length(v) > THUMBNAIL_TEXT_MAX:
            raise ValueError(f"a thumbnail_text line is longer than {THUMBNAIL_TEXT_MAX} characters (spaces not counted)")
        return v


# JSON schema for structured output. Limits that JSON schema cannot express for every model
# (lengths, counts) are checked by `Suggestions` and asked for again once.
SUGGESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "thumbnail_text": {"type": "string"},
    },
    "required": ["titles", "summary", "keywords", "hashtags", "thumbnail_text"],
    "additionalProperties": False,
}


@dataclass
class LLMResult:
    suggestions: Suggestions
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def suggest(self, script: ClipScript) -> LLMResult: ...


class FakeLLMProvider(LLMProvider):
    """Deterministic suggestions built from the script (never invents facts)."""

    name = "fake"
    model = "fake"

    def suggest(self, script: ClipScript) -> LLMResult:
        first = (script.lead or script.body or [script.title])[0]
        titles = [
            script.title if not script.series else f"[{script.series}] {script.title}",
            _shorten(first.rstrip("."), TITLE_MAX),
        ]
        if titles[0] == titles[1]:
            titles[1] = f"{script.title} ({script.program})" if script.program else f"{script.title} 소식"
        summary = " ".join(_plain(s) for s in script.body[:2]) if script.kind == "report" else ""
        words = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", script.title)]
        keywords = list(dict.fromkeys(words))[:10]
        return LLMResult(
            Suggestions(
                titles=titles,
                summary=_shorten(summary, 400),
                keywords=keywords,
                hashtags=["#" + w for w in keywords[:3]],
                thumbnail_text=cut_words(script.title, THUMBNAIL_TEXT_MAX) or script.title[:THUMBNAIL_TEXT_MAX],
            ),
            provider=self.name,
            model=self.model,
        )


def _plain(sentence: str) -> str:
    return sentence.split("] ", 1)[-1] if sentence.startswith("[") else sentence


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def cut_words(text: str, limit: int) -> str:
    """Whole words of `text` up to `limit` characters (spaces not counted)."""
    out = ""
    for word in text.split():
        candidate = f"{out} {word}".strip()
        if thumbnail_length(candidate) > limit:
            break
        out = candidate
    return out
