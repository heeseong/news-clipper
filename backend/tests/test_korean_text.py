import pytest

from app.align.korean_text import native, sino, to_spoken_hangul


@pytest.mark.parametrize(
    "n,expected",
    [(0, "영"), (1, "일"), (10, "십"), (11, "십일"), (100, "백"), (2029, "이천이십구"),
     (10000, "만"), (12345, "만이천삼백사십오"), (163_0000_0000, "백육십삼억"),
     (22_0163_0000_0000, "이십이조백육십삼억"), (110000, "십일만")],
)
def test_sino(n, expected):
    assert sino(n) == expected


def test_native():
    assert [native(n) for n in (1, 2, 3, 8, 11, 20, 21, 34, 100)] == [
        "한", "두", "세", "여덟", "열한", "스무", "스물한", "서른네", None]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("22조 163억 원", "이십이조 백육십삼억 원"),
        ("8곳 중 6곳이", "여덟곳 중 여섯곳이"),
        ("올해보다 5.8%", "올해보다 오점팔퍼센트"),
        ("2,587명에서", "이천오백팔십칠명에서"),
        ("60명분 지원", "육십명분 지원"),
        ("MBC뉴스", "엠비씨뉴스"),
        ("AI 캡틴", "에이아이 캡틴"),
        ("2028년 3월 1일", "이천이십팔년 삼월 일일"),
        ("서울~해남", "서울에서해남"),
        ("\"좋아요.\" 그렇죠?", "좋아요 그렇죠"),
        ("KIA 타이거즈", "기아 타이거즈"),
        ("KIAS", "케이아이에이에스"),
    ],
)
def test_to_spoken_hangul(text, expected):
    assert to_spoken_hangul(text) == expected
