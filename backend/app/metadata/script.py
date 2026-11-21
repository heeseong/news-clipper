"""The script of one clip, in the shape metadata generation needs (spec 5.7)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.models import Clip, Job
from app.newsroom.models import ItemType, ParsedScript, Section, SentenceKind

# Sentences that say nothing about the story.
_SKIP_IN_TEXT = (SentenceKind.INTRO, SentenceKind.SIGNOFF, SentenceKind.AMBIENT)


@dataclass
class ClipScript:
    kind: Literal["report", "brief"]
    title: str
    lead: list[str]  # anchor sentences (a brief: the whole text), without "○○○ 기자가 보도합니다"
    body: list[str]  # report sentences; interviews as "[인터뷰 이름] …". Empty for a brief
    reporter: str | None
    program: str
    broadcast_date: date | None
    station: str
    all_text: str = ""  # every sentence and the title (fact check reference)
    series: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
    # The script as written in the newsroom (MARS-L articleContent), for the YouTube description.
    # Empty when the cue sheet response is not stored: the sentences are used instead.
    script_text: str = ""


def _body_line(kind: SentenceKind, text: str, speaker: str | None) -> str:
    if kind in (SentenceKind.INTERVIEW, SentenceKind.SYNC):
        return f"[인터뷰 {speaker}] {text}" if speaker else f"[인터뷰] {text}"
    return text


# ◀ 앵 커 ▶, ◀ 리포트 ▶, ◀ SYNC ▶ 전경선 / … (not ◀ END ▶: the blank line after it separates sections)
MARKER_RE = re.compile(r"^◀(?!\s*E\s*N\s*D\s*▶).*▶")


def tidy_script(text: str) -> str:
    """The newsroom script as written, with only the whitespace tidied: spaces at line ends and
    starts, runs of blank lines, and blank lines right after a marker line such as ◀ 앵 커 ▶
    (2026-11-21). The markers and the editor's line breaks stay."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] and not MARKER_RE.match(out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def raw_script(item) -> str:
    """articleContent of the item in the stored cue sheet response (Rundown.raw)."""
    raw = (item.rundown.raw or {}).get("items") or [] if item.rundown is not None else []
    for r in raw:
        same_id = item.newsroom_item_id is not None and r.get("cueSheetItemId") == item.newsroom_item_id
        if same_id or (item.newsroom_item_id is None and str(r.get("cueSheetItemOrderNumber")) == str(item.item_no)):
            return tidy_script((r.get("article") or {}).get("articleContent") or "")
    return ""


def clip_script(clip: Clip, job: Job, station: str) -> ClipScript:
    item = clip.rundown_item
    parsed = ParsedScript.model_validate(item.parsed_script) if item.parsed_script else None
    sentences = parsed.sentences if parsed else []
    is_report = item.type == ItemType.REPORT and any(s.section == Section.REPORT for s in sentences)
    lead = [s.text for s in sentences if s.section == Section.LEAD and s.kind not in _SKIP_IN_TEXT]
    body = [
        _body_line(s.kind, s.text, s.speaker)
        for s in sentences
        if s.section == Section.REPORT and s.kind not in _SKIP_IN_TEXT
    ]
    if not is_report:  # a brief (or a report script without sections): everything is the text
        lead, body = lead + [s.split("] ", 1)[-1] for s in body], []
    reporter = item.reporter or (parsed.signoff_reporter if parsed else None)
    return ClipScript(
        kind="report" if is_report else "brief",
        title=item.title,
        lead=lead,
        body=body,
        reporter=reporter,
        program=job.program.name if job.program else "",
        broadcast_date=job.broadcast_date,
        station=station,
        all_text="\n".join([item.title, item.title_raw or "", *(s.text for s in sentences)]),
        series=item.series,
        script_text=raw_script(item) or "\n\n".join(s.text for s in sentences),
    )
