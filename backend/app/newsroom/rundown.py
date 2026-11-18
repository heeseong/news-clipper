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
# Items produced by these stations are not uploaded (still used as alignment anchors).
# Per-program setting later (ProgramProfile).
EXCLUDED_SOURCES = ("광주",)
# Department names that mean "our own newsroom" in reporter labels such as 홍길동(보도부).
OWN_DEPARTMENTS = ("보도부", "보도국", "취재부", "편집부", "영상취재부")

_REPORT_MARK_RE = re.compile(r"^\s*R\s*\]\s*|\s*\(R\)\s*$|^\s*\[?리포트\]\s*")
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


def classify(remark: str | None, item_content: str | None, script: ParsedScript | None) -> tuple[ItemType, str]:
    """Decide R/S/OTHER. Returns (type, reason)."""
    for field_name, value in (("remark", remark), ("cueSheetItemContent", item_content)):
        if value and any(w in value for w in REPORT_TYPE_WORDS):
            return ItemType.REPORT, f"{field_name}={value.strip()}"
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


def build_item(
    raw: dict[str, Any], planned_seconds: int | None = None, department: str | None = None
) -> RundownItem:
    """Build a RundownItem from one IF_MARSL_003 `data[]` element.

    `department` is the reporter's unit when known from elsewhere (e.g. the print-out).
    """
    article = raw.get("article") or {}
    content = article.get("articleContent")
    script = parse_script(content, article.get("memo")) if content and content.strip() else None
    if script is not None and not script.sentences:
        script = None
    title_raw = article.get("articleTitle") or raw.get("cueSheetItemTitle") or ""
    item_type, reason = classify(raw.get("remark"), raw.get("cueSheetItemContent"), script)
    reporter, unit = split_reporter(article.get("reporterName"))
    title = clean_title(title_raw)
    source, source_reason = detect_source(title, unit, (department,))
    if script is not None and item_type == ItemType.REPORT and reporter and script.signoff_reporter:
        if script.signoff_reporter not in reporter:
            script.warnings.append(f"SIGNOFF_REPORTER_MISMATCH:{script.signoff_reporter}!={reporter}")
    return RundownItem(
        order=int(str(raw.get("cueSheetItemOrderNumber", "0")).strip() or 0),
        title=title,
        reporter=reporter,
        item_type=item_type,
        type_reason=reason,
        source=source,
        source_reason=source_reason,
        upload=item_type == ItemType.REPORT and (source is None or source not in EXCLUDED_SOURCES),
        planned_seconds=planned_seconds,
        estimated_seconds=estimate_seconds(script),
        remark=(raw.get("remark") or None),
        script=script,
    )
