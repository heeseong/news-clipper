"""Newsroom (MARS-L) access: the provider interface and response normalization (spec 3.1, 5.4).

Two implementations:
- `MarsLProvider` (`marsl.py`): the real API, IF_MARSL_001~003.
- `FakeNewsroomProvider` (`fake.py`): serves JSON fixtures shaped exactly like the API responses.

IF_MARSL_004 (취재데스크 기사) is not used: the 003 `article` already carries its fields.

Providers return the `data` part of the responses unchanged (`raw`) next to the normalized
fields, so the raw response can be stored and re-parsed later (spec 5.4).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Protocol

from pydantic import BaseModel

TV = "TV"


class NewsroomError(Exception):
    """The newsroom could not answer; not worth retrying (bad request, auth, bad data)."""


class NewsroomUnavailable(NewsroomError):
    """Transient: timeout, connection error, 429 or 5xx. The caller may retry later."""


class NewsroomNotFound(NewsroomError):
    """The requested cue sheet does not exist."""


class RundownSummary(BaseModel):
    """One cue sheet as listed by IF_MARSL_001 / returned by IF_MARSL_002."""

    cue_sheet_id: int
    program_name: str  # trimmed (the API pads it: "뉴스데스크 ")
    program_type: str | None  # "TV" / "라디오"
    broadcast_date: date | None
    broadcast_start_time: time | None
    channel_code: str | None = None
    channel_name: str | None = None
    version: int  # cueSheetVersion (판 수); a new value means the cue sheet was edited
    anchors: list[str] = []
    updated_at: datetime | None = None
    raw: dict[str, Any]

    @property
    def is_tv(self) -> bool:
        return (self.program_type or "").strip().upper() == TV


class NewsroomProvider(Protocol):
    def list_rundowns(self, start: date, end: date) -> list[RundownSummary]: ...  # IF_MARSL_001

    def get_rundown(self, cue_sheet_id: int) -> RundownSummary: ...  # IF_MARSL_002

    def get_items(self, cue_sheet_id: int) -> list[dict[str, Any]]: ...  # IF_MARSL_003 `data`


# --- normalization -------------------------------------------------------------------------------


def pick(d: dict[str, Any], *names: str) -> Any:
    """First present value among field-name variants (the API mixes `updateDatetime` and
    `updateDateTime`); falls back to a case-insensitive match."""
    for n in names:
        if d.get(n) is not None:
            return d[n]
    lower = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = lower.get(n.lower())
        if v is not None:
            return v
    return None


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


_DATETIME_RE = re.compile(
    r"^\s*(\d{4})[-./]?(\d{2})[-./]?(\d{2})(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?"
)


def parse_datetime(value: Any) -> datetime | None:
    """Lenient: '2026-11-01 19:30:00', '2026-11-01T19:30', '20261101', '2026.11.01'."""
    if value is None or value == "":
        return None
    m = _DATETIME_RE.match(str(value))
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    dt = parse_datetime(value)
    return dt.date() if dt else None


def parse_time(value: Any) -> time | None:
    """'19:30:00', '19:30', '1930', or a datetime string ('2026-11-01T19:30')."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    if len(s) > 8 and (dt := parse_datetime(s)):
        return dt.time()
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})(?::?(\d{2}))?", s)
    if not m:
        return None
    try:
        return time(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    except ValueError:
        return None


def summary_from_raw(raw: dict[str, Any]) -> RundownSummary:
    cue_sheet_id = to_int(pick(raw, "cueSheetId"))
    if cue_sheet_id is None:
        raise NewsroomError("큐시트 응답에 cueSheetId가 없습니다")
    anchors = [a for a in (_text(pick(raw, k)) for k in ("anchor1Name", "anchor2Name")) if a]
    return RundownSummary(
        cue_sheet_id=cue_sheet_id,
        program_name=_text(pick(raw, "programName")) or "",
        program_type=_text(pick(raw, "programType")),
        broadcast_date=parse_date(pick(raw, "broadcastDate")),
        broadcast_start_time=parse_time(pick(raw, "broadcastStartTime")),
        channel_code=_text(pick(raw, "channelCode")),
        channel_name=_text(pick(raw, "channelName")),
        version=to_int(pick(raw, "cueSheetVersion")) or 0,
        anchors=anchors,
        updated_at=parse_datetime(pick(raw, "updateDatetime", "updateDateTime", "inputDatetime", "inputDateTime")),
        raw=raw,
    )


def unwrap(body: Any) -> Any:
    """Return `data` from `{status, success, data}`; raise on `{errorCode, message, success:false}`."""
    if not isinstance(body, dict):
        raise NewsroomError("MARS-L 응답 형식이 올바르지 않습니다")
    if body.get("success") is False or "errorCode" in body:
        code = body.get("errorCode")
        message = body.get("message") or "오류"
        if str(code) == "50000":
            raise NewsroomUnavailable(f"MARS-L 시스템 오류({code}): {message}")
        raise NewsroomError(f"MARS-L 오류({code}): {message}")
    return body.get("data")


def item_order(raw: dict[str, Any]) -> int:
    return to_int(pick(raw, "cueSheetItemOrderNumber")) or 0
