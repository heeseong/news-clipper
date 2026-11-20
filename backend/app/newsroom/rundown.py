"""Turn MARS-L cue sheet items (IF_MARSL_003) into RundownItems."""

from __future__ import annotations

import re
from typing import Any

from .models import ItemType, ParsedScript, RundownItem, SentenceKind, Title, count_syllables
from .script_parser import parse_script

# Speaking rates (syllables per second) for the reading-time estimate.
# Starting values; calibrate against golden-set broadcasts (eval/).
ANCHOR_RATE = 5.7
REPORT_RATE = 5.7
SENTENCE_PAUSE = 0.35  # seconds between sentences
REPORT_TYPE_WORDS = ("리포트",)
REPORT_TYPE_CODES = ("typ_002",)  # article.articleTypeCode for 리포트 (typ_001 = 스트레이트)
# Items produced by these stations are not uploaded (still used as alignment anchors).
# Per-program setting later (ProgramProfile).
EXCLUDED_SOURCES = ("광주", "여수")
# Department names that mean "our own newsroom" in reporter labels such as 홍길동(보도부).
OWN_DEPARTMENTS = ("보도부", "보도국", "취재부", "편집부", "영상취재부")

_REPORT_MARK_RE = re.compile(r"^\s*R\s*\]\s*|\s*\(R(?:-\d+)?\)\s*$|^\s*\[?리포트\]\s*")
_SERIES_RE = re.compile(r"^\s*\[?(?P<series>[^\[\]\s]{1,10})\]\s*")
_STATION_RE = re.compile(r"^\s*(?P<station>[가-힣]{2,4})\)\s*")
_REPORTER_UNIT_RE = re.compile(r"^\s*(?P<name>[^\s(]+)\s*\((?P<unit>[^)]+)\)\s*$")


def clean_title(raw: str) -> Title:
    text = (raw or "").strip()
    prev = None
    while prev != text:
        prev, text = text, _REPORT_MARK_RE.sub("", text).strip()
    station = None
    m = _STATION_RE.match(text)
    if m:
        station = m.group("station")
        text = text[m.end() :].strip()
    series = None
    m = _SERIES_RE.match(text)
    if m and m.group("series").upper() != "R":
        series = m.group("series")
        text = text[m.end() :].strip()
    return Title(raw=raw, clean=text, series=series, station=station)


def split_reporter(label: str | None) -> tuple[str | None, str | None]:
    """'이재원(광주)' -> ('이재원', '광주'); '김양훈' -> ('김양훈', None)."""
    if not label or not label.strip():
        return None, None
    m = _REPORTER_UNIT_RE.match(label)
    if m:
        return m.group("name"), m.group("unit").strip()
    return label.strip(), None


def detect_source(title: Title, reporter_unit: str | None, extra_units: tuple[str | None, ...] = ()) -> tuple[str | None, str | None]:
    """Find another station's name in the title prefix or the reporter's unit."""
    if title.station:
        return title.station, f"title prefix {title.station})"
    for unit in (reporter_unit, *extra_units):
        if unit and unit not in OWN_DEPARTMENTS:
            return unit, f"reporter unit ({unit})"
    return None, None


def classify(
    remark: str | None,
    item_content: str | None,
    script: ParsedScript | None,
    article_type: str | None = None,
    article_type_code: str | None = None,
) -> tuple[ItemType, str]:
    """Decide R/S/OTHER. Returns (type, reason).

    Order: cue sheet remark/content (the 비고 column) -> the article's type
    (articleTypeCodeName / articleTypeCode) -> script structure.
    """
    for field_name, value in (("remark", remark), ("cueSheetItemContent", item_content)):
        if value and any(w in value for w in REPORT_TYPE_WORDS):
            return ItemType.REPORT, f"{field_name}={value.strip()}"
    if (article_type and any(w in article_type for w in REPORT_TYPE_WORDS)) or (
        article_type_code in REPORT_TYPE_CODES
    ):
        return ItemType.REPORT, f"articleType={article_type or article_type_code}"
    if script is not None and script.sentences:
        has_report = any(s.section == "report" for s in script.sentences)
        if script.structured and has_report and script.signoff_reporter:
            return ItemType.REPORT, "script has ◀리포트▶ section and sign-off"
        if not script.structured:
            return ItemType.BRIEF, "anchor-only script"
        return ItemType.BRIEF, "structured script without report section/sign-off"
    return ItemType.OTHER, "no spoken script"


def estimate_seconds(script: ParsedScript | None) -> float | None:
    if script is None or not script.sentences:
        return None
    total = 0.0
    for s in script.sentences:
        rate = ANCHOR_RATE if s.kind in (SentenceKind.ANCHOR, SentenceKind.INTRO) else REPORT_RATE
        total += count_syllables(s.text) / rate + SENTENCE_PAUSE
    return round(total, 1)


def parse_duration(value: Any) -> int | None:
    """'02:48' -> 168, 168 -> 168, None/'' -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*", str(value))
    if not m:
        return None
    h, mnt, sec = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mnt * 60 + sec


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def planned_from_article(article: dict[str, Any]) -> int | None:
    """Planned length from the article (seconds): the cue sheet's 시간 column.

    = ancMentCttTime + artclCttTime + artclExtTime (ext can be negative).
    Checked against the 2026-11-04 print-out: 22/22 items match.
    """
    parts = [_int(article.get(k)) for k in ("ancMentCttTime", "artclCttTime", "artclExtTime")]
    total = sum(p or 0 for p in parts)
    return total if total > 0 else None


def source_from_article(article: dict[str, Any]) -> tuple[str | None, str | None]:
    """Other station named by the article's origin fields (IF_MARSL_003 article)."""
    same = article.get("isSameChannel", article.get("sameChannel"))
    origin = (article.get("originChannelName") or "").strip() or None
    own = (article.get("channelName") or "").strip() or None
    if same is False and origin:
        return origin, f"isSameChannel=false (origin {origin})"
    if origin and own and origin != own:
        return origin, f"originChannelName={origin} != {own}"
    return None, None


def strip_reporter_suffix(title: Title, names: tuple[str | None, ...]) -> Title:
    """'항구 정비 착공/최지훈' -> '항구 정비 착공' when 최지훈 is the item's reporter.

    Only the reporter's own name is removed, so endings such as '광주/전남' stay.
    """
    for name in names:
        if not name:
            continue
        m = re.search(rf"\s*/\s*{re.escape(name)}\s*$", title.clean)
        if m:
            return title.model_copy(update={"clean": title.clean[: m.start()].rstrip()})
    return title


def build_item(
    raw: dict[str, Any],
    planned_seconds: int | None = None,
    department: str | None = None,
    excluded_sources: tuple[str, ...] | list[str] | None = None,
) -> RundownItem:
    """Build a RundownItem from one IF_MARSL_003 `data[]` element.

    `planned_seconds` overrides the article's planned length (e.g. from the print-out).
    `department` is the reporter's unit when known from elsewhere (e.g. the print-out).
    `excluded_sources` are stations whose reports are not uploaded (program profile setting);
    None means the default `EXCLUDED_SOURCES`.
    """
    excluded = EXCLUDED_SOURCES if excluded_sources is None else tuple(excluded_sources)
    article = raw.get("article") or {}
    content = article.get("articleContent")
    script = parse_script(content, article.get("memo")) if content and content.strip() else None
    if script is not None and not script.sentences:
        script = None
    title_raw = article.get("articleTitle") or raw.get("cueSheetItemTitle") or ""
    article_type = (article.get("articleTypeCodeName") or "").strip() or None
    item_type, reason = classify(
        raw.get("remark"), raw.get("cueSheetItemContent"), script, article_type, article.get("articleTypeCode")
    )
    # originReporterName is the author; reporterName can be the desk that imported the article.
    reporter, unit = split_reporter(article.get("originReporterName") or article.get("reporterName"))
    title = clean_title(title_raw)
    source, source_reason = source_from_article(article)
    if source is None:
        source, source_reason = detect_source(title, unit, (department,))
    signoff = script.signoff_reporter if script is not None else None
    if script is not None and item_type == ItemType.REPORT and reporter and signoff:
        if signoff not in reporter:
            # The cue sheet can name the desk that filed the item; the voice is the sign-off's.
            script.warnings.append(f"SIGNOFF_REPORTER_MISMATCH:{signoff}!={reporter}")
            reporter = signoff
    elif reporter is None and signoff:
        reporter = signoff
    title = strip_reporter_suffix(title, (reporter, signoff, article.get("reporterName")))
    if planned_seconds is None:
        planned_seconds = planned_from_article(article)
    return RundownItem(
        order=_int(raw.get("cueSheetItemOrderNumber")) or 0,
        item_id=_int(raw.get("cueSheetItemId")),
        article_id=_int(article.get("articleId")),
        article_type=article_type,
        title=title,
        reporter=reporter,
        item_type=item_type,
        type_reason=reason,
        source=source,
        source_reason=source_reason,
        upload=item_type == ItemType.REPORT and (source is None or source not in excluded),
        planned_seconds=planned_seconds,
        estimated_seconds=estimate_seconds(script),
        remark=(raw.get("remark") or None),
        script=script,
    )
