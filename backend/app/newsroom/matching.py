"""Find the cue sheet that belongs to a recording (spec 5.4).

Broadcast date → `programType == "TV"` → `programName` (trimmed) equals the program profile name
→ if several remain, the one whose `broadcastStartTime` is closest to the profile's broadcast time.
If that still does not decide it, the user picks one (upload form / refresh).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from .provider import RundownSummary


@dataclass
class MatchResult:
    chosen: RundownSummary | None
    candidates: list[RundownSummary]  # TV cue sheets of the day with the program's name
    reason: str  # why nothing was chosen, or how the choice was made
    warnings: list[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return self.chosen is None and len(self.candidates) > 1


def _norm(name: str | None) -> str:
    return " ".join((name or "").split())


def _minutes_apart(a: time, b: time) -> int:
    d = datetime.combine(datetime.min, a) - datetime.combine(datetime.min, b)
    return abs(int(d.total_seconds() // 60))


def version_warnings(summary: RundownSummary) -> list[str]:
    if summary.version == 0:
        return [f"큐시트 판 수가 0입니다. 아직 작성 중이거나 내용이 비어 있을 수 있습니다 (#{summary.cue_sheet_id})"]
    return []


def match_rundown(
    summaries: list[RundownSummary],
    program_name: str | None,
    broadcast_time: time | None = None,
    cue_sheet_id: int | None = None,
) -> MatchResult:
    tv = [s for s in summaries if s.is_tv]
    if cue_sheet_id is not None:
        chosen = next((s for s in summaries if s.cue_sheet_id == cue_sheet_id), None)
        if chosen is None:
            return MatchResult(None, tv, f"선택한 큐시트(#{cue_sheet_id})가 이 날짜 목록에 없습니다")
        return MatchResult(chosen, [chosen], "사용자가 선택", version_warnings(chosen))

    if not program_name:
        return MatchResult(None, tv, "프로그램이 지정되지 않아 큐시트를 고를 수 없습니다")
    name = _norm(program_name)
    candidates = [s for s in tv if _norm(s.program_name) == name]
    if not candidates:
        return MatchResult(None, [], f"'{name}' TV 큐시트가 없습니다")
    if len(candidates) == 1:
        chosen = candidates[0]
        return MatchResult(chosen, candidates, "프로그램명 일치", version_warnings(chosen))

    timed = [s for s in candidates if s.broadcast_start_time is not None]
    if broadcast_time is not None and timed:
        ranked = sorted(timed, key=lambda s: _minutes_apart(s.broadcast_start_time, broadcast_time))
        best = _minutes_apart(ranked[0].broadcast_start_time, broadcast_time)
        if len(ranked) == 1 or _minutes_apart(ranked[1].broadcast_start_time, broadcast_time) > best:
            chosen = ranked[0]
            return MatchResult(
                chosen,
                candidates,
                f"방송 시각이 가장 가까움 ({chosen.broadcast_start_time:%H:%M})",
                version_warnings(chosen),
            )
    return MatchResult(None, candidates, f"'{name}' 큐시트가 {len(candidates)}개입니다. 하나를 골라 주세요")
