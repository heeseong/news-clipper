"""Parse MARS-L article text (원고) into spoken sentences, sections and notes.

The script format follows newsroom conventions observed in MARS-L scripts:

    ◀앵커▶            anchor lead (also written ◀ 앵 커 ▶)
    ○○○ 기자가 보도합니다.
    ◀END▶             optional
    ◀리포트▶          reporter package
    ◀INT▶ 이름/직함   interview (also ◀인터뷰▶); following lines are the quote
    ◀SYNC▶, ◀현장음▶, ◀☏INT▶   other recorded speech
    ◀st-up▶ / S/U     reporter stand-up
    MBC뉴스 ○○○입니다.   sign-off
    ◀END▶

Text that is not spoken is removed from sentences: directives such as (CG),
통CG], (화면전환), *super*, editing marks (/, //) and parenthetical notes such
as 오늘(11) or "(저희가) …" which are subtitle annotations, not speech.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    ParsedScript,
    ScriptNotes,
    Section,
    Sentence,
    SentenceKind,
    count_syllables,
)

MARKER_RE = re.compile(r"◀\s*([^◀▶]*?)\s*▶")
# Directive prefixes glued to the start of a line, e.g. "통CG]", "반투명CG]".
DIRECTIVE_PREFIX_RE = re.compile(r"^(?:[^\s\]◀]{0,6}CG\]\s*)+")
DIRECTIVE_ONLY_RE = re.compile(
    r"^(?:\(?\s*(?:CG|화면전환|자막|그래픽)\s*\)?|/+|\+|\[[^\]]*\]\s*\S{0,6})$", re.IGNORECASE
)
BARE_STANDUP_RE = re.compile(r"^(?:S/U|st-?up|스탠드업)\s*(.*)$", re.IGNORECASE)
PAREN_RE = re.compile(r"[(（][^()（）]*[)）]")
SUPER_RE = re.compile(r"\*[^*]*\*")
BRACKET_RE = re.compile(r"\[[^\[\]]*\]")  # [영상] 홈런 style directives
SLASH_RE = re.compile(r"(?<!\d)/+|/+(?!\d)")
SIGNOFF_RE = re.compile(r"^(?:MBC|엠비씨)\s*뉴스\s*(?P<name>[가-힣]{2,4})\s*(?:기자\s*)?입니다\.?$", re.IGNORECASE)
# "○○○ 기자가 보도합니다." / "○○○ 기자입니다." / "이어서 ○○○ 기자 입니다." (spacing varies)
INTRO_RE = re.compile(r"기자(?:가\s.*니다|\s*입니다)\.?$")
SENTENCE_RE = re.compile(r".+?(?:[.?!]+[\"”’']?(?=\s|$)|$)")

_SPEECH_MARKERS = {
    "INT": SentenceKind.INTERVIEW,
    "인터뷰": SentenceKind.INTERVIEW,
    "SYNC": SentenceKind.SYNC,
    "현장음": SentenceKind.AMBIENT,
}
_STANDUP_MARKERS = {"ST-UP", "STUP", "S/U", "STANDUP", "스탠드업"}


@dataclass
class _Segment:
    section: Section | None
    kind: SentenceKind | None
    speaker: str | None
    lines: list[str]


def parse_script(content: str | None, memo: str | None = None) -> ParsedScript:
    """Parse article content (and optional memo) into a ParsedScript."""
    warnings: list[str] = []
    content = _normalize(content or "")
    structured = bool(re.search(r"◀\s*(앵\s*커|리\s*포\s*트)\s*▶", content))

    segments = _segment(content, structured, warnings)
    sentences = _to_sentences(segments, structured, warnings)

    signoff_reporter = None
    for s in sentences:
        if s.kind == SentenceKind.SIGNOFF:
            m = SIGNOFF_RE.match(s.text)
            signoff_reporter = m.group("name") if m else None

    if structured:
        if not any(s.section == Section.REPORT for s in sentences):
            warnings.append("NO_REPORT_SECTION")
        elif signoff_reporter is None:
            warnings.append("NO_SIGNOFF")
        if not any(s.section == Section.LEAD for s in sentences):
            warnings.append("NO_ANCHOR_LEAD")

    return ParsedScript(
        structured=structured,
        sentences=sentences,
        signoff_reporter=signoff_reporter,
        notes=parse_memo(memo),
        warnings=warnings,
    )


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    return text.replace("․", "·")


def _segment(content: str, structured: bool, warnings: list[str]) -> list[_Segment]:
    """Walk paragraphs/lines and group text by (section, kind, speaker)."""
    segments: list[_Segment] = []
    section: Section | None = None if structured else Section.LEAD
    last_section: Section = Section.LEAD

    for paragraph in re.split(r"\n\s*\n", content):
        kind: SentenceKind | None = None  # speech blocks end with the paragraph
        speaker: str | None = None
        speaker_open = False  # speaker line ended with "/": next line continues it
        for raw_line in paragraph.split("\n"):
            line = DIRECTIVE_PREFIX_RE.sub("", raw_line.strip())
            if not line or DIRECTIVE_ONLY_RE.match(line):
                continue
            if speaker_open and "◀" not in line:
                speaker = _clean_speaker(f"{speaker or ''} / {line}")
                speaker_open = False
                continue

            m = BARE_STANDUP_RE.match(line)
            if m:
                kind, speaker, line = SentenceKind.STANDUP, None, m.group(1)

            parts = MARKER_RE.split(line)
            # parts = [text, marker, text, marker, text, ...]
            for i, part in enumerate(parts):
                if i % 2 == 1:  # marker
                    name = re.sub(r"\s+", "", part).upper()
                    if name == "앵커":
                        section, last_section, kind, speaker = Section.LEAD, Section.LEAD, None, None
                    elif name == "리포트":
                        section, last_section, kind, speaker = Section.REPORT, Section.REPORT, None, None
                    elif name == "END":
                        section, kind, speaker = None, None, None
                    elif name in _STANDUP_MARKERS:
                        kind, speaker = SentenceKind.STANDUP, None
                    elif (speech := _speech_kind(name)) is not None:
                        kind = speech
                        # The rest of this line names the speaker.
                        rest = parts[i + 1] if i + 1 < len(parts) else ""
                        speaker = _clean_speaker(rest)
                        speaker_open = rest.strip().endswith("/")
                        if i + 1 < len(parts):
                            parts[i + 1] = ""
                    else:
                        warnings.append(f"UNKNOWN_MARKER:{part.strip()}")
                    continue

                text = part.strip()
                if not text:
                    continue
                current = section
                if current is None:
                    if structured:
                        warnings.append("TEXT_OUTSIDE_SECTION")
                    current = last_section
                if segments and (segments[-1].section, segments[-1].kind, segments[-1].speaker) == (
                    current,
                    kind,
                    speaker,
                ):
                    segments[-1].lines.append(text)
                else:
                    segments.append(_Segment(current, kind, speaker, [text]))
        # A blank line also ends a sentence run: start a new segment next time.
        segments.append(_Segment(None, None, None, []))

    return [s for s in segments if s.lines]


def _speech_kind(name: str) -> SentenceKind | None:
    for key, kind in _SPEECH_MARKERS.items():
        if key in name:
            return kind
    return None


def _clean_speaker(text: str) -> str | None:
    text = re.sub(r"\s*/\s*", " / ", text.strip()).strip(" /")
    return text or None


def _to_sentences(segments: list[_Segment], structured: bool, warnings: list[str]) -> list[Sentence]:
    sentences: list[Sentence] = []
    for seg in segments:
        assert seg.section is not None
        default = SentenceKind.ANCHOR if seg.section == Section.LEAD else SentenceKind.NARRATION
        text = clean_spoken(" ".join(seg.lines))
        for chunk in SENTENCE_RE.findall(text):
            chunk = chunk.strip()
            if count_syllables(chunk) == 0:
                continue
            kind = seg.kind or default
            if SIGNOFF_RE.match(chunk):
                kind = SentenceKind.SIGNOFF
            elif structured and seg.section == Section.LEAD and seg.kind is None and INTRO_RE.search(chunk):
                kind = SentenceKind.INTRO
            sentences.append(Sentence(section=seg.section, kind=kind, text=chunk, speaker=seg.speaker))
    return sentences


def clean_spoken(text: str) -> str:
    """Remove non-spoken annotations from a line of script text."""
    prev = None
    while prev != text:  # nested parentheses
        prev, text = text, PAREN_RE.sub(" ", text)
    text = SUPER_RE.sub(" ", text)
    text = BRACKET_RE.sub(" ", text)
    text = SLASH_RE.sub(" ", text)
    text = re.sub(r"\s+([.,?!])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r'(^|\s)(["“])\s+', r"\1\2", text)  # opening quote
    text = re.sub(r'([.?!,])\s+(["”])(?=\s|$)', r"\1\2", text)  # closing quote
    return text.strip()


# --- memo (left-column notes) -------------------------------------------------

THUMB_RE = re.compile(r"^썸네일\s*[:：]?\s*(.*)$")
TOP_RE = re.compile(r"^(?:좌상단|우상단|상단)\s*[:：]\s*(.*)$")
CREDIT_RE = re.compile(r"^(?:영상취재|영상편집|촬영|CG|그래픽)\s*[:：]?\s*[가-힣]{2,4}(?:\s*[,·]\s*[가-힣]{2,4})*$")
NOTE_NOISE_RE = re.compile(r"^(?:[^\s]{0,6}CG\]?|/+|\+|CG)$")


def parse_memo(memo: str | None) -> ScriptNotes:
    notes = ScriptNotes()
    if not memo:
        return notes
    for block in re.split(r"\n\s*\n", _normalize(memo)):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        lines = [ln for ln in lines if not NOTE_NOISE_RE.match(ln)]
        if not lines:
            continue
        first = lines[0]
        if (m := THUMB_RE.match(first)) is not None:
            body = [m.group(1)] + lines[1:] if m.group(1) else lines[1:]
            if body and notes.thumbnail is None:
                notes.thumbnail = "\n".join(body)
        elif (m := TOP_RE.match(first)) is not None:
            if notes.top_caption is None:
                notes.top_caption = " ".join([m.group(1)] + lines[1:]).strip()
        elif CREDIT_RE.match(first):
            notes.credits.extend(lines)
        else:
            notes.other.append("\n".join(lines))
    return notes
