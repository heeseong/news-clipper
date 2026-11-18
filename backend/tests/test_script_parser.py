from app.newsroom.models import ItemType, Section, SentenceKind
from app.newsroom.rundown import build_item, classify, clean_title, estimate_seconds, parse_duration
from app.newsroom.script_parser import clean_spoken, parse_memo, parse_script

REPORT = """◀ 앵 커 ▶

새 다리가 오늘 개통했습니다.
교통 체증이 줄어들 전망입니다.

홍길동 기자가 보도합니다.

◀ END ▶
◀ 리포트 ▶

길이 1킬로미터의 다리가 문을 열었습니다.

반투명CG] 공사에는 5년이 걸렸습니다./

◀ INT ▶ 김철수/시민
"(이제) 출근길이 편해졌어요."

CG
통행량은 하루 2만 대로 예상됩니다.//

◀ st-up ▶
남은 과제는 주변 도로 정비입니다.
MBC뉴스 홍길동입니다.
◀ END ▶
"""


def kinds(script):
    return [(s.section.value, s.kind.value) for s in script.sentences]


def test_report_sections_and_kinds():
    sc = parse_script(REPORT)
    assert sc.structured
    assert sc.warnings == []
    assert sc.signoff_reporter == "홍길동"
    assert [s.text for s in sc.lead] == [
        "새 다리가 오늘 개통했습니다.",
        "교통 체증이 줄어들 전망입니다.",
        "홍길동 기자가 보도합니다.",
    ]
    assert sc.lead[-1].kind == SentenceKind.INTRO
    assert kinds(sc)[3:] == [
        ("report", "narration"),
        ("report", "narration"),
        ("report", "interview"),
        ("report", "narration"),
        ("report", "standup"),
        ("report", "signoff"),
    ]


def test_directives_and_annotations_removed():
    sc = parse_script(REPORT)
    texts = [s.text for s in sc.report]
    assert "공사에는 5년이 걸렸습니다." in texts  # 반투명CG] prefix and trailing / removed
    assert '"출근길이 편해졌어요."' in texts  # (이제) removed, no space after quote
    assert "통행량은 하루 2만 대로 예상됩니다." in texts  # bare CG line and // removed
    interview = next(s for s in sc.sentences if s.kind == SentenceKind.INTERVIEW)
    assert interview.speaker == "김철수 / 시민"


def test_lead_without_end_marker_and_inline_markers():
    content = (
        "◀ 앵커 ▶\n\n리드 문장입니다.\n\n이몽룡 기자가 취재했습니다.\n\n◀ 리포트 ▶\n\n"
        "본문입니다.\n◀ 현장음 ▶ 주민\n와 시원하다.\n\n"
        "통CG]◀ SYNC ▶ 성춘향 / 남원시장\n좋은 일입니다.\n\n"
        "S/U\n마무리 멘트입니다.\n\nmbc뉴스 이몽룡입니다.//"
    )
    sc = parse_script(content)
    assert sc.signoff_reporter == "이몽룡"
    assert kinds(sc) == [
        ("lead", "anchor"),
        ("lead", "intro"),
        ("report", "narration"),
        ("report", "ambient"),
        ("report", "sync"),
        ("report", "standup"),
        ("report", "signoff"),
    ]
    assert sc.sentences[3].speaker == "주민"
    assert sc.sentences[4].speaker == "성춘향 / 남원시장"


def test_speaker_title_on_next_line():
    content = "◀ 리포트 ▶\n\n◀ SYNC ▶ 백 아무개 /\n어느시 부시장\n예산을 반영하겠습니다.\n\nMBC 뉴스 홍길동 입니다."
    sc = parse_script(content)
    sync = [s for s in sc.sentences if s.kind == SentenceKind.SYNC]
    assert sync[0].speaker == "백 아무개 / 어느시 부시장"
    assert sync[0].text == "예산을 반영하겠습니다."
    assert sc.signoff_reporter == "홍길동"


def test_korean_interview_marker():
    sc = parse_script("◀ 리포트 ▶\n\n◀ 인터뷰 ▶ 어민\n많이 잡혀요.\n\nMBC뉴스 홍길동입니다.")
    assert sc.sentences[0].kind == SentenceKind.INTERVIEW
    assert not any(w.startswith("UNKNOWN_MARKER") for w in sc.warnings)


def test_speech_block_ends_with_paragraph():
    content = '◀ 리포트 ▶\n\n◀ INT ▶ 시민\n"좋아요."\n\n이어지는 내레이션입니다.\n\nMBC뉴스 홍길동입니다.'
    sc = parse_script(content)
    assert sc.sentences[1].kind == SentenceKind.NARRATION


def test_brief_is_unstructured():
    sc = parse_script("군이 축제를 엽니다.\n오늘(11) 개막했습니다.")
    assert not sc.structured
    assert [s.kind for s in sc.sentences] == [SentenceKind.ANCHOR, SentenceKind.ANCHOR]
    assert sc.sentences[1].text == "오늘 개막했습니다."
    assert all(s.section == Section.LEAD for s in sc.sentences)


def test_warnings():
    sc = parse_script("◀ 앵커 ▶\n리드입니다.\n◀ 리포트 ▶\n본문입니다.")
    assert "NO_SIGNOFF" in sc.warnings
    sc = parse_script("◀ 앵커 ▶\n리드입니다.\n◀ 뭔가 ▶\n더.")
    assert "NO_REPORT_SECTION" in sc.warnings
    assert any(w.startswith("UNKNOWN_MARKER") for w in sc.warnings)


def test_bracket_directives_removed():
    sc = parse_script("◀ 리포트 ▶\n\n[영상] 홈런\n예상을 뒤집은 건 장타력이었습니다.\n\nMBC뉴스 홍길동입니다.")
    assert [s.text for s in sc.sentences][0] == "예상을 뒤집은 건 장타력이었습니다."


def test_clean_spoken_keeps_number_slashes():
    assert clean_spoken("비율은 1/2입니다.//") == "비율은 1/2입니다."
    assert clean_spoken("*목포여자고등학교* 개교 (CG)") == "개교"


def test_memo_parsing():
    memo = (
        "상단 : 기존 부지에 컨벤션이?\n\n썸네일\n가칭 통합목포고 2028년 개교\n기존 부지에는?\n\n"
        "통CG]\n\n영상취재 고재필\nCG 이희정\n\n좌상단: 무시됨(이미 상단 있음)\n\n편집 요청 메모"
    )
    notes = parse_memo(memo)
    assert notes.top_caption == "기존 부지에 컨벤션이?"
    assert notes.thumbnail == "가칭 통합목포고 2028년 개교\n기존 부지에는?"
    assert notes.credits == ["영상취재 고재필", "CG 이희정"]
    assert notes.other == ["편집 요청 메모"]

    assert parse_memo("썸네일 : 의대냐, 분원이냐").thumbnail == "의대냐, 분원이냐"


def test_clean_title():
    t = clean_title("기획4]국립의대와 대학병원 분원..무엇이 다른가(R)")
    assert (t.clean, t.series) == ("국립의대와 대학병원 분원..무엇이 다른가", "기획4")
    assert clean_title('R] "주사무소 전남 줄게" 말뿐?').clean == '"주사무소 전남 줄게" 말뿐?'
    assert clean_title("R]20대 용접공 사망 2주").clean == "20대 용접공 사망 2주"
    assert clean_title("새 다리 개통").series is None


def test_classify_priority():
    report = parse_script(REPORT)
    brief = parse_script("단신 문장입니다.")
    assert classify("리포트", None, brief)[0] == ItemType.REPORT  # remark wins
    assert classify(None, None, report)[0] == ItemType.REPORT
    assert classify("어깨걸이", None, brief)[0] == ItemType.BRIEF
    assert classify("타이틀+주요뉴스", None, None)[0] == ItemType.OTHER


def test_build_item_from_api_shape():
    raw = {
        "cueSheetItemTitle": "새 다리 개통(R)",
        "cueSheetItemContent": None,
        "cueSheetItemOrderNumber": "3",
        "article": {"articleTitle": "새 다리 개통(R)", "articleContent": REPORT, "memo": None, "reporterName": "김갑동"},
        "remark": "리포트",
    }
    item = build_item(raw, parse_duration("02:10"))
    assert item.order == 3 and item.item_type == ItemType.REPORT
    assert item.title.clean == "새 다리 개통"
    assert item.planned_seconds == 130
    assert item.estimated_seconds and item.estimated_seconds > 0
    assert any(w.startswith("SIGNOFF_REPORTER_MISMATCH") for w in item.script.warnings)


def test_empty_script_is_other():
    raw = {"cueSheetItemOrderNumber": 1, "article": {"articleTitle": "타이틀", "articleContent": "\n\n\n"}}
    item = build_item(raw)
    assert item.item_type == ItemType.OTHER and item.script is None
    assert estimate_seconds(None) is None


def test_parse_duration():
    assert parse_duration("02:48") == 168
    assert parse_duration("1:02:03") == 3723
    assert parse_duration(90) == 90
    assert parse_duration("") is None


def test_other_station_report_is_not_uploaded():
    base = {
        "cueSheetItemOrderNumber": "6",
        "article": {"articleContent": REPORT, "reporterName": "김갑동"},
        "remark": "리포트",
    }
    own = build_item({**base, "article": {**base["article"], "articleTitle": "새 다리 개통"}})
    assert own.upload and own.source is None

    by_title = build_item({**base, "article": {**base["article"], "articleTitle": "광주)기대 뒤집은 KIA"}})
    assert by_title.title.clean == "기대 뒤집은 KIA" and by_title.title.station == "광주"
    assert by_title.item_type == ItemType.REPORT and not by_title.upload and by_title.source == "광주"

    by_reporter = build_item(
        {**base, "article": {**base["article"], "articleTitle": "제목", "reporterName": "이재원(광주)"}}
    )
    assert by_reporter.reporter == "이재원" and not by_reporter.upload

    own_dept = build_item({**base, "article": {**base["article"], "articleTitle": "제목"}}, department="보도부")
    assert own_dept.upload

    by_dept = build_item({**base, "article": {**base["article"], "articleTitle": "제목"}}, department="광주")
    assert not by_dept.upload


def test_brief_is_never_uploaded():
    raw = {"cueSheetItemOrderNumber": "7", "article": {"articleTitle": "단신", "articleContent": "단신입니다."}}
    assert not build_item(raw).upload
