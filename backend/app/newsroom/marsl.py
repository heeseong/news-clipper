"""MARS-L external API client (IF_MARSL_001~003, spec 3.1).

- Auth header `Mars-Secret: <access key>`. The key comes from settings (`MARSL_API_KEY`) and is
  never logged.
- Timeouts, connection errors, 429 and 5xx are retried with exponential backoff; if they persist
  `NewsroomUnavailable` is raised so the pipeline step can be retried later.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import date
from typing import Any

import httpx

from .provider import (
    NewsroomError,
    NewsroomNotFound,
    NewsroomUnavailable,
    RundownSummary,
    item_order,
    summary_from_raw,
    unwrap,
)

log = logging.getLogger(__name__)

PREFIX = "/api/external"
AUTH_HEADER = "Mars-Secret"


class MarsLProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise NewsroomError("MARS-L 액세스 키(MARSL_API_KEY)가 설정되지 않았습니다")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={AUTH_HEADER: api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._backoff = backoff

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = PREFIX + path
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.get(url, params=params)
            except httpx.TimeoutException as e:
                error: NewsroomError = NewsroomUnavailable(f"MARS-L 응답 시간 초과 ({path})")
                cause: Exception = e
            except httpx.TransportError as e:
                error, cause = NewsroomUnavailable(f"MARS-L 연결 실패 ({path}): {type(e).__name__}"), e
            else:
                try:
                    return self._handle(resp, path)
                except NewsroomUnavailable as e:
                    error, cause = e, e
            if attempt > self._max_retries:
                raise error from cause
            delay = self._backoff * 2 ** (attempt - 1)
            log.warning(
                "marsl request will be retried",
                extra={"path": path, "attempt": attempt, "error": str(error), "delay": delay},
            )
            _time.sleep(delay)

    @staticmethod
    def _handle(resp: httpx.Response, path: str) -> Any:
        status = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            body = None
        if status == 429 or status >= 500:
            raise NewsroomUnavailable(f"MARS-L HTTP {status} ({path})")
        if status == 404:
            raise NewsroomNotFound(f"MARS-L에 없는 큐시트입니다 ({path})")
        if status in (401, 403):
            raise NewsroomError(f"MARS-L 인증 실패(HTTP {status}): 액세스 키를 확인하세요")
        if body is None:
            raise NewsroomError(f"MARS-L 응답이 JSON이 아닙니다 (HTTP {status}, {path})")
        if status >= 400 and isinstance(body, dict) and "errorCode" not in body:
            raise NewsroomError(f"MARS-L HTTP {status} ({path})")
        return unwrap(body)

    def list_rundowns(self, start: date, end: date) -> list[RundownSummary]:
        data = self._get(
            "/cuesheet/list", {"startDate": start.isoformat(), "endDate": end.isoformat()}
        )
        if data is None:
            return []
        if not isinstance(data, list):
            raise NewsroomError("큐시트 목록 응답의 data가 배열이 아닙니다")
        return [summary_from_raw(d) for d in data if isinstance(d, dict)]

    def get_rundown(self, cue_sheet_id: int) -> RundownSummary:
        data = self._get(f"/cuesheet/{cue_sheet_id}")
        if not isinstance(data, dict):
            raise NewsroomNotFound(f"큐시트 {cue_sheet_id}를 찾을 수 없습니다")
        return summary_from_raw(data)

    def get_items(self, cue_sheet_id: int) -> list[dict[str, Any]]:
        data = self._get(f"/cuesheet/{cue_sheet_id}/detailed-item-list")
        if data is None:
            return []
        if not isinstance(data, list):
            raise NewsroomError("큐시트 상세 응답의 data가 배열이 아닙니다")
        return sorted((d for d in data if isinstance(d, dict)), key=item_order)
