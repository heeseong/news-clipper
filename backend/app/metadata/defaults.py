"""Script-based metadata (spec 5.7, plan 4.1): what a clip gets without any LLM.

- title: the cleaned script title
- description: the anchor lead (a brief: its whole text) + the program's footer template
- tags: program default tags + reporter + region names found in the script (+ LLM keywords later)
- thumbnail text: the script memo is always empty in the real API, so whole words of the title
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .llm import THUMBNAIL_TEXT_MAX, cut_words
from .script import ClipScript

YOUTUBE_TAGS_MAX_CHARS = 500  # all tags together
# Placeholders: {station} {program} {date} {reporter} {reporter_credit} {title}
DEFAULT_FOOTER = "{reporter_credit}\n{station} {program} {date} 방송"

# 전라남도 22개 시·군과 광주 (tags). A name counts only at the start of a word.
REGIONS = (
    "목포 여수 순천 나주 광양 담양 곡성 구례 고흥 보성 화순 장흥 강진 해남 영암 무안 함평 영광 장성 "
    "완도 진도 신안 광주 전남"
).split()
_REGION_RE = re.compile(rf"(?<![가-힣])({'|'.join(REGIONS)})")


@dataclass
class Defaults:
    title: str
    description: str
    tags: list[str]
    thumbnail_text: str


def format_date(script: ClipScript) -> str:
    d = script.broadcast_date
    return f"{d.year}년 {d.month}월 {d.day}일" if d else ""


def footer(script: ClipScript, template: str | None) -> str:
    values = defaultdict(
        str,
        station=script.station,
        program=script.program,
        date=format_date(script),
        reporter=script.reporter or "",
        reporter_credit=f"취재: {script.reporter} 기자" if script.reporter else "",
        title=script.title,
    )
    try:
        text = (template or DEFAULT_FOOTER).format_map(values)
    except (ValueError, IndexError):  # a stray brace in a hand-written template
        text = template or ""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def description(script: ClipScript, footer_template: str | None) -> str:
    parts = [" ".join(script.lead).strip(), footer(script, footer_template)]
    return "\n\n".join(p for p in parts if p)


def regions(text: str) -> list[str]:
    return list(dict.fromkeys(_REGION_RE.findall(text)))


def limit_tags(tags: list[str]) -> list[str]:
    """Drop duplicates and empty tags; keep the total under YouTube's 500 characters."""
    out: list[str] = []
    total = 0
    for tag in dict.fromkeys(t.strip().lstrip("#") for t in tags):
        if not tag or "<" in tag or ">" in tag:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)  # quotes and comma
        if total + cost > YOUTUBE_TAGS_MAX_CHARS:
            break
        out.append(tag)
        total += cost
    return out


def tags(script: ClipScript, default_tags: list[str] | None, extra: list[str] | None = None) -> list[str]:
    base = list(default_tags or [])
    if script.reporter:
        base.append(script.reporter)
    base += regions(script.all_text)
    return limit_tags(base + list(extra or []))


def thumbnail_text(title: str) -> str:
    """Whole words of the title that fit; the first clause when the title has one."""
    for sep in (",", "…", "...", "·", '"', "'"):
        head = title.split(sep)[0].strip()
        if head and head != title and len(re.sub(r"\s", "", head)) <= THUMBNAIL_TEXT_MAX:
            return head
    return cut_words(title, THUMBNAIL_TEXT_MAX) or title[:THUMBNAIL_TEXT_MAX]


def build(script: ClipScript, footer_template: str | None, default_tags: list[str] | None) -> Defaults:
    return Defaults(
        title=script.title,
        description=description(script, footer_template),
        tags=tags(script, default_tags),
        thumbnail_text=thumbnail_text(script.title),
    )
