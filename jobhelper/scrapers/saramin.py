"""사람인 검색 결과 수집기.

사람인은 공식 API가 아니라 HTML을 읽기 때문에 마크업이 바뀌면 조용히 0건이
될 수 있다. 그래서 (1) 셀렉터를 여러 개 두고 순서대로 시도하고,
(2) 수집 결과를 진단 정보로 함께 돌려주어 UI가 고장을 표시할 수 있게 한다.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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

# 마크업이 바뀌어도 버티도록 앞에서부터 차례로 시도한다.
ITEM_SELECTORS = [".item_recruit", "[class*=item_recruit]", ".list_item", ".box_item"]
CORP_SELECTORS = [".area_corp .corp_name a", ".corp_name a", "[class*=corp_name] a"]
TITLE_SELECTORS = [".area_job .job_tit a", ".job_tit a", "[class*=job_tit] a"]
CONDITION_SELECTORS = [".area_job .job_condition span", ".job_condition span"]
DATE_SELECTORS = [".area_job .date", ".job_date .date", ".date"]

_REC_IDX = re.compile(r"rec_idx=(\d+)")


@dataclass
class FetchDiagnostics:
    """수집이 정상이었는지 UI에 알리기 위한 진단 정보."""

    pages_requested: int = 0
    pages_failed: int = 0
    pages_empty: int = 0
    items_found: int = 0
    selector_used: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.items_found > 0 and self.pages_failed < self.pages_requested

    @property
    def warning(self) -> str:
        """문제가 있을 때 사용자에게 보여줄 한 줄. 정상이면 빈 문자열."""
        if self.pages_requested == 0:
            return ""
        if self.pages_failed == self.pages_requested:
            return "사람인 서버에 연결하지 못했습니다. 네트워크를 확인해 주세요."
        if self.items_found == 0:
            return (
                "사람인에서 공고를 한 건도 읽지 못했습니다. "
                "검색어에 결과가 없거나, 사람인 페이지 구조가 바뀌었을 수 있습니다."
            )
        if self.pages_failed:
            return f"{self.pages_requested}개 요청 중 {self.pages_failed}개가 실패해 일부 공고가 빠졌습니다."
        return ""


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"https://www.saramin.co.kr{href}"


def _text(node, default: str = "") -> str:
    return node.get_text(strip=True) if node else default


def _select_first(node, selectors: list[str]):
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            return found
    return None


def _select_items(soup) -> tuple[list, str]:
    """여러 셀렉터를 시도해 공고 목록과 실제로 쓰인 셀렉터를 돌려준다."""
    for selector in ITEM_SELECTORS:
        items = soup.select(selector)
        if items:
            return items, selector
    return [], ""


def _parse_item(item) -> dict[str, Any] | None:
    corp_tag = _select_first(item, CORP_SELECTORS)
    title_tag = _select_first(item, TITLE_SELECTORS)
    if not (corp_tag and title_tag):
        return None

    company = _text(corp_tag)
    position = title_tag.get("title") or _text(title_tag)
    href = title_tag.get("href", "")
    link = _absolute(href)

    # 조건 span은 [지역, 경력, 학력, 고용형태] 순서로 들어온다.
    conditions: list[str] = []
    for selector in CONDITION_SELECTORS:
        found = item.select(selector)
        if found:
            conditions = [_text(s) for s in found]
            break

    location = conditions[0] if len(conditions) > 0 else "전국"
    career = conditions[1] if len(conditions) > 1 else ""
    education = conditions[2] if len(conditions) > 2 else ""
    employment = conditions[3] if len(conditions) > 3 else ""

    raw_date = _text(_select_first(item, DATE_SELECTORS), "상세 확인")
    deadline = parse_deadline(raw_date)
    sector = _text(item.select_one(".job_sector"))

    welfares = analyze(company, f"{position} {sector} {employment}")
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
        "salary": "",
        "raw_date": raw_date,
        "deadline": deadline.isoformat() if deadline else "",
        "date": format_dday(deadline, raw_date),
        "category": classify_company(company, f"{position} {sector}"),
        "welfares": welfares,
    }


def _fetch_page(keyword: str, sort_code: str, page: int) -> tuple[list[dict[str, Any]], str, str]:
    """한 페이지를 읽어 (공고들, 사용한 셀렉터, 오류메시지)를 돌려준다."""
    url = SEARCH_URL.format(keyword=quote(keyword), sort=sort_code, page=page)
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("사람인 '%s' %d페이지 요청 실패: %s", keyword, page, exc)
        return [], "", f"{keyword} {page}쪽: {exc}"

    soup = BeautifulSoup(response.text, "html.parser")
    items, selector = _select_items(soup)
    if not items:
        log.warning("사람인 '%s' %d페이지에서 공고 요소를 찾지 못했습니다.", keyword, page)
        return [], "", ""

    jobs = []
    for item in items:
        try:
            parsed = _parse_item(item)
        except Exception as exc:  # 마크업 변경 시 한 건만 건너뛴다
            log.warning("공고 파싱 실패: %s", exc)
            continue
        if parsed:
            jobs.append(parsed)
    return jobs, selector, ""


def fetch_saramin_jobs_detailed(
    keywords: list[str] | str,
    sort_code: str = "rc",
    pages: int = 3,
    exclude: list[str] | None = None,
) -> tuple[list[dict[str, Any]], FetchDiagnostics]:
    """공고 목록과 수집 진단 정보를 함께 돌려준다."""
    diagnostics = FetchDiagnostics()

    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        return [], diagnostics

    tasks = [(kw, page) for kw in keywords for page in range(1, max(1, pages) + 1)]
    diagnostics.pages_requested = len(tasks)

    collected: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as executor:
        results = executor.map(lambda t: _fetch_page(t[0], sort_code, t[1]), tasks)
        for jobs, selector, error in results:
            if error:
                diagnostics.pages_failed += 1
                diagnostics.errors.append(error)
            elif not jobs:
                diagnostics.pages_empty += 1
            if selector and not diagnostics.selector_used:
                diagnostics.selector_used = selector
            collected.extend(jobs)

    exclude_terms = [e.strip().lower() for e in (exclude or []) if e.strip()]
    unique: dict[str, dict[str, Any]] = {}
    for job in collected:
        haystack = f"{job['company']} {job['position']} {job['sector']}".lower()
        if any(term in haystack for term in exclude_terms):
            continue
        unique.setdefault(job["job_key"], job)

    diagnostics.items_found = len(unique)
    return list(unique.values()), diagnostics


def fetch_saramin_jobs(
    keywords: list[str] | str,
    sort_code: str = "rc",
    pages: int = 3,
    exclude: list[str] | None = None,
) -> list[dict[str, Any]]:
    """여러 검색어를 병렬로 긁어 중복을 제거한 공고 리스트를 돌려준다."""
    jobs, _ = fetch_saramin_jobs_detailed(keywords, sort_code, pages, exclude)
    return jobs


def group_by_category(jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    from ..config import CATEGORIES

    grouped: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
    for job in jobs:
        grouped.setdefault(job.get("category", "일반/기타기업"), []).append(job)
    return grouped
