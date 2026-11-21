"""Script-based metadata (spec 5.7, plan 4.1): what a clip gets without any LLM.

- title: the cleaned script title in the program's title template, by default
  "{title}[{station} {program}]" → "…원고 제목[목포MBC 뉴스데스크]" like the channel (2026-11-21)
- description: "[목포MBC 뉴스]" + the script as written in the newsroom (2026-11-21, user decision;
  before: the anchor lead + a footer), within YouTube's 5,000 bytes. The program's footer template
  is added only when set; the hashtags are added at the end on upload
- tags: program default tags + reporter + region names found in the script (+ LLM keywords later)
- thumbnail text: the script memo is always empty in the real API, so the title (see thumbnail_text)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .llm import THUMBNAIL_TEXT_MAX
from .script import ClipScript

YOUTUBE_TAGS_MAX_CHARS = 500  # all tags together
# Placeholders: {station} {program} {date} {reporter} {reporter_credit} {title}
DEFAULT_TITLE_TEMPLATE = "{title}[{station} {program}]"
YOUTUBE_TITLE_MAX = 100
DEFAULT_FOOTER = "{reporter_credit}\n{station} {program} {date} 방송"
# The description starts with this line, then the script as written (2026-11-21, like the channel).
DESCRIPTION_HEADER = "[{station} 뉴스]"

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


def _values(script: ClipScript, title: str | None = None) -> defaultdict:
    return defaultdict(
        str,
        station=script.station,
        program=script.program,
        date=format_date(script),
        reporter=script.reporter or "",
        reporter_credit=f"취재: {script.reporter} 기자" if script.reporter else "",
        title=script.title if title is None else title,
    )


def _format(template: str, values: defaultdict) -> str:
    try:
        return template.format_map(values)
    except (ValueError, IndexError):  # a stray brace in a hand-written template
        return template


def youtube_title(title: str, script: ClipScript, template: str | None) -> tuple[str, list[str]]:
    """The upload title: `title` in the program's template. Over YouTube's 100 characters the
    title part is shortened (the program suffix stays) and a warning says so."""
    template = template or DEFAULT_TITLE_TEMPLATE

    def render(t: str) -> str:
        text = re.sub(r" {2,}", " ", _format(template, _values(script, t)))
        return re.sub(r"\s+\]", "]", re.sub(r"\[\s+", "[", text)).strip()  # "[목포MBC ]" without a program

    full = render(title)
    if len(full) <= YOUTUBE_TITLE_MAX:
        return full, []
    room = YOUTUBE_TITLE_MAX - (len(full) - len(title)) - 1
    return render(title[: max(0, room)].rstrip() + "…"), ["제목이 유튜브 제한(100자)을 넘어 원고 제목 부분을 줄였습니다"]


def footer(script: ClipScript, template: str | None) -> str:
    text = _format(template or DEFAULT_FOOTER, _values(script))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
HASHTAG_ROOM_BYTES = 300  # hashtags are added at upload (app/publish/service.with_hashtags)


def _fit_bytes(text: str, limit: int) -> str:
    """Cut at a line (or a sentence) so the text stays under `limit` UTF-8 bytes."""
    if len(text.encode()) <= limit:
        return text
    cut = text.encode()[: max(0, limit - len("…".encode()))].decode(errors="ignore")
    for sep in ("\n", ". ", " "):
        i = cut.rfind(sep)
        if i > len(cut) // 2:
            cut = cut[: i + (1 if sep != "\n" else 0)]
            break
    return cut.rstrip() + "…"


def description(script: ClipScript, footer_template: str | None) -> str:
    """"[목포MBC 뉴스]", then the newsroom script as written (2026-11-21, user's example). A footer
    is added only when the program profile has one (description_footer)."""
    head = _format(DESCRIPTION_HEADER, _values(script)).strip()
    tail = footer(script, footer_template) if footer_template and footer_template.strip() else ""
    body = script.script_text.strip() or " ".join(script.lead).strip()
    room = YOUTUBE_DESCRIPTION_MAX_BYTES - HASHTAG_ROOM_BYTES - len(head.encode()) - len(tail.encode()) - 4
    text = "\n".join(p for p in (head, _fit_bytes(body, room)) if p)
    return f"{text}\n\n{tail}" if tail else text


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


SEPARATORS = ("…", "...", "..")  # not "·": 여수·순천


def split_at_separator(text: str) -> tuple[str, str] | None:
    """'A…B' → ('A…', 'B'): titles often join two phrases with an ellipsis."""
    for sep in SEPARATORS:
        head, found, tail = text.partition(sep)
        if found and head.strip() and tail.strip():
            return (head + sep).strip(), tail.strip()
    return None


def _balanced_lines(title: str) -> str:
    """Two lines of about equal length, broken at a space."""
    words = title.split()
    if len(words) < 2:
        return title
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def thumbnail_text(title: str, kind: str = "report") -> str:
    """What the channel puts on thumbnails (2026-11-20):
    - a brief: nothing (its thumbnail is a frame showing the on-air title caption),
    - a report: the title in two big lines, split at its ellipsis
      ("공정성 외친 77일"...광장 떠나 장기전으로 → two lines); a short title stays one line."""
    title = " ".join(title.split())
    if kind == "brief":
        return ""
    parts = split_at_separator(title)
    if parts:
        head, tail = parts
        # Drop the ellipsis at the line end, except after a quote ("…77일"...), as the channel does.
        bare = head.rstrip(".…")
        if bare != head and not bare.endswith(("'", '"')):
            head = bare
        return f"{head}\n{tail}"
    if len(title.replace(" ", "")) <= THUMBNAIL_TEXT_MAX:
        return title
    return _balanced_lines(title)


def build(
    script: ClipScript,
    footer_template: str | None,
    default_tags: list[str] | None,
    title_template: str | None = None,
) -> Defaults:
    return Defaults(
        title=youtube_title(script.title, script, title_template)[0],
        description=description(script, footer_template),
        tags=tags(script, default_tags),
        thumbnail_text=thumbnail_text(script.title, script.kind),
    )
