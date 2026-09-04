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


def analyze(company: str, position: str = "") -> list[str]:
    """공고에서 뽑아낸 복지 라벨 목록.

    예전 버전에는 회사명 해시로 만든 가짜 '잡플래닛 추정 평점'이 함께 있었지만,
    실제 평판과 무관한 값이라 제거했다. 회사 규모·보수는 국민연금 실데이터를
    쓰는 ``jobhelper.company_info`` 를 참고한다.
    """
    return extract_welfares(company, position)
