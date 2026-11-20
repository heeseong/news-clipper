"""Leave out items that already aired in another program (e.g. 뉴스투데이 vs the previous day's
뉴스데스크, 2026-11-20 user decision).

An item of today's cue sheet is "the same article" as an item of the compared cue sheet when
1. their article ids meet: any of {articleId, orgArticleId} on one side equals one on the other
   (the API copies a desk article into the morning cue sheet with a new articleId and the same
   orgArticleId), or
2. their normalized title and reporter are equal.
A matched article whose script was shortened or re-edited for the new program is a new article
and stays an upload target: the spoken text must be at least SAME_TEXT_RATIO similar to count as
the same broadcast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from .models import RundownItem

SAME_TEXT_RATIO = 0.9  # spoken-text similarity at or above which it is the same broadcast
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣]+")


@dataclass
class Aired:
    """An item of the compared cue sheet."""

    order: int
    ids: set[int]
    title_key: str
    reporter: str | None
    text: str


def _ids(raw: dict[str, Any]) -> set[int]:
    article = raw.get("article") or {}
    out = set()
    for k in ("articleId", "orgArticleId"):
        try:
            out.add(int(str(article.get(k)).strip()))
        except (TypeError, ValueError):
            continue
    return out


def title_key(title: str) -> str:
    return _NON_WORD_RE.sub("", title).lower()


def spoken_text(item: RundownItem) -> str:
    if item.script is None:
        return ""
    return _NON_WORD_RE.sub("", " ".join(s.text for s in item.script.sentences))


def aired_items(raw_items: list[dict[str, Any]], items: list[RundownItem]) -> list[Aired]:
    return [
        Aired(it.order, _ids(raw), title_key(it.title.clean), it.reporter, spoken_text(it))
        for raw, it in zip(raw_items, items, strict=True)
    ]


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def exclude_aired(
    raw_items: list[dict[str, Any]],
    items: list[RundownItem],
    aired: list[Aired],
    label: str,
) -> list[RundownItem]:
    """Mark upload targets that already aired. `label` names the compared broadcast, e.g.
    "11-19 뉴스데스크". Returns the items (updated in place)."""
    by_id = {i: a for a in aired for i in a.ids}
    by_title = {(a.title_key, a.reporter): a for a in aired}
    for raw, it in zip(raw_items, items, strict=True):
        if not it.upload:
            continue
        match = next((by_id[i] for i in _ids(raw) if i in by_id), None)
        how = "기사 ID"
        if match is None:
            match = by_title.get((title_key(it.title.clean), it.reporter))
            how = "제목·기자"
        if match is None:
            continue
        ratio = similarity(spoken_text(it), match.text)
        if ratio >= SAME_TEXT_RATIO:
            it.upload = False
            it.dedupe_note = f"{label} 방송분 ({match.order}번, {how} 일치)"
        else:
            it.dedupe_note = f"{label} {match.order}번 기사를 다시 편집 (원고 유사도 {ratio:.0%}): 새 기사로 업로드"
    return items


def previous_day(d: date) -> date:
    return d - timedelta(days=1)
