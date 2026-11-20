"""뉴스투데이: leave out what the previous day's 뉴스데스크 already aired (2026-11-20)."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from app.newsroom import dedupe, service
from app.newsroom.fake import FakeNewsroomProvider
from app.newsroom.rundown import build_item
from tests.test_script_parser import API_REPORT

DESK_DAY, TODAY = date(2026, 11, 19), date(2026, 11, 20)


def article(order: int, article_id: int, org_id: int, *, title="새 다리 개통(R)", content=None, reporter="홍길동"):
    raw = copy.deepcopy(API_REPORT)
    raw["cueSheetItemOrderNumber"] = str(order)
    raw["cueSheetItemTitle"] = title
    a = raw["article"]
    a.update(articleId=article_id, orgArticleId=org_id, articleTitle=title, reporterName=reporter,
             originReporterName=reporter)
    if content is not None:
        a["articleContent"] = content
    return raw


BRIEF = "군이 겨울 축제 준비에 들어갔습니다.\n올해는 체험 행사가 늘어납니다."
LONG_REPORT = API_REPORT["article"]["articleContent"].replace(
    "길이 1킬로미터의 다\n리가 문을 열었습니다.",
    "길이 1킬로미터의 다리가 문을 열었습니다. 하루 통행량은 2만 대로 예상됩니다. 주변 상권도 기대가 큽니다.",
)


def apply(today_raw, desk_raw):
    items = [build_item(r) for r in today_raw]
    prev = [build_item(r) for r in desk_raw]
    dedupe.exclude_aired(today_raw, items, dedupe.aired_items(desk_raw, prev), "11-19 뉴스데스크")
    return {i.order: i for i in items}


def test_same_article_by_org_id_is_left_out():
    desk = [article(4, 1001, 500)]
    today = [article(2, 2001, 500)]  # copied into the morning cue sheet: new articleId, same origin
    item = apply(today, desk)[2]
    assert not item.upload
    assert item.dedupe_note == "11-19 뉴스데스크 방송분 (4번, 기사 ID 일치)"


def test_same_article_by_title_and_reporter():
    desk = [article(4, 1001, 500, title="R]새 다리 개통")]
    today = [article(2, 2001, 600, title="새 다리 개통(R)")]  # unrelated ids, same cleaned title
    assert not apply(today, desk)[2].upload
    other_reporter = [article(2, 2001, 600, reporter="김영희", content=API_REPORT["article"]["articleContent"]
                              .replace("홍길동", "김영희"))]
    assert apply(other_reporter, desk)[2].upload


def test_reedited_article_is_new():
    desk = [article(4, 1001, 500, content=LONG_REPORT)]
    today = [article(2, 2001, 500)]  # same origin, shortened for the morning news
    item = apply(today, desk)[2]
    assert item.upload
    assert "다시 편집" in item.dedupe_note and "새 기사로 업로드" in item.dedupe_note


def test_new_articles_and_non_targets_are_untouched():
    desk = [article(4, 1001, 500)]
    brief = article(3, 2002, 700, title="축제 준비", content=BRIEF)
    brief["remark"] = None
    brief["article"].update(articleTypeCode="typ_001", articleTypeCodeName="스트레이트")
    items = apply([brief], desk)
    assert items[3].upload and items[3].dedupe_note is None


def test_headline_segment_is_not_an_upload_target():
    raw = article(1, 3001, 3000, title="투데이 주요뉴스", content=BRIEF)
    raw["remark"] = None
    raw["article"].update(articleTypeCode="typ_001", articleTypeCodeName="스트레이트")
    item = build_item(raw)
    assert item.item_type == "OTHER" and not item.upload and item.script is not None  # still an anchor


def provider(desk_items, today_items, desk_day=DESK_DAY):
    def cue(cid, name, day, start):
        return {"cueSheetId": cid, "programName": name, "programType": "TV", "broadcastDate": day.isoformat(),
                "broadcastStartTime": start, "cueSheetVersion": 1}

    return FakeNewsroomProvider(
        [cue(1, "뉴스데스크 ", desk_day, "19:30:00"), cue(2, "뉴스투데이", TODAY, "07:10:00")],
        {1: desk_items, 2: today_items},
    )


TODAY_PROGRAM = service.ProgramInfo("뉴스투데이", None, None, "뉴스데스크")


def test_fetch_compares_with_previous_days_newsdesk():
    p = provider([article(1, 1001, 500)], [article(1, 2001, 500), article(2, 2002, 800, title="새 소식(R)")])
    fetched = service.fetch(p, TODAY, TODAY_PROGRAM)
    assert [i.upload for i in fetched.items] == [False, True]
    assert fetched.compared["rundown"]["cueSheetId"] == 1 and fetched.warnings == []
    counts = service.summarize(fetched.items)
    assert (counts["uploads"], counts["aired"], counts["excluded"]) == (1, 1, 0)
    # The desk program itself is not compared with anything.
    assert service.fetch(p, DESK_DAY, service.ProgramInfo("뉴스데스크", None, None)).compared is None


def test_missing_previous_newsdesk_only_warns():
    p = provider([article(1, 1001, 500)], [article(1, 2001, 500)], desk_day=date(2026, 11, 10))
    fetched = service.fetch(p, TODAY, TODAY_PROGRAM)
    assert fetched.items[0].upload  # nothing left out
    assert "11-19 뉴스데스크 큐시트를 찾지 못해" in fetched.warnings[0]


def test_newsroom_outage_while_comparing_propagates():
    from app.newsroom.provider import NewsroomUnavailable

    p = provider([article(1, 1001, 500)], [article(1, 2001, 500)])
    calls = {"n": 0}
    real = p.list_rundowns

    def flaky(start, end):
        calls["n"] += 1
        if start == DESK_DAY:
            raise NewsroomUnavailable("timeout")
        return real(start, end)

    p.list_rundowns = flaky
    with pytest.raises(NewsroomUnavailable):
        service.fetch(p, TODAY, TODAY_PROGRAM)


def test_program_profile_default_and_api(client):
    r = client.post("/api/programs", json={"name": "뉴스투데이"})
    assert r.json()["dedupe_program"] == "뉴스데스크"
    assert client.post("/api/programs", json={"name": "뉴스데스크"}).json()["dedupe_program"] is None
    pid = r.json()["id"]
    r = client.put(f"/api/programs/{pid}", json={"name": "뉴스투데이", "dedupe_program": ""})
    assert r.json()["dedupe_program"] is None


def test_dev_fixture_morning_news_repeats_one_desk_item(client):
    from app.newsroom import factory

    factory.set_provider(FakeNewsroomProvider.from_dir(any_date=True))
    try:
        pid = client.post("/api/programs", json={"name": "뉴스투데이"}).json()["id"]
        body = client.get("/api/newsroom/rundowns", params={"date": "2026-11-20", "program_id": pid}).json()
        assert body["counts"]["aired"] == 1 and body["counts"]["uploads"] == 2
    finally:
        factory.set_provider(None)
