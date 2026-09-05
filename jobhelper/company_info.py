"""국민연금 사업장 데이터로 회사의 실제 규모와 보수 수준을 추정한다.

출처: 공공데이터포털 「국민연금공단_국민연금 가입 사업장 내역」 (NpsBplcInfoInqireServiceV2)

가짜 해시 별점을 대신하는 **검증 가능한 숫자**를 제공한다.
- 직원 수  = 국민연금 가입자 수 (jnngpCnt)
- 월평균보수 = 당월 고지 금액 / 가입자 수 / 0.09 (연금보험료율 9%)

두 값 모두 한계가 있어 UI와 함께 명시한다. 아래 CAVEAT 참고.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from typing import Any

import requests

from . import settings
from .config import REQUEST_TIMEOUT
from .storage import connect, upsert
from .dates import now_iso

log = logging.getLogger(__name__)

BASE_URL = "http://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2/getBassInfoSearchV2"

# 국민연금 기준소득월액 상한액(2025년 기준 월 637만원). 이 이상 버는 직원은
# 상한에서 잘리므로, 고연봉 회사일수록 추정 보수가 실제보다 낮게 나온다.
PENSION_CEILING = 6_370_000
PENSION_RATE = 0.09

CAVEAT = (
    "국민연금 가입자 기준입니다. 사업장(공장·지점)별로 분리 신고되어 "
    "전사 인원과 다를 수 있고, 월평균보수는 고지금액에서 역산한 추정치라 "
    "고연봉 회사는 기준소득월액 상한 때문에 실제보다 낮게 나옵니다."
)

# 회사명 정규화용 접미사/접두사
_CORP_NOISE = re.compile(r"\(주\)|주식회사|\(유\)|유한회사|\(재\)|\(사\)|㈜|\s+")


@dataclass
class CompanyInfo:
    name: str
    employees: int | None = None
    avg_monthly_pay: int | None = None
    address: str = ""
    joined_this_month: int | None = None
    left_this_month: int | None = None
    data_month: str = ""
    found: bool = True

    @property
    def size_label(self) -> str:
        """직원 수를 사람이 읽는 규모 구간으로."""
        if self.employees is None:
            return "규모 정보 없음"
        n = self.employees
        if n >= 1000:
            return f"대규모 {n:,}명"
        if n >= 300:
            return f"중견 {n:,}명"
        if n >= 50:
            return f"중소 {n:,}명"
        return f"소규모 {n:,}명"

    @property
    def pay_label(self) -> str:
        if not self.avg_monthly_pay:
            return "보수 정보 없음"
        man = round(self.avg_monthly_pay / 10_000)
        suffix = "+" if self.avg_monthly_pay >= PENSION_CEILING * 0.95 else ""
        return f"월평균 약 {man:,}만원{suffix}"

    @property
    def turnover_label(self) -> str:
        """당월 입·퇴사 인원. 퇴사가 유독 많으면 참고 신호가 된다."""
        if self.joined_this_month is None or self.left_this_month is None:
            return ""
        return f"입사 {self.joined_this_month} / 퇴사 {self.left_this_month}"


def normalize_company(name: str) -> str:
    """`(주)삼성전자` → `삼성전자` 처럼 조회용으로 회사명을 정규화한다."""
    return _CORP_NOISE.sub("", name or "").strip()


# --- 캐시 -------------------------------------------------------------------
def init_cache(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS company_info_cache (
                company TEXT PRIMARY KEY,
                employees INTEGER,
                avg_monthly_pay INTEGER,
                address TEXT,
                joined_this_month INTEGER,
                left_this_month INTEGER,
                data_month TEXT,
                found INTEGER,
                fetched_at TEXT
            )
            """
        )


def _read_cache(company: str, db_path: str | None = None) -> CompanyInfo | None:
    try:
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM company_info_cache WHERE company = ?", (company,)
            ).fetchone()
    except Exception as exc:
        log.warning("회사 정보 캐시 조회 실패: %s", exc)
        return None
    if not row:
        return None
    return CompanyInfo(
        name=company,
        employees=row["employees"],
        avg_monthly_pay=row["avg_monthly_pay"],
        address=row["address"] or "",
        joined_this_month=row["joined_this_month"],
        left_this_month=row["left_this_month"],
        data_month=row["data_month"] or "",
        found=bool(row["found"]),
    )


def _write_cache(info: CompanyInfo, db_path: str | None = None) -> None:
    try:
        with connect(db_path) as conn:
            conn.execute(
                upsert(
                    "company_info_cache",
                    ["company", "employees", "avg_monthly_pay", "address",
                     "joined_this_month", "left_this_month", "data_month",
                     "found", "fetched_at"],
                    ["company"],
                ),
                (
                    info.name,
                    info.employees,
                    info.avg_monthly_pay,
                    info.address,
                    info.joined_this_month,
                    info.left_this_month,
                    info.data_month,
                    int(info.found),
                    now_iso(),
                ),
            )
    except Exception as exc:
        log.warning("회사 정보 캐시 저장 실패: %s", exc)


# --- API --------------------------------------------------------------------
def _find_text(item: ET.Element, *names: str) -> str:
    """응답 필드명이 버전마다 조금씩 달라 여러 후보를 순서대로 찾는다."""
    for name in names:
        node = item.find(name)
        if node is not None and (node.text or "").strip():
            return node.text.strip()
    return ""


def _to_int(value: str) -> int | None:
    try:
        return int(float(value.replace(",", "")))
    except (ValueError, AttributeError):
        return None


def _parse_item(item: ET.Element, company: str) -> CompanyInfo:
    employees = _to_int(_find_text(item, "jnngpCnt", "jnngpCn"))
    notice_amount = _to_int(_find_text(item, "crrmmNtcAmt"))

    avg_pay = None
    if employees and notice_amount and employees > 0:
        # 고지금액 = 기준소득월액 * 9% * 인원  →  기준소득월액 = 고지금액 / 인원 / 0.09
        avg_pay = int(notice_amount / employees / PENSION_RATE)
        if avg_pay <= 0 or avg_pay > PENSION_CEILING * 1.5:
            avg_pay = None

    return CompanyInfo(
        name=company,
        employees=employees,
        avg_monthly_pay=avg_pay,
        address=_find_text(item, "wkplRoadNmDtlAddr", "wkplAddr", "ldongAddrMgplDgCd"),
        joined_this_month=_to_int(_find_text(item, "nwAcqzrCnt")),
        left_this_month=_to_int(_find_text(item, "lssJnngpCnt")),
        data_month=_find_text(item, "dataCrtYm"),
        found=True,
    )


def fetch_company_info(
    company: str, db_path: str | None = None, use_cache: bool = True
) -> CompanyInfo | None:
    """회사명으로 국민연금 사업장 정보를 조회한다.

    인증키가 없으면 None (기능 자체가 꺼진 상태).
    조회했지만 없는 회사면 ``found=False`` 인 CompanyInfo.
    """
    key = settings.nps_service_key()
    if not key:
        return None

    normalized = normalize_company(company)
    if not normalized:
        return None

    if use_cache:
        cached = _read_cache(normalized, db_path)
        if cached is not None:
            return cached

    try:
        response = requests.get(
            BASE_URL,
            params={
                "serviceKey": key,
                "wkpl_nm": normalized,
                "pageNo": 1,
                "numOfRows": 10,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError) as exc:
        log.warning("국민연금 API 조회 실패 (%s): %s", normalized, exc)
        return None

    # 인증키 오류 등은 캐시하지 않고 바로 알린다.
    err = root.find(".//errMsg")
    if err is not None:
        log.warning("국민연금 API 오류 (%s): %s", normalized, err.text)
        return None

    items = root.findall(".//item")
    if not items:
        info = CompanyInfo(name=normalized, found=False)
        _write_cache(info, db_path)
        return info

    # 같은 이름의 사업장이 여러 곳이면 가입자가 가장 많은 곳(본사로 추정)을 쓴다.
    parsed = [_parse_item(item, normalized) for item in items]
    parsed.sort(key=lambda i: i.employees or 0, reverse=True)
    best = parsed[0]
    _write_cache(best, db_path)
    return best


def enrich_jobs(jobs: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    """공고 목록에 회사 정보를 붙인다.

    API 호출 수를 아끼려고 회사명 기준으로 중복을 제거하고, 상위 ``limit``개
    회사만 조회한다. 나머지는 캐시에 있으면 쓰고 없으면 비워 둔다.
    """
    if not settings.nps_service_key():
        return jobs

    init_cache()
    seen: dict[str, CompanyInfo | None] = {}
    budget = limit

    for job in jobs:
        name = normalize_company(job.get("company", ""))
        if not name:
            continue
        if name not in seen:
            cached = _read_cache(name)
            if cached is not None:
                seen[name] = cached
            elif budget > 0:
                seen[name] = fetch_company_info(name)
                budget -= 1
            else:
                seen[name] = None
        info = seen[name]
        if info and info.found:
            job["company_info"] = asdict(info)
            job["employees"] = info.employees
            job["avg_monthly_pay"] = info.avg_monthly_pay
    return jobs
