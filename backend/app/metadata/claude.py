"""Claude implementation of LLMProvider (spec 5.7). Only the script text is sent.

- Structured output (`output_config.format`, JSON schema) and a Pydantic check (`Suggestions`).
  A response that fails the check is sent back once with the error (spec 10).
- 429 / 5xx / timeouts are retried by the SDK with exponential backoff (`llm_max_retries`).
- A refused request is re-run on another model by the API (server-side fallback, optional).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
from pydantic import ValidationError

from .llm import (
    SUGGESTIONS_SCHEMA,
    SUMMARY_MAX,
    THUMBNAIL_TEXT_MAX,
    TITLE_MAX,
    LLMError,
    LLMProvider,
    LLMResult,
    Suggestions,
)
from .script import ClipScript

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = f"""\
당신은 지역 방송사 보도국의 유튜브 담당 편집자입니다. 뉴스 원고를 받아 유튜브 업로드용 문구 후보를 만듭니다.
결과는 검수자가 원고와 대조해 고르므로, 원고에 있는 사실만 씁니다.

규칙
- 원고에 없는 사실, 숫자, 이름, 지명, 기관명을 만들지 않습니다. 숫자와 고유명사는 원고에 적힌 그대로 씁니다.
- 과장, 낚시성 표현, 물음표로 궁금증을 끄는 제목, 감탄사, 이모지를 쓰지 않습니다. 보도 기사의 어조를 지킵니다.
- titles: 원고 제목과 다른 대안 제목 2개. 각 {TITLE_MAX}자 이하, 40~60자를 권장합니다. 누가/어디서/무엇을이 드러나게 씁니다.
- summary: 리포트 본문을 2~3문장, {SUMMARY_MAX}자 이하로 요약합니다. 원고 종류가 단신이면 빈 문자열로 둡니다.
- keywords: 검색용 키워드 10개 이하. 원고에 나온 지명, 기관, 주제어 위주로 씁니다.
- hashtags: 해시태그 3개 이하. 띄어쓰기 없이 씁니다.
- thumbnail_text: 썸네일에 크게 올릴 두 줄 문구. 줄바꿈(\\n)으로 나누고, 각 줄은 띄어쓰기를 빼고 {THUMBNAIL_TEXT_MAX}자 이하입니다. 첫 줄은 핵심 소재(인용이면 작은따옴표), 둘째 줄은 상황이나 쟁점을 짧게 씁니다.
"""


def render_script(script: ClipScript) -> str:
    lines = [
        f"원고 종류: {'리포트' if script.kind == 'report' else '단신'}",
        f"프로그램: {script.program}" if script.program else "",
        f"방송일: {script.broadcast_date.isoformat()}" if script.broadcast_date else "",
        f"원고 제목: {script.title}",
        f"기자: {script.reporter}" if script.reporter else "",
        "",
        "[앵커 리드]" if script.kind == "report" else "[원고]",
        *script.lead,
    ]
    if script.body:
        lines += ["", "[리포트 본문]", *script.body]
    return "\n".join(line for line in lines if line is not None).strip()


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        effort: str = "medium",
        timeout: float = 60.0,
        max_retries: int = 3,
        refusal_fallback: bool = True,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    def _create(self, messages: list[dict[str, Any]]):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": SUGGESTIONS_SCHEMA}},
        }
        if self.refusal_fallback:
            kwargs |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        try:
            return self.client.beta.messages.create(**kwargs)
        except anthropic.APIConnectionError as e:  # includes timeouts, after the SDK's retries
            raise LLMError(f"Claude API에 연결하지 못했습니다: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API 사용량 한도에 걸렸습니다 (429)") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API 오류 ({e.status_code}): {e.message}") from e

    def suggest(self, script: ClipScript) -> LLMResult:
        messages: list[dict[str, Any]] = [{"role": "user", "content": render_script(script)}]
        input_tokens = output_tokens = 0
        error = ""
        for attempt in (1, 2):
            response = self._create(messages)
            input_tokens += response.usage.input_tokens or 0
            output_tokens += response.usage.output_tokens or 0
            if response.stop_reason == "refusal":
                raise LLMError("Claude가 요청을 거절했습니다", input_tokens, output_tokens)
            text = "".join(b.text for b in response.content if b.type == "text")
            try:
                suggestions = Suggestions.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as e:
                error = str(e)
                log.warning("invalid LLM response", extra={"attempt": attempt, "error": error[:500]})
                messages += [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": f"응답이 규칙에 맞지 않습니다. 오류를 고쳐 같은 형식으로 다시 답해 주세요.\n{error}",
                    },
                ]
                continue
            return LLMResult(
                suggestions,
                provider=self.name,
                model=getattr(response, "model", None) or self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                attempts=attempt,
            )
        raise LLMError(f"LLM 응답이 형식에 맞지 않습니다 (2회): {error[:200]}", input_tokens, output_tokens)
