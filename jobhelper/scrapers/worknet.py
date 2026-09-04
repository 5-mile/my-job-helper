"""워크넷(고용24) 채용정보 오픈API 수집기.

스크래핑이 아니라 공식 API라 HTML 구조 변경에 영향을 받지 않는다.
인증키는 https://openapi.work.go.kr 에서 발급받아 WORKNET_AUTH_KEY로 넣는다.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import requests

from .. import settings
from ..classify import analyze, classify_company
from ..config import REQUEST_TIMEOUT
from ..dates import format_dday, parse_deadline

log = logging.getLogger(__name__)

API_URL = "http://openapi.work.go.kr/opi/opi/opia/wantedApi.do"


def _text(node: ET.Element, *names: str) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _parse_wanted(node: ET.Element) -> dict[str, Any] | None:
    company = _text(node, "company")
    position = _text(node, "title")
    if not (company and position):
        return None

    close_raw = _text(node, "closeDt")
    deadline = parse_deadline(close_raw)
    region = _text(node, "region")
    sal_type = _text(node, "salTpNm")
    salary = _text(node, "sal")
    salary_label = f"{sal_type} {salary}".strip() if salary else ""

    welfares = analyze(company, f"{position} {salary_label}")
    auth_no = _text(node, "wantedAuthNo")

    return {
        "source": "워크넷",
        "job_key": f"worknet:{auth_no}" if auth_no else f"worknet:{company}:{position}",
        "company": company,
        "position": position,
        "link": _text(node, "wantedInfoUrl", "wantedMobileInfoUrl"),
        "location": region or "전국",
        "career": _text(node, "career"),
        "education": _text(node, "minEdubg"),
        "employment": _text(node, "empTpNm"),
        "sector": _text(node, "jobsNm", "jobsCd"),
        "salary": salary_label,
        "raw_date": close_raw,
        "deadline": deadline.isoformat() if deadline else "",
        "date": format_dday(deadline, close_raw),
        "category": classify_company(company, position),
        "welfares": welfares,
    }


def fetch_worknet_jobs(
    keywords: list[str] | str,
    display: int = 50,
    pages: int = 1,
    exclude: list[str] | None = None,
) -> list[dict[str, Any]]:
    """워크넷 채용공고를 검색한다. 인증키가 없으면 빈 리스트."""
    auth_key = settings.worknet_auth_key()
    if not auth_key:
        return []

    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        return []

    collected: list[dict[str, Any]] = []
    for keyword in keywords:
        for page in range(1, max(1, pages) + 1):
            try:
                response = requests.get(
                    API_URL,
                    params={
                        "authKey": auth_key,
                        "callTp": "L",
                        "returnType": "XML",
                        "startPage": page,
                        "display": min(display, 100),
                        "keyword": keyword,
                        "sortOrderBy": "DESC",
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
            except (requests.RequestException, ET.ParseError) as exc:
                log.warning("워크넷 API 요청 실패 (%s, %d쪽): %s", keyword, page, exc)
                continue

            message = root.find(".//message")
            if message is not None and root.find(".//wanted") is None:
                log.warning("워크넷 API 응답: %s", message.text)
                break

            for node in root.findall(".//wanted"):
                try:
                    parsed = _parse_wanted(node)
                except Exception as exc:
                    log.warning("워크넷 공고 파싱 실패: %s", exc)
                    continue
                if parsed:
                    collected.append(parsed)

    exclude_terms = [e.strip().lower() for e in (exclude or []) if e.strip()]
    unique: dict[str, dict[str, Any]] = {}
    for job in collected:
        haystack = f"{job['company']} {job['position']} {job['sector']}".lower()
        if any(term in haystack for term in exclude_terms):
            continue
        unique.setdefault(job["job_key"], job)
    return list(unique.values())
