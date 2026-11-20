"""Fake newsroom: serves synthetic fixtures shaped exactly like MARS-L responses.

Fixture directory layout (each file is a full API response, envelope included):

    cuesheet_list.json           IF_MARSL_001 (every cue sheet, all dates)
    cuesheet_{id}_items.json     IF_MARSL_003 for one cue sheet

IF_MARSL_002 is answered from the list (the API returns the same fields).

With `any_date=True` (development default) every cue sheet in the fixtures is served for
whatever date is asked, so any synthetic recording can be matched to a cue sheet.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from .provider import (
    NewsroomNotFound,
    RundownSummary,
    item_order,
    pick,
    summary_from_raw,
    to_int,
    unwrap,
)

FIXTURES = Path(__file__).parent / "fixtures"


class FakeNewsroomProvider:
    def __init__(
        self,
        cue_sheets: list[dict[str, Any]],
        items: dict[int, list[dict[str, Any]]],
        *,
        any_date: bool = False,
    ) -> None:
        self.cue_sheets = cue_sheets  # IF_MARSL_001 `data` elements (mutable, for tests)
        self.items = items  # cueSheetId -> IF_MARSL_003 `data`
        self.any_date = any_date
        self.calls: list[tuple[str, Any]] = []
        self._listed_date: date | None = None  # any_date: the date the last list was asked for
        # Set to an exception to make every call raise it (tests).
        self.fail_with: Exception | None = None

    @classmethod
    def from_dir(cls, path: Path = FIXTURES, *, any_date: bool = False) -> FakeNewsroomProvider:
        cue_sheets = unwrap(json.loads((path / "cuesheet_list.json").read_text(encoding="utf-8"))) or []
        items: dict[int, list[dict[str, Any]]] = {}
        for f in sorted(path.glob("cuesheet_*_items.json")):
            cid = to_int(f.name.split("_")[1])
            if cid is not None:
                items[cid] = unwrap(json.loads(f.read_text(encoding="utf-8"))) or []
        return cls(cue_sheets, items, any_date=any_date)

    def _check(self, call: str, arg: Any) -> None:
        self.calls.append((call, arg))
        if self.fail_with is not None:
            raise self.fail_with

    def _raw(self, cue_sheet_id: int) -> dict[str, Any]:
        for raw in self.cue_sheets:
            if to_int(pick(raw, "cueSheetId")) == cue_sheet_id:
                return raw
        raise NewsroomNotFound(f"큐시트 {cue_sheet_id}를 찾을 수 없습니다")

    def list_rundowns(self, start: date, end: date) -> list[RundownSummary]:
        self._check("list", (start, end))
        self._listed_date = start
        out = []
        for raw in self.cue_sheets:
            if self.any_date:
                raw = {**copy.deepcopy(raw), "broadcastDate": start.isoformat()}
            summary = summary_from_raw(raw)
            if summary.broadcast_date and start <= summary.broadcast_date <= end:
                out.append(summary)
        return out

    def get_rundown(self, cue_sheet_id: int) -> RundownSummary:
        self._check("get", cue_sheet_id)
        raw = copy.deepcopy(self._raw(cue_sheet_id))
        if self.any_date and self._listed_date:
            raw["broadcastDate"] = self._listed_date.isoformat()
        return summary_from_raw(raw)

    def get_items(self, cue_sheet_id: int) -> list[dict[str, Any]]:
        self._check("items", cue_sheet_id)
        self._raw(cue_sheet_id)
        return sorted(copy.deepcopy(self.items.get(cue_sheet_id, [])), key=item_order)
