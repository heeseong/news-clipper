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
REPORT.script_text = (
    "◀ 앵 커 ▶\n목포시와 신안군을 잇는 새 다리가\n오늘 개통했습니다.\n\n◀ 리포트 ▶\n길이 1킬로미터의 다리가 문을 열었습니다.\n\n"
    "◀ 인터뷰 ▶ 김주민\n이제 병원 가기가 편해졌습니다.\n\nMBC 뉴스 홍길동입니다."
)
REPORT_SPLIT = ClipScript(**{**REPORT.__dict__, "title": "압해도 새 다리…오늘 개통"})
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
    assert d.title == "압해도 새 다리 개통[목포MBC 뉴스데스크]"  # like the channel
    # "[목포MBC 뉴스]", then the newsroom script as written; no footer unless the program has one.
    assert d.description == "[목포MBC 뉴스]\n" + REPORT.script_text
    with_footer = defaults.build(REPORT, "{reporter_credit}", [])
    assert with_footer.description == "[목포MBC 뉴스]\n" + REPORT.script_text + "\n\n취재: 홍길동 기자"
    assert d.tags == ["뉴스", "목포MBC", "홍길동", "목포", "신안"]
    assert d.thumbnail_text == "압해도 새 다리 개통"


def test_description_fits_youtube_and_keeps_the_footer():
    long = "\n\n".join(f"{i}번째 문단입니다. 아주 긴 원고가 이어집니다." for i in range(400))
    d = defaults.build(ClipScript(**{**REPORT.__dict__, "script_text": long}), None, [])
    assert len(defaults.build(ClipScript(**{**REPORT.__dict__, "script_text": long}), "{reporter_credit}", []).description.encode()) <= 5000
    assert len(d.description.encode()) <= 5000
    assert d.description.startswith("[목포MBC 뉴스]\n0번째") and d.description.endswith("…")


USER_EXAMPLE = """◀ 앵 커 ▶
전남 국립의대 선정 과정의 공정성 논란으로
시작된 서남권 시민들의 정례 집회가
77일 만에 막을 내렸습니다.

신광하 기자 입니다.
◀ END ▶

◀ 리포트 ▶

◀ SYNC ▶
"(순천대 의대 추천안을) 즉각 반려하라! 반려
하라! 반려하라!"

MBC 뉴스 신광하 입니다.
◀ END ▶"""


def test_description_matches_the_users_example():
    """The format the user gave (2026-11-21): header line, then the script exactly as written,
    editor line breaks (even inside a word) and ◀ ▶ markers included."""
    from app.metadata.script import tidy_script

    raw = USER_EXAMPLE.replace("\n전남", "\n 전남").replace("입니다.\n◀ END", "입니다.  \n◀ END") + "\n"
    script = ClipScript(**{**REPORT.__dict__, "script_text": tidy_script(raw)})
    assert defaults.build(script, None, []).description == "[목포MBC 뉴스]\n" + USER_EXAMPLE


def test_script_whitespace_is_tidied_but_the_text_kept():
    from app.metadata.script import tidy_script

    raw = "◀ 앵 커 ▶\r\n\n 민형배 시장이 \n항의했습니다.\n\n\n\n 서일영 기자입니다.  \n"
    assert tidy_script(raw) == "◀ 앵 커 ▶\n\n민형배 시장이\n항의했습니다.\n\n서일영 기자입니다."


def test_brief_description_without_a_stored_script_uses_its_text_and_footer_template():
    d = defaults.build(BRIEF, "{program} | {date}\n{reporter_credit}\n{unknown}", [])
    assert d.description == (
        "[목포MBC 뉴스]\n진도군이 겨울 축제 준비에 들어갔습니다. 올해는 체험 행사가 늘어납니다.\n\n뉴스데스크 | 2026년 11월 4일"
    )
    assert d.tags == ["진도"]


def test_thumbnail_text_follows_the_channel():
    # Reports: the title in two lines, split at its ellipsis (examples from the channel).
    assert defaults.thumbnail_text('"공정성 외친 77일"...광장 떠나 장기전으로') == '"공정성 외친 77일"...\n광장 떠나 장기전으로'
    assert defaults.thumbnail_text("일제강점기부터 지금까지..'100년의 목포극장'") == "일제강점기부터 지금까지\n'100년의 목포극장'"
    assert defaults.thumbnail_text("짧은 제목") == "짧은 제목"
    assert defaults.thumbnail_text("전남 서남권 섬 지역 응급 의료 공백 여전히 심각") == "전남 서남권 섬 지역 응급\n의료 공백 여전히 심각"
    assert defaults.thumbnail_text("여수·순천 통합 논의") == "여수·순천 통합 논의"
    # Briefs: no text (the frame shows the on-air title caption).
    assert defaults.thumbnail_text("출장비 부풀리기 의혹 1년 만에…시의원 해외연수 4억 원", "brief") == ""


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
    assert g.title_candidates == [{"text": "압해도 새 다리 개통[목포MBC 뉴스데스크]", "source": "script", "warnings": []}]


def test_llm_suggestions_are_added_as_candidates():
    g = generate(REPORT, StaticProvider(suggestions()), default_tags=["뉴스"])
    assert g.llm_status == "ok" and (g.input_tokens, g.output_tokens) == (120, 40)
    assert g.title == "압해도 새 다리 개통[목포MBC 뉴스데스크]"  # the script title is the default (2026-11-21)
    assert [c["source"] for c in g.title_candidates] == ["script", "llm", "llm"]
    assert g.summary.startswith("길이 1킬로미터")
    assert g.tags == ["뉴스", "홍길동", "목포", "신안", "압해도", "다리", "신안군"]
    assert g.hashtags == ["#압해도", "#목포시"]
    # No memo and no ellipsis in the title: the checked LLM line is used.
    assert g.thumbnail_text == "압해도 다리 개통"
    split = generate(REPORT_SPLIT, StaticProvider(suggestions()))
    assert split.thumbnail_text == "압해도 새 다리\n오늘 개통"  # the title split at its ellipsis wins
    assert [c["source"] for c in split.thumbnail_text_candidates] == ["title", "llm"]
    brief = generate(BRIEF, StaticProvider(suggestions(thumbnail_text="축제 준비")))
    assert brief.thumbnail_text == "" and brief.thumbnail_text_candidates == []  # nothing drawn on a brief
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
    assert [c["text"] for c in g.title_candidates] == ["압해도 새 다리 개통[목포MBC 뉴스데스크]", "김영록 지사, 압해도 다리 개통식 참석[목포MBC 뉴스데스크]"]
    assert g.title_candidates[1]["warnings"] == ["대안 제목 1: 원고에 없는 표현 확인 필요 (김영록)"]
    assert "요약: 원고에 없는 표현 확인 필요 (2000)" in g.warnings
    assert "원고에서 확인되지 않아 뺀 키워드·해시태그: 무안공항, #KTX" in g.warnings
    assert "무안공항" not in g.tags and g.hashtags == []
    assert g.thumbnail_text == "압해도 새 다리 개통"  # the checked line is not the default
    assert g.thumbnail_text_candidates[1]["source"] == "llm" and g.thumbnail_text_candidates[1]["warnings"]


def test_llm_failure_keeps_defaults():
    g = generate(REPORT, StaticProvider(LLMError("Claude API 오류 (529): overloaded", 50, 0)))
    assert g.llm_status == "failed" and g.input_tokens == 50
    assert g.title == "압해도 새 다리 개통[목포MBC 뉴스데스크]" and len(g.title_candidates) == 1
    assert g.warnings[0].startswith("LLM 보완 실패")
    g = generate(REPORT, StaticProvider(RuntimeError("bug")))
    assert g.llm_status == "failed"


def test_brief_gets_no_summary():
    g = generate(BRIEF, FakeLLMProvider())
    assert g.summary is None and g.llm_status == "ok"


def test_suggestions_are_validated():
    with pytest.raises(ValueError, match="exactly 2"):
        suggestions(titles=["하나"])
    with pytest.raises(ValueError, match="16"):
        suggestions(thumbnail_text="이 문구는 띄어쓰기를 빼도 열여섯 자가 넘는 한 줄입니다")
    with pytest.raises(ValueError, match="2 lines"):
        suggestions(thumbnail_text="하나\n둘\n셋")
    assert suggestions(thumbnail_text=" '고병원성 AI' 잇단 확진 \n\n 한 달 빨라진 발생 ").thumbnail_text == (
        "'고병원성 AI' 잇단 확진\n한 달 빨라진 발생"
    )
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


def test_youtube_title_template():
    assert defaults.youtube_title("새 다리 개통", REPORT, None) == ("새 다리 개통[목포MBC 뉴스데스크]", [])
    assert defaults.youtube_title("새 다리 개통", REPORT, "{title} | {program}")[0] == "새 다리 개통 | 뉴스데스크"
    no_program = ClipScript(**{**REPORT.__dict__, "program": ""})
    assert defaults.youtube_title("새 다리 개통", no_program, None)[0] == "새 다리 개통[목포MBC]"
    long = "가" * 95
    title, warnings = defaults.youtube_title(long, REPORT, None)
    assert len(title) == 100 and title.endswith("…[목포MBC 뉴스데스크]") and warnings
    g = generate(ClipScript(**{**REPORT.__dict__, "title": long}), None)
    assert g.warnings == warnings and g.title_candidates[0]["warnings"] == warnings
