"""공고 마감일 파싱과 D-Day 계산."""

from __future__ import annotations

import re
from datetime import date, datetime

MONTHS_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# "~ 09/14(월)", "~09/14", "2026/09/14" 등을 잡아낸다.
_MMDD = re.compile(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})")
_YYYYMMDD = re.compile(r"(20\d{2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})")
# "9월 18일까지" 같은 한국어 표기
_KOREAN_MMDD = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")

ALWAYS_OPEN_HINTS = ("상시", "채용시", "수시", "미정")


def parse_deadline(text: str, today: date | None = None) -> date | None:
    """공고의 마감일 문자열에서 날짜를 추출한다. 없으면 None.

    연도가 빠진 ``09/14`` 형태는 오늘 기준으로 가장 가까운 미래 날짜로 본다.
    """
    if not text:
        return None
    today = today or date.today()

    m = _YYYYMMDD.search(text)
    if m:
        year, month, day = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    if any(hint in text for hint in ALWAYS_OPEN_HINTS):
        return None

    m = _KOREAN_MMDD.search(text) or _MMDD.search(text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        # 지난 달 날짜라면 내년 공고로 해석한다 (연말/연초 경계 보정).
        if (candidate - today).days >= -14:
            return candidate
    return None


def days_left(deadline: date | None, today: date | None = None) -> int | None:
    """마감까지 남은 일수. 마감일이 없으면 None."""
    if deadline is None:
        return None
    return (deadline - (today or date.today())).days


def format_dday(deadline: date | None, raw_text: str = "", today: date | None = None) -> str:
    """카드에 표시할 D-Day 문자열을 만든다."""
    left = days_left(deadline, today)
    if left is None:
        if any(hint in (raw_text or "") for hint in ALWAYS_OPEN_HINTS):
            return "🔁 상시채용"
        return "⏳ 상세 확인"
    if left < 0:
        return "⛔ 마감"
    if left == 0:
        return "🔥 D-DAY"
    return f"⏳ D-{left}"


def parse_rss_date(pub_date: str) -> date | None:
    """RSS ``pubDate`` (RFC 822)에서 날짜만 뽑아낸다."""
    if not pub_date:
        return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", pub_date)
    if not m:
        return None
    day, month_eng, year = int(m.group(1)), m.group(2), int(m.group(3))
    month = MONTHS_MAP.get(month_eng)
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def humanize(day: date | None) -> str:
    """``6월 14일`` 형태의 짧은 한국어 날짜."""
    if day is None:
        return "최신"
    return f"{day.month}월 {day.day}일"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
