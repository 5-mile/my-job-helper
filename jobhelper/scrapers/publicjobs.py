"""공공기관 채용정보(잡알리오) 수집기.

출처: 공공데이터포털 「인사혁신처_공공기관 채용정보」
      https://apis.data.go.kr/1051000/recruitment/list

공기업·공공기관 공고만 모여 있어, 그쪽을 노린다면 사람인/워크넷과 겹치지 않는
공고가 많이 잡힌다. 인증키는 공공데이터포털에서 발급받아 PUBLIC_JOBS_KEY 로 넣는다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from .. import settings
from ..classify import analyze
from ..config import REQUEST_TIMEOUT
from ..dates import format_dday, parse_deadline

log = logging.getLogger(__name__)

API_URL = "https://apis.data.go.kr/1051000/recruitment/list"
MAX_ROWS = 100

# 공공기관은 규모가 제각각이라 사람인식 대기업/중견 분류가 맞지 않는다.
CATEGORY = "공공기관"


def _first(item: dict[str, Any], *names: str) -> str:
    """응답 필드명이 조금씩 다를 수 있어 여러 후보를 순서대로 찾는다."""
    for name in names:
        value = item.get(name)
        if value not in (None, "", []):
            if isinstance(value, list):
                return ", ".join(str(v) for v in value if v)
            return str(value).strip()
    return ""


def _parse_date(value: str) -> str:
    """`20260920` 또는 `2026-09-20` 을 ISO 로."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return value.strip()


def _parse_item(item: dict[str, Any]) -> dict[str, Any] | None:
    company = _first(item, "instNm", "pblntInstNm", "instNn")
    title = _first(item, "recrutPbancTtl", "pbancTtl", "recrutPblntTtl")
    if not (company and title):
        return None

    raw_end = _parse_date(_first(item, "pbancEndYmd", "pbancEndDe", "endYmd"))
    deadline = parse_deadline(raw_end)

    location = _first(item, "workRgnNmLst", "workRgnLst")
    employment = _first(item, "hireTypeNmLst", "hireTypeLst")
    education = _first(item, "acbgCondNmLst", "acbgCondLst")
    sector = _first(item, "ncsCdNmLst", "ncsCdLst")
    recruit_type = _first(item, "recrutSeNm", "recrutSe")
    headcount = _first(item, "recrutNope")
    serial = _first(item, "recrutPblntSn", "pblntSn")

    link = _first(item, "srcUrl")
    if not link and serial:
        link = f"https://job.alio.go.kr/orgrecruit.do?recrutPblntSn={serial}"

    detail = " ".join(x for x in (sector, recruit_type, _first(item, "prefCondCn")) if x)
    welfares = analyze(company, f"{title} {detail}")

    return {
        "source": "공공기관",
        "job_key": f"alio:{serial}" if serial else f"alio:{company}:{title}",
        "company": company,
        "position": title,
        "link": link,
        "location": location or "전국",
        "career": recruit_type,
        "education": education,
        "employment": employment,
        "sector": sector,
        "salary": "",
        "headcount": headcount,
        "raw_date": raw_end,
        "deadline": deadline.isoformat() if deadline else "",
        "date": format_dday(deadline, raw_end),
        "category": CATEGORY,
        "welfares": welfares,
    }


def fetch_public_jobs(
    keywords: list[str] | str | None = None,
    pages: int = 2,
    rows: int = MAX_ROWS,
    exclude: list[str] | None = None,
    ongoing_only: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """공공기관 채용공고를 수집한다.

    이 API는 키워드 검색이 약해서, 진행 중인 공고를 받아온 뒤 클라이언트에서
    검색어로 거른다. 검색어를 주지 않으면 전부 돌려준다.
    """
    key = settings.public_jobs_key()
    if not key:
        return [], ""

    if isinstance(keywords, str):
        keywords = [keywords]
    terms = [k.strip().lower() for k in (keywords or []) if k.strip()]

    rows = max(1, min(rows, MAX_ROWS))
    collected: list[dict[str, Any]] = []
    warning = ""

    for page in range(1, max(1, pages) + 1):
        params: dict[str, Any] = {
            "serviceKey": key,
            "pageNo": page,
            "numOfRows": rows,
            "resultType": "json",
        }
        if ongoing_only:
            params["ongoingYn"] = "Y"

        try:
            response = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            log.warning("공공기관 채용정보 요청 실패 (%d쪽): %s", page, exc)
            warning = warning or f"공공기관 채용정보 요청에 실패했습니다: {exc}"
            break

        # 인증키 오류는 XML로 오기도 하고 JSON 안에 메시지로 오기도 한다.
        if isinstance(payload, dict):
            code = str(payload.get("resultCode", "")).strip()
            if code and code not in ("0", "00", "200"):
                message = str(payload.get("resultMsg") or payload.get("resultMessage") or code)
                log.warning("공공기관 채용정보 응답: %s", message)
                return [], f"공공기관 채용정보: {message}"

        items = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(items, dict):
            items = items.get("item") or items.get("list") or []
        if not items:
            break

        before = len(collected)
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                parsed = _parse_item(item)
            except Exception as exc:
                log.warning("공공기관 공고 파싱 실패: %s", exc)
                continue
            if parsed:
                collected.append(parsed)

        if len(collected) == before and items:
            log.error(
                "공공기관 채용정보 응답을 해석하지 못했습니다. 첫 항목: %s",
                json.dumps(items[0], ensure_ascii=False)[:800],
            )
            warning = "공공기관 채용정보 응답 형태가 예상과 다릅니다. 로그를 확인해 주세요."

        if len(items) < rows:
            break

    if terms:
        collected = [
            job
            for job in collected
            if any(
                term in f"{job['company']} {job['position']} {job['sector']}".lower()
                for term in terms
            )
        ]

    exclude_terms = [e.strip().lower() for e in (exclude or []) if e.strip()]
    unique: dict[str, dict[str, Any]] = {}
    for job in collected:
        haystack = f"{job['company']} {job['position']} {job['sector']}".lower()
        if any(term in haystack for term in exclude_terms):
            continue
        unique.setdefault(job["job_key"], job)
    return list(unique.values()), warning
