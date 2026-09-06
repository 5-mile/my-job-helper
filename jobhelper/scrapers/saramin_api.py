"""사람인 공식 오픈API 수집기.

HTML 스크래핑(`saramin.py`)과 같은 사이트지만 이쪽은 공식 API라
페이지 구조 변경에 영향을 받지 않고, 지역·경력·학력 필터를 서버에서 걸 수 있다.
인증키는 https://oapi.saramin.co.kr 에서 발급받아 SARAMIN_API_KEY 로 넣는다.

응답 필드명이 문서와 다를 수 있어, 모든 접근을 방어적으로 한다.
파싱이 0건이면 원본 첫 항목을 로그에 남겨 원인을 추적할 수 있게 한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from .. import settings
from ..classify import analyze, classify_company
from ..config import REQUEST_TIMEOUT
from ..dates import format_dday, parse_deadline

log = logging.getLogger(__name__)

API_URL = "https://oapi.saramin.co.kr/job-search"
MAX_COUNT = 110  # API 1회 최대 건수


def _dig(data: Any, *path: str, default: Any = None) -> Any:
    """중첩 딕셔너리를 안전하게 판다. 중간에 없으면 default."""
    current = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _name_of(node: Any) -> str:
    """{'code':..,'name':..} 또는 문자열 모두 받아 이름만 뽑는다."""
    if isinstance(node, dict):
        return str(node.get("name") or node.get("code") or "").strip()
    if node is None:
        return ""
    return str(node).strip()


def _epoch_to_date(value: Any) -> str:
    """유닉스 타임스탬프(초)를 YYYY-MM-DD 로. 실패하면 빈 문자열."""
    try:
        from datetime import datetime

        return datetime.fromtimestamp(int(value)).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _parse_job(item: dict[str, Any]) -> dict[str, Any] | None:
    company = _name_of(_dig(item, "company", "detail", "name")) or _name_of(
        _dig(item, "company", "name")
    )
    position_node = item.get("position") or {}
    title = str(position_node.get("title") or "").strip()
    if not (company and title):
        return None

    location = _name_of(position_node.get("location"))
    industry = _name_of(position_node.get("industry"))
    job_category = _name_of(position_node.get("job-mid-code")) or _name_of(
        position_node.get("job-code")
    )
    experience = _name_of(position_node.get("experience-level"))
    education = _name_of(position_node.get("required-education-level"))
    employment = _name_of(position_node.get("job-type"))
    salary = _name_of(item.get("salary"))

    # 마감일: expiration-date(문자열) 우선, 없으면 expiration-timestamp
    raw_deadline = str(item.get("expiration-date") or "").strip()
    deadline = parse_deadline(raw_deadline)
    if deadline is None and item.get("expiration-timestamp"):
        deadline = parse_deadline(_epoch_to_date(item["expiration-timestamp"]))

    sector = ", ".join(x for x in (industry, job_category) if x)
    welfares = analyze(company, f"{title} {sector} {employment}")
    job_id = str(item.get("id") or "").strip()
    link = str(item.get("url") or "").strip()

    return {
        "source": "사람인API",
        "job_key": f"saramin:{job_id}" if job_id else f"saramin:{company}:{title}",
        "company": company,
        "position": title,
        "link": link,
        "location": location or "전국",
        "career": experience,
        "education": education,
        "employment": employment,
        "sector": sector,
        "salary": salary,
        "raw_date": raw_deadline,
        "deadline": deadline.isoformat() if deadline else "",
        "date": format_dday(deadline, raw_deadline),
        "category": classify_company(company, f"{title} {sector}"),
        "welfares": welfares,
    }


def fetch_saramin_api_jobs(
    keywords: list[str] | str,
    pages: int = 3,
    count: int = MAX_COUNT,
    exclude: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """공고 목록과 (문제가 있을 때의) 경고 문구를 돌려준다."""
    key = settings.saramin_api_key()
    if not key:
        return [], ""

    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        return [], ""

    count = max(1, min(count, MAX_COUNT))
    collected: list[dict[str, Any]] = []
    warning = ""
    raw_seen = False

    for keyword in keywords:
        for page in range(max(1, pages)):
            try:
                response = requests.get(
                    API_URL,
                    params={
                        "access-key": key,
                        "keywords": keyword,
                        "start": page,
                        "count": count,
                        "sr": "directhire",
                    },
                    headers={"Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, json.JSONDecodeError) as exc:
                log.warning("사람인 API 요청 실패 (%s, %d쪽): %s", keyword, page, exc)
                warning = warning or f"사람인 API 요청에 실패했습니다: {exc}"
                break

            # 인증 실패 등은 code/message 로 온다
            if isinstance(payload, dict) and payload.get("message") and "jobs" not in payload:
                message = str(payload.get("message"))
                log.warning("사람인 API 응답: %s", message)
                return [], f"사람인 API: {message}"

            items = _dig(payload, "jobs", "job", default=[]) or []
            if isinstance(items, dict):  # 1건이면 리스트가 아닐 수 있다
                items = [items]
            if not items:
                break

            if not raw_seen:
                raw_seen = True
                log.debug("사람인 API 첫 항목: %s", json.dumps(items[0], ensure_ascii=False)[:800])

            before = len(collected)
            for item in items:
                try:
                    parsed = _parse_job(item)
                except Exception as exc:
                    log.warning("사람인 API 공고 파싱 실패: %s", exc)
                    continue
                if parsed:
                    collected.append(parsed)

            if len(collected) == before:
                # 항목은 왔는데 하나도 못 읽었다면 응답 형태가 바뀐 것이다.
                log.error(
                    "사람인 API 응답을 해석하지 못했습니다. 첫 항목: %s",
                    json.dumps(items[0], ensure_ascii=False)[:800],
                )
                warning = "사람인 API 응답 형태가 예상과 다릅니다. 로그를 확인해 주세요."

            if len(items) < count:
                break

    exclude_terms = [e.strip().lower() for e in (exclude or []) if e.strip()]
    unique: dict[str, dict[str, Any]] = {}
    for job in collected:
        haystack = f"{job['company']} {job['position']} {job['sector']}".lower()
        if any(term in haystack for term in exclude_terms):
            continue
        unique.setdefault(job["job_key"], job)
    return list(unique.values()), warning
