"""회사 분류, 복지 키워드 추출, 참고용 사내평판 추정치 계산."""

from __future__ import annotations

import re

from .config import (
    FOREIGN_CORP_KEYWORDS,
    LARGE_CORP_KEYWORDS,
    MID_CORP_KEYWORDS,
    WELFARE_PATTERNS,
)


def classify_company(company: str, position: str = "") -> str:
    """회사명/공고명을 보고 대기업·중견·외국계·일반 중 하나로 분류한다."""
    haystack = f"{company} {position}"
    lowered = haystack.lower()

    if any(k in company for k in LARGE_CORP_KEYWORDS):
        return "대기업"
    if any(k.lower() in lowered for k in FOREIGN_CORP_KEYWORDS):
        return "외국계"
    if "중견" in haystack or any(k in haystack for k in MID_CORP_KEYWORDS):
        return "중견기업"
    return "일반/기타기업"


def extract_welfares(*texts: str) -> list[str]:
    """공고 텍스트에서 눈에 띄는 복지 키워드를 뽑아 라벨 목록으로 돌려준다."""
    haystack = " ".join(t for t in texts if t)
    found: list[str] = []
    for pattern, label in WELFARE_PATTERNS:
        if re.search(pattern, haystack) and label not in found:
            found.append(label)
    return found


def estimate_rating(company: str) -> float:
    """회사 규모대별 참고용 점수(1.0~5.0).

    실제 잡플래닛 평점이 아니라, 회사명 해시를 이용해 규모대별 밴드 안에서
    일정하게 재현되는 **추정치**다. 정렬/필터링의 보조 지표로만 쓴다.
    """
    if not company:
        return 3.0
    hash_val = sum(ord(ch) for ch in company)
    tier = classify_company(company)
    if tier == "대기업":
        base, span = 3.6, 5
    elif tier == "외국계":
        base, span = 3.4, 6
    elif tier == "중견기업":
        base, span = 2.8, 6
    else:
        base, span = 2.3, 7
    return round(min(5.0, base + (hash_val % span) * 0.1), 1)


def analyze(company: str, position: str = "") -> tuple[list[str], float]:
    """복지 라벨과 추정 평점을 한 번에 반환한다."""
    return extract_welfares(company, position), estimate_rating(company)
