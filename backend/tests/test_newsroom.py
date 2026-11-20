"""Newsroom integration (M2): providers, cue sheet matching, the newsroom step and its API."""

from __future__ import annotations

import json
from datetime import date, time

import httpx
import pytest

from app.core.db import session_scope
from app.models import Job, JobStatus, ProgramProfile, Rundown, StepStatus
from app.newsroom import factory
from app.newsroom.fake import FakeNewsroomProvider
from app.newsroom.marsl import AUTH_HEADER, MarsLProvider
from app.newsroom.matching import match_rundown
from app.newsroom.provider import (
    NewsroomError,
    NewsroomNotFound,
    NewsroomUnavailable,
    parse_datetime,
    parse_time,
    pick,
    summary_from_raw,
)
from app.pipeline import orchestrator as orch
from app.pipeline import steps as step_registry
from app.pipeline import tasks
from app.storage import JobFiles

DAY = date(2026, 11, 2)


# --- normalization -------------------------------------------------------------------------------


def test_field_aliases_and_lenient_dates():
    assert pick({"updateDateTime": "x"}, "updateDatetime", "updateDateTime") == "x"
    assert pick({"UPDATEDATETIME": "y"}, "updateDatetime") == "y"
    assert parse_datetime("2026-11-01 19:30:00").hour == 19
    assert parse_datetime("2026-11-01T19:30").minute == 30
    assert parse_datetime("20261101").day == 1
    assert parse_datetime("nope") is None
    assert parse_time("19:30:00") == time(19, 30)
    assert parse_time("1930") == time(19, 30)
    assert parse_time("2026-11-01T19:30") == time(19, 30)

    s = summary_from_raw(
        {"cueSheetId": "7", "programName": "뉴스데스크 ", "programType": "TV", "broadcastDate": "2026-11-01",
         "broadcastStartTime": "19:30:00", "cueSheetVersion": "3", "anchor1Name": "가", "anchor2Name": None,
         "updateDateTime": "2026-11-01 18:00:00"}
    )
    assert (s.cue_sheet_id, s.program_name, s.version, s.anchors) == (7, "뉴스데스크", 3, ["가"])
    assert s.is_tv and s.updated_at.hour == 18


# --- MARS-L client -------------------------------------------------------------------------------


def ok(data):
    return httpx.Response(200, json={"status": "OK", "success": True, "data": data})


def marsl(handler, **kw) -> MarsLProvider:
    return MarsLProvider("https://marsl.test", "secret-key", backoff=0, transport=httpx.MockTransport(handler), **kw)


def test_marsl_requests_and_parses():
    seen = []
    fake = FakeNewsroomProvider.from_dir()

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers[AUTH_HEADER] == "secret-key"
        if req.url.path == "/api/external/cuesheet/list":
            return ok(fake.cue_sheets)
        if req.url.path == "/api/external/cuesheet/5001":
            return ok(fake.cue_sheets[0])
        if req.url.path == "/api/external/cuesheet/5001/detailed-item-list":
            return ok(list(reversed(fake.items[5001])))
        return httpx.Response(404)

    p = marsl(handler)
    day = p.list_rundowns(DAY, DAY)
    assert [s.cue_sheet_id for s in day] == [5001, 5002, 5003]
    assert dict(seen[0].url.params) == {"startDate": "2026-11-02", "endDate": "2026-11-02"}
    assert p.get_rundown(5001).version == 3
    items = p.get_items(5001)
    assert [i["cueSheetItemOrderNumber"] for i in items] == [str(n) for n in range(1, 8)]  # sorted
    with pytest.raises(NewsroomNotFound):
        p.get_rundown(1)


def test_marsl_retries_transient_errors():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("slow")
        if len(calls) == 2:
            return httpx.Response(503)
        return ok([])

    assert marsl(handler).list_rundowns(DAY, DAY) == []
    assert len(calls) == 3

    calls.clear()
    with pytest.raises(NewsroomUnavailable, match="429"):
        marsl(lambda r: (calls.append(1), httpx.Response(429))[1], max_retries=2).list_rundowns(DAY, DAY)
    assert len(calls) == 3


def test_marsl_errors_are_not_retried():
    calls = []

    def auth(req):
        calls.append(1)
        return httpx.Response(401, json={"errorCode": 20000, "message": "인증 오류", "success": False})

    with pytest.raises(NewsroomError, match="인증") as e:
        marsl(auth).list_rundowns(DAY, DAY)
    assert not isinstance(e.value, NewsroomUnavailable) and len(calls) == 1

    bad = lambda r: httpx.Response(200, json={"errorCode": 40000, "message": "잘못된 요청", "success": False})  # noqa: E731
    with pytest.raises(NewsroomError, match="40000"):
        marsl(bad).list_rundowns(DAY, DAY)
    system = lambda r: httpx.Response(200, json={"errorCode": 50000, "message": "x", "success": False})  # noqa: E731
    with pytest.raises(NewsroomUnavailable):
        marsl(system, max_retries=0).list_rundowns(DAY, DAY)


def test_marsl_requires_key_and_never_logs_it(caplog):
    with pytest.raises(NewsroomError, match="MARSL_API_KEY"):
        MarsLProvider("https://marsl.test", "")
    with pytest.raises(NewsroomUnavailable):
        marsl(lambda r: httpx.Response(500), max_retries=1).list_rundowns(DAY, DAY)
    assert "secret-key" not in caplog.text


# --- fake + matching -----------------------------------------------------------------------------


def test_fake_serves_fixtures():
    fake = FakeNewsroomProvider.from_dir()
    assert [s.cue_sheet_id for s in fake.list_rundowns(DAY, DAY)] == [5001, 5002, 5003]
    assert fake.list_rundowns(date(2026, 1, 1), date(2026, 1, 1)) == []
    mirrored = FakeNewsroomProvider.from_dir(any_date=True).list_rundowns(date(2026, 1, 1), date(2026, 1, 1))
    assert {s.broadcast_date for s in mirrored} == {date(2026, 1, 1)}
    assert len(fake.get_items(5001)) == 7
    with pytest.raises(NewsroomNotFound):
        fake.get_items(1)


def summaries(*specs):
    return [
        summary_from_raw({"cueSheetId": i, "programName": name, "programType": ptype,
                          "broadcastDate": "2026-11-02", "broadcastStartTime": start, "cueSheetVersion": v})
        for i, (name, ptype, start, v) in enumerate(specs, 1)
    ]


def test_matching_rules():
    day = summaries(
        ("뉴스데스크 ", "TV", "19:30:00", 3),
        ("뉴스데스크", "라디오", "00:00:00", 0),
        ("뉴스투데이", "TV", "06:00:00", 2),
    )
    m = match_rundown(day, "뉴스데스크")
    assert m.chosen.cue_sheet_id == 1 and m.warnings == []  # trimmed name, radio ignored
    assert match_rundown(day, "뉴스 데스크").chosen is None
    assert match_rundown(day, None).chosen is None

    two = summaries(("뉴스데스크", "TV", "19:30:00", 3), ("뉴스데스크", "TV", "12:00:00", 1))
    assert match_rundown(two, "뉴스데스크", time(19, 25)).chosen.cue_sheet_id == 1
    assert match_rundown(two, "뉴스데스크", time(12, 0)).chosen.cue_sheet_id == 2
    m = match_rundown(two, "뉴스데스크")  # no broadcast time in the profile
    assert m.chosen is None and m.ambiguous and len(m.candidates) == 2
    tie = match_rundown(two, "뉴스데스크", time(15, 45))
    assert tie.ambiguous
    assert match_rundown(two, "뉴스데스크", cue_sheet_id=2).chosen.cue_sheet_id == 2

    empty = summaries(("뉴스데스크", "TV", "19:30:00", 0))
    assert "판 수가 0" in match_rundown(empty, "뉴스데스크").warnings[0]


# --- the newsroom step ---------------------------------------------------------------------------


@pytest.fixture
def fake(app_env, monkeypatch):
    """Fake newsroom; the media steps do nothing so jobs need no video."""
    provider = FakeNewsroomProvider.from_dir()
    factory.set_provider(provider)
    for step in ("proxy", "analyze"):
        monkeypatch.setitem(step_registry._HANDLERS, step, lambda ctx: None)
    yield provider
    factory.set_provider(None)


def make_job(program: str | None = "뉴스데스크", broadcast_time=time(19, 30), **program_kw) -> int:
    with session_scope() as s:
        prog = None
        if program:
            prog = ProgramProfile(name=program, broadcast_time=broadcast_time, **program_kw)
            s.add(prog)
        job = Job(status=JobStatus.UPLOADED, broadcast_date=DAY, program=prog)
        orch.create_steps(job)
        s.add(job)
        s.flush()
        return job.id


def job_state(job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        return job.status, {r.step: r.status for r in job.steps}, job.error


def rundown_of(job_id: int) -> dict:
    with session_scope() as s:
        r = s.query(Rundown).filter_by(job_id=job_id).one()
        return {
            "id": r.newsroom_rundown_id,
            "edition": r.edition,
            "changes": r.changes,
            "items": {i.item_no: i for i in r.items},
        }


def test_newsroom_step_stores_rundown(fake):
    job_id = make_job()
    tasks.start_job(job_id)
    status, st, _ = job_state(job_id)
    assert st["newsroom"] == StepStatus.SUCCEEDED and status == JobStatus.REVIEW_READY

    r = rundown_of(job_id)
    assert r["id"] == 5001 and r["edition"] == 3 and r["changes"] == []
    items = r["items"]
    assert [n for n, i in items.items() if i.upload] == [1, 2, 5, 7]
    assert items[3].type == "S" and not items[3].upload
    assert items[4].source == "여수" and items[4].type == "R" and not items[4].upload
    assert items[2].title == "섬마을 의료 공백" and items[2].series == "기획"
    assert items[5].title == "항구 정비 사업 착공" and items[5].reporter == "최지훈"
    assert items[7].title == "전통시장 활기"
    assert items[1].planned_duration == 122
    lead = [s["text"] for s in items[1].parsed_script["sentences"] if s["section"] == "lead"]
    assert lead[1] == "새 다리가 오늘 개통했습니다."  # line broken mid-sentence in the API text

    files = JobFiles(job_id)
    raw = json.loads(files.rundown.read_text())
    assert raw["rundown"]["cueSheetId"] == 5001 and len(raw["items"]) == 7
    assert json.loads(files.script(5).read_text())["reporter"] == "최지훈"


def test_excluded_sources_from_program_profile(fake):
    job_id = make_job(excluded_sources=[])
    tasks.start_job(job_id)
    assert rundown_of(job_id)["items"][4].upload  # 여수 not excluded for this program


def test_no_cue_sheet_waits_then_refresh_resumes(fake):
    job_id = make_job(program="주말뉴스")
    tasks.start_job(job_id)
    status, st, error = job_state(job_id)
    assert status == JobStatus.WAITING_RUNDOWN
    assert st["newsroom"] == StepStatus.WAITING and st["proxy"] == StepStatus.SUCCEEDED
    assert st["align"] == StepStatus.PENDING
    assert "주말뉴스" in error

    # The user picks the cue sheet by hand.
    tasks.retry_step(job_id, "newsroom", {"cue_sheet_id": 5002})
    status, st, _ = job_state(job_id)
    assert status == JobStatus.REVIEW_READY and st["newsroom"] == StepStatus.SUCCEEDED
    assert rundown_of(job_id)["id"] == 5002
    with session_scope() as s:
        assert s.get(Job, job_id).cue_sheet_id == 5002


def test_ambiguous_cue_sheets_wait(fake):
    fake.cue_sheets.append({**fake.cue_sheets[0], "cueSheetId": 5009, "broadcastStartTime": "12:00:00"})
    job_id = make_job(broadcast_time=None)
    tasks.start_job(job_id)
    status, _, error = job_state(job_id)
    assert status == JobStatus.WAITING_RUNDOWN and "2개" in error


def test_failure_wins_over_waiting(fake, monkeypatch):
    def broken(ctx):
        raise step_registry.StepError("proxy broke")

    monkeypatch.setitem(step_registry._HANDLERS, "proxy", broken)
    job_id = make_job(program="주말뉴스")
    tasks.start_job(job_id)
    status, st, _ = job_state(job_id)
    assert status == JobStatus.FAILED and st["newsroom"] == StepStatus.WAITING


def test_edition_change_is_recorded(fake):
    job_id = make_job()
    tasks.start_job(job_id)
    # The desk edits the cue sheet: item 6 removed, item 7 moves up, a new item 8, version 4.
    items = fake.items[5001]
    last = {**items[6], "cueSheetItemOrderNumber": "6"}
    new = {**items[2], "cueSheetItemId": 199, "cueSheetItemOrderNumber": "7", "cueSheetItemTitle": "속보"}
    new["article"] = {**new["article"], "articleTitle": "속보", "articleId": 99199}
    fake.items[5001] = [*items[:5], last, new]
    fake.cue_sheets[0]["cueSheetVersion"] = 4

    tasks.retry_step(job_id, "newsroom")
    r = rundown_of(job_id)
    assert r["edition"] == 4 and len(r["items"]) == 7
    (change,) = r["changes"]
    assert (change["from"], change["to"]) == (3, 4)
    assert change["added"] == [{"item_no": 7, "title": "속보"}]
    assert change["removed"] == [{"item_no": 6, "title": "내일 아침 기온 뚝"}]
    assert change["moved"] == [{"title": "전통시장 활기", "from": 7, "to": 6}]

    tasks.retry_step(job_id, "newsroom")  # same edition again: nothing new recorded
    assert len(rundown_of(job_id)["changes"]) == 1


def test_newsroom_errors(fake, monkeypatch):
    from app.worker import celery_app

    monkeypatch.setattr(tasks, "backoff_seconds", lambda retries: 0)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    fake.fail_with = NewsroomUnavailable("MARS-L 응답 시간 초과")
    job_id = make_job()
    tasks.start_job(job_id)
    status, st, error = job_state(job_id)
    assert status == JobStatus.FAILED and st["newsroom"] == StepStatus.FAILED
    assert len([c for c in fake.calls if c[0] == "list"]) == 3  # 1 + 2 automatic retries

    fake.fail_with = NewsroomError("MARS-L 인증 실패")
    fake.calls.clear()
    job_id = make_job(program="뉴스투데이")
    tasks.start_job(job_id)
    status, _, error = job_state(job_id)
    assert status == JobStatus.FAILED and "인증" in error and len(fake.calls) == 1


# --- API -----------------------------------------------------------------------------------------


@pytest.fixture
def api(client, fake):
    return client


def test_preview_api(api):
    pid = api.post("/api/programs", json={"name": "뉴스데스크", "broadcast_time": "19:30:00"}).json()["id"]
    r = api.get("/api/newsroom/rundowns", params={"date": "2026-11-02", "program_id": pid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"]["cue_sheet_id"] == 5001 and body["matched"]["version"] == 3
    assert body["counts"] == {"items": 7, "reports": 5, "uploads": 4, "excluded": 1, "planned_total_seconds": 610}
    assert [d["cue_sheet_id"] for d in body["day_rundowns"]] == [5001, 5002]  # radio left out
    assert not body["ambiguous"] and body["warnings"] == []

    none = api.get("/api/newsroom/rundowns", params={"date": "2026-01-01", "program_id": pid}).json()
    assert none["matched"] is None and none["counts"] is None and "없습니다" in none["reason"]

    picked = api.get("/api/newsroom/rundowns", params={"date": "2026-11-02", "cue_sheet_id": 5002}).json()
    assert picked["matched"]["program_name"] == "뉴스투데이" and picked["counts"]["uploads"] == 1


def test_preview_api_newsroom_down(api, fake):
    fake.fail_with = NewsroomUnavailable("연결 실패")
    r = api.get("/api/newsroom/rundowns", params={"date": "2026-11-02"})
    assert r.status_code == 502 and "연결 실패" in r.json()["detail"]


def test_job_rundown_api_and_refresh(api):
    job_id = make_job(program="주말뉴스")
    assert api.get(f"/api/jobs/{job_id}/rundown").status_code == 404
    tasks.start_job(job_id)
    assert api.get(f"/api/jobs/{job_id}").json()["status"] == "WAITING_RUNDOWN"

    r = api.post(f"/api/jobs/{job_id}/rundown/refresh", json={"cue_sheet_id": 5001})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REVIEW_READY" and r.json()["cue_sheet_id"] == 5001
    body = api.get(f"/api/jobs/{job_id}/rundown").json()
    assert body["newsroom_rundown_id"] == 5001 and body["counts"]["uploads"] == 4
    assert [i["item_no"] for i in body["items"]] == list(range(1, 8))
    assert body["items"][0]["parsed_script"]["signoff_reporter"] == "홍길동"


def test_program_excluded_sources_api(api):
    r = api.post("/api/programs", json={"name": "뉴스데스크"})
    assert r.json()["excluded_sources"] == ["광주", "여수"]
    pid = r.json()["id"]
    r = api.put(f"/api/programs/{pid}", json={"name": "뉴스데스크", "excluded_sources": [" 순천 ", "순천", ""]})
    assert r.json()["excluded_sources"] == ["순천"]
