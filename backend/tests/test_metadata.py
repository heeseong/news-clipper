"""Clip metadata (M4): script-based defaults, fact check, LLM candidates and the Claude client."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.metadata import defaults, factcheck
from app.metadata.claude import ClaudeProvider, render_script
from app.metadata.llm import FakeLLMProvider, LLMError, LLMProvider, LLMResult, Suggestions
from app.metadata.script import ClipScript
from app.metadata.service import generate

REPORT = ClipScript(
    kind="report",
    title="압해도 새 다리 개통",
    lead=["목포시와 신안군을 잇는 새 다리가 오늘 개통했습니다.", "공사비 1,200억 원이 들었습니다."],
    body=["길이 1킬로미터의 다리가 문을 열었습니다.", "[인터뷰 김주민] 이제 병원 가기가 편해졌습니다."],
    reporter="홍길동",
    program="뉴스데스크",
    broadcast_date=date(2026, 11, 4),
    station="목포MBC",
)
REPORT.all_text = "\n".join([REPORT.title, *REPORT.lead, *REPORT.body, "엠비씨 뉴스 홍길동입니다."])
BRIEF = ClipScript(
    kind="brief",
    title="겨울 축제 준비",
    lead=["진도군이 겨울 축제 준비에 들어갔습니다.", "올해는 체험 행사가 늘어납니다."],
    body=[],
    reporter=None,
    program="뉴스데스크",
    broadcast_date=date(2026, 11, 4),
    station="목포MBC",
    all_text="겨울 축제 준비\n진도군이 겨울 축제 준비에 들어갔습니다.\n올해는 체험 행사가 늘어납니다.",
)


def suggestions(**kw) -> Suggestions:
    base = dict(
        titles=["목포 압해도 새 다리 개통…병원 가기 편해져", "신안군 잇는 새 다리 오늘 개통"],
        summary="길이 1킬로미터의 다리가 문을 열었습니다. 주민들은 병원 가기가 편해졌다고 말했습니다.",
        keywords=["압해도", "다리", "신안군"],
        hashtags=["#압해도", "#목포시"],
        thumbnail_text="압해도 다리 개통",
    )
    return Suggestions(**(base | kw))


class StaticProvider(LLMProvider):
    name, model = "static", "static-1"

    def __init__(self, s: Suggestions | Exception) -> None:
        self.s = s

    def suggest(self, script: ClipScript) -> LLMResult:
        if isinstance(self.s, Exception):
            raise self.s
        return LLMResult(self.s, self.name, self.model, input_tokens=120, output_tokens=40)


# --- defaults (4.1) ---


def test_report_defaults_come_from_the_script():
    d = defaults.build(REPORT, None, ["뉴스", "목포MBC"])
    assert d.title == "압해도 새 다리 개통"
    lead, footer = d.description.split("\n\n")
    assert lead == "목포시와 신안군을 잇는 새 다리가 오늘 개통했습니다. 공사비 1,200억 원이 들었습니다."
    assert footer == "취재: 홍길동 기자\n목포MBC 뉴스데스크 2026년 11월 4일 방송"
    assert d.tags == ["뉴스", "목포MBC", "홍길동", "목포", "신안"]
    assert d.thumbnail_text == "압해도 새 다리 개통"


def test_brief_description_is_the_whole_text_and_footer_template_is_used():
    d = defaults.build(BRIEF, "{program} | {date}\n{reporter_credit}\n{unknown}", [])
    assert d.description == (
        "진도군이 겨울 축제 준비에 들어갔습니다. 올해는 체험 행사가 늘어납니다.\n\n뉴스데스크 | 2026년 11월 4일"
    )
    assert d.tags == ["진도"]


def test_thumbnail_text_fits_15_characters():
    assert defaults.thumbnail_text("짧은 제목") == "짧은 제목"
    long = "전남 서남권 섬 지역 응급 의료 공백 여전히 심각"
    text = defaults.thumbnail_text(long)
    assert len(text.replace(" ", "")) <= 15 and long.startswith(text)
    assert defaults.thumbnail_text("의료 공백, 섬 주민들 여전히 불편 호소하는 현실") == "의료 공백"


def test_tags_stay_under_youtube_limit():
    tags = defaults.limit_tags([f"태그{i:03d}" * 3 for i in range(200)] + ["", "<b>"])
    assert sum(len(t) for t in tags) + len(tags) - 1 <= 500
    assert "" not in tags and "<b>" not in tags


def test_region_names_only_at_word_start():
    assert defaults.regions("목포시와 무안군, 성장성이 큰 해남") == ["목포", "무안", "해남"]


# --- fact check (4.3) ---


def test_fact_check_finds_numbers_and_names_missing_from_the_script():
    ref = REPORT.all_text
    assert factcheck.missing_from_script("압해도 다리 1200억 원 투입, 목포시 개통", ref) == []
    assert factcheck.missing_from_script("무안공항까지 3분, 김영록 지사 참석", ref) == ["3", "김영록", "무안공항"]
    assert factcheck.missing_from_script("KTX 연결 다리", ref) == ["KTX"]
    # Ordinary words are not checked; particles do not matter.
    assert factcheck.missing_from_script("섬 주민들 다시 웃음", ref) == []
    assert factcheck.missing_from_script("신안군과 목포시는", ref) == []


# --- generation with an LLM (4.2) ---


def test_without_llm_only_the_script_defaults():
    g = generate(REPORT, None)
    assert g.llm_status == "off" and g.summary is None and g.hashtags == []
    assert g.title_candidates == [{"text": "압해도 새 다리 개통", "source": "script", "warnings": []}]


def test_llm_suggestions_are_added_as_candidates():
    g = generate(REPORT, StaticProvider(suggestions()), default_tags=["뉴스"])
    assert g.llm_status == "ok" and (g.input_tokens, g.output_tokens) == (120, 40)
    assert g.title == "압해도 새 다리 개통"  # the script title stays the default (spec 16-5 is open)
    assert [c["source"] for c in g.title_candidates] == ["script", "llm", "llm"]
    assert g.summary.startswith("길이 1킬로미터")
    assert g.tags == ["뉴스", "홍길동", "목포", "신안", "압해도", "다리", "신안군"]
    assert g.hashtags == ["#압해도", "#목포시"]
    assert g.thumbnail_text == "압해도 다리 개통"  # no memo in the script: the generated line is used
    assert g.warnings == []


def test_invented_facts_are_flagged_or_dropped():
    s = suggestions(
        titles=["김영록 지사, 압해도 다리 개통식 참석", "압해도 새 다리 개통"],
        summary="공사비 2,000억 원이 들었습니다.",
        keywords=["압해도", "무안공항"],
        hashtags=["#KTX"],
        thumbnail_text="3년 만의 개통",
    )
    g = generate(REPORT, StaticProvider(s))
    assert [c["text"] for c in g.title_candidates] == ["압해도 새 다리 개통", "김영록 지사, 압해도 다리 개통식 참석"]
    assert g.title_candidates[1]["warnings"] == ["대안 제목 1: 원고에 없는 표현 확인 필요 (김영록)"]
    assert "요약: 원고에 없는 표현 확인 필요 (2000)" in g.warnings
    assert "원고에서 확인되지 않아 뺀 키워드·해시태그: 무안공항, #KTX" in g.warnings
    assert "무안공항" not in g.tags and g.hashtags == []
    assert g.thumbnail_text == "압해도 새 다리 개통"  # the checked line is not the default
    assert g.thumbnail_text_candidates[1]["source"] == "llm" and g.thumbnail_text_candidates[1]["warnings"]


def test_llm_failure_keeps_defaults():
    g = generate(REPORT, StaticProvider(LLMError("Claude API 오류 (529): overloaded", 50, 0)))
    assert g.llm_status == "failed" and g.input_tokens == 50
    assert g.title == "압해도 새 다리 개통" and len(g.title_candidates) == 1
    assert g.warnings[0].startswith("LLM 보완 실패")
    g = generate(REPORT, StaticProvider(RuntimeError("bug")))
    assert g.llm_status == "failed"


def test_brief_gets_no_summary():
    g = generate(BRIEF, FakeLLMProvider())
    assert g.summary is None and g.llm_status == "ok"


def test_suggestions_are_validated():
    with pytest.raises(ValueError, match="exactly 2"):
        suggestions(titles=["하나"])
    with pytest.raises(ValueError, match="15"):
        suggestions(thumbnail_text="이 문구는 띄어쓰기를 빼도 열다섯 자가 넘습니다")
    assert suggestions(hashtags=["목포 시", "#목포시"]).hashtags == ["#목포시"]


def test_fake_provider_never_invents_facts():
    result = FakeLLMProvider().suggest(REPORT)
    g = generate(REPORT, FakeLLMProvider())
    assert len(result.suggestions.titles) == 2
    assert all(not c["warnings"] for c in g.title_candidates)


# --- Claude client ---


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def response(payload, stop_reason="end_turn", usage=(100, 30)):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]),
        model="claude-opus-5-5",
    )


def claude(*responses) -> tuple[ClaudeProvider, FakeMessages]:
    messages = FakeMessages(responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeProvider("key", "claude-opus-5-5", client=client), messages


def test_claude_request_sends_only_the_script_with_a_schema():
    good = suggestions().model_dump()
    provider, calls = claude(response(good))
    result = provider.suggest(REPORT)
    assert result.suggestions.titles == good["titles"] and result.attempts == 1
    call = calls.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    prompt = call["messages"][0]["content"]
    assert prompt == render_script(REPORT)
    assert "[리포트 본문]" in prompt and "[인터뷰 김주민]" in prompt


def test_claude_invalid_response_is_retried_once_with_the_error():
    bad = suggestions().model_dump() | {"titles": ["하나", "둘", "셋"]}
    provider, calls = claude(response(bad), response(suggestions().model_dump()))
    result = provider.suggest(REPORT)
    assert result.attempts == 2 and (result.input_tokens, result.output_tokens) == (200, 60)
    retry = calls.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "exactly 2" in retry[2]["content"]


def test_claude_gives_up_after_two_invalid_responses():
    provider, _ = claude(response("not json"), response({"titles": []}))
    with pytest.raises(LLMError, match="2회") as e:
        provider.suggest(REPORT)
    assert e.value.input_tokens == 200


def test_claude_refusal_and_api_errors_become_llm_errors():
    provider, _ = claude(response("", stop_reason="refusal"))
    with pytest.raises(LLMError, match="거절"):
        provider.suggest(REPORT)
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.InternalServerError("overloaded", response=httpx2.Response(529, request=req), body=None)
    provider, _ = claude(err)
    with pytest.raises(LLMError, match="529"):
        provider.suggest(REPORT)
