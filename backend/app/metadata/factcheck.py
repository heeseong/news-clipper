"""Fact check of LLM text against the script (spec 5.7, plan 4.3).

The LLM may only rephrase what the script says. Numbers and proper nouns in its output must be in
the script; anything else is reported as a warning for the reviewer (not removed from titles).

Korean proper nouns are found without a morphological analyzer, by shape:
- Latin words (KTX, LNG …) and numbers,
- words ending in a place or organization suffix (목포시, 신안군, 압해도, 전남도청, 목포대학교 …),
- the word before a title or an honorific (김영록 지사, 홍길동 씨 …).
A word counts as found when it appears anywhere in the script with spaces removed, so word order
and spacing changes are fine.
"""

from __future__ import annotations

import re

# Longest first: the first match is stripped.
_JOSA = sorted(
    "에서는 으로는 에게서 으로서 이라는 이라고 에서도 까지 부터 에서 으로 에게 처럼 보다 라는 라고 "
    "와 과 은 는 이 가 을 를 의 에 도 로 만 께".split(),
    key=len,
    reverse=True,
)
_SUFFIXES = sorted(
    "대학교 위원회 교육청 경찰서 소방서 보건소 의료원 대학 병원 학교 센터 재단 협회 조합 공사 공단 "
    "은행 기업 회사 그룹 해수욕장 국립공원 시 군 구 도 읍 면 동 리 섬 항 역 청 원 당 부 처 다리 대교".split(),
    key=len,
    reverse=True,
)
_TITLES = (
    "기자|씨|의원|시장|군수|구청장|지사|교수|대표|장관|위원장|청장|원장|회장|총장|대통령|교육감|이사장|"
    "국장|과장|소장|센터장|후보|선수|감독|작가|대표이사|총리"
)
_PERSON_RE = re.compile(rf"([가-힣]{{2,4}})\s*(?:{_TITLES})(?![가-힣])")
_NUMBER_RE = re.compile(r"\d[\d,.]*")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.\-]*")
_HANGUL_RE = re.compile(r"[가-힣]+")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _strip_josa(word: str) -> str:
    for j in _JOSA:
        if word.endswith(j) and len(word) - len(j) >= 2:
            return word[: -len(j)]
    return word


def _normalize_number(n: str) -> str:
    return n.rstrip(".,").replace(",", "")


def proper_nouns(text: str) -> list[str]:
    """Numbers, Latin words and proper-noun-looking Korean words in `text`, in order."""
    found: list[str] = []
    for m in _NUMBER_RE.finditer(text):
        found.append(_normalize_number(m.group()))
    for m in _LATIN_RE.finditer(text):
        if len(m.group().rstrip(".-")) >= 2:
            found.append(m.group().rstrip(".-"))
    for m in _PERSON_RE.finditer(text):
        found.append(m.group(1))
    for m in _HANGUL_RE.finditer(text):
        # 압해도 ends in a suffix that is also a particle (도): try the word as written too.
        for word in dict.fromkeys((_strip_josa(m.group()), m.group())):
            # Suffixes need a 2+ syllable name in front (다시, 도시 are not places).
            if any(word.endswith(sfx) and len(word) - len(sfx) >= 2 for sfx in _SUFFIXES):
                found.append(word)
                break
    return list(dict.fromkeys(f for f in found if f))


def missing_from_script(text: str, script: str) -> list[str]:
    """Proper nouns and numbers of `text` that the script does not contain."""
    ref = _compact(script)
    ref_numbers = {_normalize_number(n) for n in _NUMBER_RE.findall(script)}
    missing = []
    for term in proper_nouns(text):
        if term[0].isdigit():
            if term not in ref_numbers and term not in ref:
                missing.append(term)
        elif _compact(term) not in ref and _compact(_strip_josa(term)) not in ref:
            missing.append(term)
    return missing


def warnings_for(label: str, text: str, script: str) -> list[str]:
    missing = missing_from_script(text, script)
    return [f"{label}: 원고에 없는 표현 확인 필요 ({', '.join(missing)})"] if missing else []
