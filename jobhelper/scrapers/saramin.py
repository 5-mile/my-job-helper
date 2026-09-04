"""사람인 검색 결과 수집기."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..classify import analyze, classify_company
from ..config import REQUEST_TIMEOUT, USER_AGENT
from ..dates import format_dday, parse_deadline

log = logging.getLogger(__name__)

SEARCH_URL = (
    "https://www.saramin.co.kr/zf_user/search/recruit"
    "?searchword={keyword}&sort={sort}&recruitPage={page}&Page={page}"
)

_REC_IDX = re.compile(r"rec_idx=(\d+)")


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"https://www.saramin.co.kr{href}"


def _text(node, default: str = "") -> str:
    return node.get_text(strip=True) if node else default


def _parse_item(item) -> dict[str, Any] | None:
    corp_tag = item.select_one(".area_corp .corp_name a")
    title_tag = item.select_one(".area_job .job_tit a")
    if not (corp_tag and title_tag):
        return None

    company = _text(corp_tag)
    position = title_tag.get("title") or _text(title_tag)
    href = title_tag.get("href", "")
    link = _absolute(href)

    # 조건 span은 [지역, 경력, 학력, 고용형태] 순서로 들어온다.
    conditions = [_text(s) for s in item.select(".area_job .job_condition span")]
    location = conditions[0] if len(conditions) > 0 else "전국"
    career = conditions[1] if len(conditions) > 1 else ""
    education = conditions[2] if len(conditions) > 2 else ""
    employment = conditions[3] if len(conditions) > 3 else ""

    raw_date = _text(item.select_one(".area_job .date"), "상세 확인")
    deadline = parse_deadline(raw_date)
    sector = _text(item.select_one(".job_sector"))

    welfares, rating = analyze(company, f"{position} {sector} {employment}")
    rec_idx = _REC_IDX.search(href)

    return {
        "source": "사람인",
        "job_key": f"saramin:{rec_idx.group(1)}" if rec_idx else f"saramin:{company}:{position}",
        "company": company,
        "position": position,
        "link": link,
        "location": location,
        "career": career,
        "education": education,
        "employment": employment,
        "sector": sector,
        "raw_date": raw_date,
        "deadline": deadline.isoformat() if deadline else "",
        "date": format_dday(deadline, raw_date),
        "category": classify_company(company, f"{position} {sector}"),
        "welfares": welfares,
        "rating": rating,
    }


def _fetch_page(keyword: str, sort_code: str, page: int) -> list[dict[str, Any]]:
    url = SEARCH_URL.format(keyword=quote(keyword), sort=sort_code, page=page)
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("사람인 %d페이지 요청 실패: %s", page, exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    jobs = []
    for item in soup.select(".item_recruit"):
        try:
            parsed = _parse_item(item)
        except Exception as exc:  # 마크업 변경 시 한 건만 건너뛴다
            log.warning("공고 파싱 실패: %s", exc)
            continue
        if parsed:
            jobs.append(parsed)
    return jobs


def fetch_saramin_jobs(
    keywords: list[str] | str,
    sort_code: str = "rc",
    pages: int = 3,
    exclude: list[str] | None = None,
) -> list[dict[str, Any]]:
    """여러 검색어를 병렬로 긁어 중복을 제거한 공고 리스트를 돌려준다."""
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        return []

    tasks = [(kw, page) for kw in keywords for page in range(1, max(1, pages) + 1)]
    collected: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
        for result in executor.map(lambda t: _fetch_page(t[0], sort_code, t[1]), tasks):
            collected.extend(result)

    exclude_terms = [e.strip().lower() for e in (exclude or []) if e.strip()]
    unique: dict[str, dict[str, Any]] = {}
    for job in collected:
        haystack = f"{job['company']} {job['position']} {job['sector']}".lower()
        if any(term in haystack for term in exclude_terms):
            continue
        unique.setdefault(job["job_key"], job)
    return list(unique.values())


def group_by_category(jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    from ..config import CATEGORIES

    grouped: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
    for job in jobs:
        grouped.setdefault(job.get("category", "일반/기타기업"), []).append(job)
    return grouped
