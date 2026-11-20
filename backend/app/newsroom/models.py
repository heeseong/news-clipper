"""Data models for parsed cue sheet items and scripts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class Section(StrEnum):
    LEAD = "lead"  # ◀앵커▶ … anchor lead read before the report
    REPORT = "report"  # ◀리포트▶ … reporter package


class SentenceKind(StrEnum):
    ANCHOR = "anchor"  # anchor speech (lead, or the whole text of a brief)
    INTRO = "intro"  # "○○○ 기자가 보도합니다." at the end of the lead
    NARRATION = "narration"  # reporter voice-over
    INTERVIEW = "interview"  # ◀INT▶ / ◀☏INT▶
    SYNC = "sync"  # ◀SYNC▶ (speech recorded on site)
    AMBIENT = "ambient"  # ◀현장음▶
    STANDUP = "standup"  # ◀st-up▶ / S/U
    SIGNOFF = "signoff"  # "MBC뉴스 ○○○입니다."


class ItemType(StrEnum):
    REPORT = "R"
    BRIEF = "S"
    OTHER = "OTHER"  # title, headlines, closing, separators, CM


class Sentence(BaseModel):
    section: Section
    kind: SentenceKind
    text: str
    speaker: str | None = None


class ScriptNotes(BaseModel):
    """Production notes written in the left column of the script (memo)."""

    thumbnail: str | None = None  # 썸네일 문구 (line breaks preserved)
    top_caption: str | None = None  # 상단 / 좌상단 자막
    credits: list[str] = Field(default_factory=list)  # 영상취재, CG, 그래픽 …
    other: list[str] = Field(default_factory=list)  # supers, editing requests …


class ParsedScript(BaseModel):
    structured: bool  # True when the script uses ◀앵커▶/◀리포트▶ sections
    sentences: list[Sentence]
    signoff_reporter: str | None = None
    notes: ScriptNotes = Field(default_factory=ScriptNotes)
    warnings: list[str] = Field(default_factory=list)

    @property
    def lead(self) -> list[Sentence]:
        return [s for s in self.sentences if s.section == Section.LEAD]

    @property
    def report(self) -> list[Sentence]:
        return [s for s in self.sentences if s.section == Section.REPORT]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def syllable_count(self) -> int:
        return sum(count_syllables(s.text) for s in self.sentences)


class Title(BaseModel):
    raw: str
    clean: str  # report markers removed
    series: str | None = None  # e.g. "기획4" from "기획4]…"
    station: str | None = None  # e.g. "광주" from "광주)…" (item from another station)


class RundownItem(BaseModel):
    order: int
    item_id: int | None = None  # cueSheetItemId
    article_id: int | None = None  # article.articleId
    article_type: str | None = None  # article.articleTypeCodeName, e.g. 리포트 / 스트레이트
    title: Title
    reporter: str | None  # sign-off reporter when the script has one
    cue_sheet_reporter: str | None = None  # reporterName when it differs (usually the desk)
    item_type: ItemType
    type_reason: str  # why item_type was chosen (for review / debugging)
    source: str | None = None  # other station that produced the item, e.g. "광주" (None = own)
    source_reason: str | None = None
    upload: bool = False  # own-station reports (R) and straight briefs (S)
    planned_seconds: int | None = None  # None when the source has no duration
    estimated_seconds: float | None = None  # reading-time estimate from the script
    remark: str | None = None
    script: ParsedScript | None = None


def count_syllables(text: str) -> int:
    """Count spoken units: Hangul syllables, plus Latin letters and digits."""
    n = 0
    for ch in text:
        if "가" <= ch <= "힣" or ch.isdigit() or ("a" <= ch.lower() <= "z"):
            n += 1
    return n
