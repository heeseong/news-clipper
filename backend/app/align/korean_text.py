"""Turn script sentences into Hangul-only text for acoustic alignment.

Korean CTC models emit Hangul syllables, so digits, symbols and Latin letters
must be spelled out the way an anchor reads them:

    22조 163억 원   -> 이십이조 백육십삼억 원
    8곳 중 6곳      -> 여덟곳 중 여섯곳     (native numbers before some counters)
    5.8%           -> 오점팔퍼센트
    MBC, AI        -> 엠비씨, 에이아이

This only needs to be close: alignment tolerates small differences.
"""

from __future__ import annotations

import re

_SINO_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ["", "십", "백", "천"]
_BIG_UNITS = ["", "만", "억", "조", "경"]

_NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
# Counters read with native numbers (1-99) in broadcast speech.
NATIVE_COUNTERS = ("개", "곳", "명", "가지", "군데", "마리", "대", "살", "시간", "번째", "번", "척", "채", "그루", "켤레")

_LETTERS = {
    "A": "에이", "B": "비", "C": "씨", "D": "디", "E": "이", "F": "에프", "G": "지", "H": "에이치",
    "I": "아이", "J": "제이", "K": "케이", "L": "엘", "M": "엠", "N": "엔", "O": "오", "P": "피",
    "Q": "큐", "R": "알", "S": "에스", "T": "티", "U": "유", "V": "브이", "W": "더블유", "X": "엑스",
    "Y": "와이", "Z": "지",
}
# Words read differently from their spelling. Extend per program as needed.
PRONUNCIATIONS = {
    "KIA": "기아",
    "KTX": "케이티엑스",
    "SRT": "에스알티",
    "FA": "에프에이",
}

_SYMBOLS = {"%": "퍼센트", "~": "에서", "→": "에서", "℃": "도", "㎡": "제곱미터", "㎞": "킬로미터", "&": "앤"}


def sino(n: int) -> str:
    """Sino-Korean reading: 1 -> 일, 10 -> 십, 12345 -> 만이천삼백사십오."""
    if n == 0:
        return "영"
    parts: list[str] = []
    group_idx = 0
    while n > 0:
        group = n % 10000
        n //= 10000
        if group:
            s = ""
            for pos in range(3, -1, -1):
                d = (group // 10**pos) % 10
                if d == 0:
                    continue
                digit = "" if (d == 1 and pos > 0) else _SINO_DIGITS[d]
                s += digit + _SMALL_UNITS[pos]
            if group == 1 and group_idx == 1:  # 10000 -> 만, not 일만
                s = ""
            parts.append(s + _BIG_UNITS[group_idx])
        group_idx += 1
    return "".join(reversed(parts))


def native(n: int) -> str | None:
    """Native Korean counting form (한, 두, 스무 …) for 1-99, else None."""
    if not 1 <= n <= 99:
        return None
    tens, ones = divmod(n, 10)
    if n == 20:
        return "스무"
    return _NATIVE_TENS[tens] + _NATIVE_ONES[ones]


_NUM_RE = re.compile(r"(\d[\d,]*)(?:\.(\d+))?")


def _read_number(m: re.Match, following: str) -> str:
    int_part = m.group(1).replace(",", "")
    frac = m.group(2)
    n = int(int_part)
    if frac is None and following.startswith(NATIVE_COUNTERS) and not following.startswith(("개월", "명분")):
        nat = native(n)
        if nat:
            return nat
    text = sino(n)
    if frac:
        text += "점" + "".join(_SINO_DIGITS[int(d)] for d in frac)
    return text


def spell_numbers(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _NUM_RE.finditer(text):
        out.append(text[pos : m.start()])
        out.append(_read_number(m, text[m.end() :].lstrip()))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def spell_latin(text: str) -> str:
    return re.sub(r"[A-Za-z]+", lambda m: "".join(_LETTERS[c] for c in m.group(0).upper()), text)


def to_spoken_hangul(text: str) -> str:
    """Hangul syllables and single spaces only."""
    for word, reading in PRONUNCIATIONS.items():
        text = re.sub(rf"(?<![A-Za-z]){word}(?![A-Za-z])", reading, text)
    for sym, word in _SYMBOLS.items():
        text = text.replace(sym, word)
    text = spell_numbers(text)
    text = spell_latin(text)
    text = re.sub(r"[^가-힣\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
