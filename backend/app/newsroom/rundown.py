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

_REPORT_MARK_RE = re.compile(r"^\s*R\s*\]\s*|\s*\(R\)\s*$|^\s*\[?리포트\]\s*")
_SERIES_RE = re.compile(r"^\s*\[?(?P<series>[^\[\]\s]{1,10})\]\s*")


def clean_title(raw: str) -> Title:
    text = (raw or "").strip()
    prev = None
    while prev != text:
        prev, text = text, _REPORT_MARK_RE.sub("", text).strip()
    series = None
    m = _SERIES_RE.match(text)
    if m and m.group("series").upper() != "R":
        series = m.group("series")
        text = text[m.end() :].strip()
    return Title(raw=raw, clean=text, series=series)


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


def build_item(raw: dict[str, Any], planned_seconds: int | None = None) -> RundownItem:
    """Build a RundownItem from one IF_MARSL_003 `data[]` element."""
    article = raw.get("article") or {}
    content = article.get("articleContent")
    script = parse_script(content, article.get("memo")) if content and content.strip() else None
    if script is not None and not script.sentences:
        script = None
    title_raw = article.get("articleTitle") or raw.get("cueSheetItemTitle") or ""
    item_type, reason = classify(raw.get("remark"), raw.get("cueSheetItemContent"), script)
    reporter = (article.get("reporterName") or "").strip() or None
    if script is not None and item_type == ItemType.REPORT and reporter and script.signoff_reporter:
        if script.signoff_reporter not in reporter:
            script.warnings.append(f"SIGNOFF_REPORTER_MISMATCH:{script.signoff_reporter}!={reporter}")
    return RundownItem(
        order=int(str(raw.get("cueSheetItemOrderNumber", "0")).strip() or 0),
        title=clean_title(title_raw),
        reporter=reporter,
        item_type=item_type,
        type_reason=reason,
        planned_seconds=planned_seconds,
        estimated_seconds=estimate_seconds(script),
        remark=(raw.get("remark") or None),
        script=script,
    )
