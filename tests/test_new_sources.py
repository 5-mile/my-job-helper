"""사람인 공식 API · 공공기관 채용정보 수집기 테스트.

실제 키가 없어 응답 형태를 확인하지 못했으므로, 파서가
(1) 문서대로 온 응답을 읽고
(2) 필드가 빠지거나 이름이 달라도 죽지 않고
(3) 해석 실패를 조용히 넘기지 않고 경고를 돌려주는지
를 검증한다.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import settings  # noqa: E402
from jobhelper.scrapers import publicjobs, saramin_api  # noqa: E402


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        if isinstance(self._payload, str):
            raise json.JSONDecodeError("bad", self._payload, 0)
        return self._payload


def _serve(monkeypatch, module, pages: list):
    """호출 순서대로 응답을 돌려준다. 다 쓰면 빈 결과."""
    calls = []
    queue = list(pages)

    def fake_get(url, params=None, **kwargs):
        calls.append(params or {})
        return _FakeResponse(queue.pop(0) if queue else module._EMPTY)

    monkeypatch.setattr(module.requests, "get", fake_get)
    return calls


# ==========================================
# 사람인 공식 API
# ==========================================
SARAMIN_ITEM = {
    "id": "50123456",
    "url": "https://www.saramin.co.kr/job/50123456",
    "company": {"detail": {"name": "테스트산업(주)", "href": "https://example.com"}},
    "position": {
        "title": "생산직 사원 채용",
        "industry": {"code": "1", "name": "자동차부품"},
        "location": {"code": "101000", "name": "경기 평택시"},
        "job-type": {"code": "1", "name": "정규직"},
        "job-mid-code": {"code": "2", "name": "생산·제조"},
        "experience-level": {"code": 1, "min": 0, "max": 0, "name": "신입"},
        "required-education-level": {"code": "8", "name": "고졸↑"},
    },
    "salary": {"code": "0", "name": "회사내규에 따름"},
    "expiration-date": "2026-09-20",
    "expiration-timestamp": "1789000000",
}
SARAMIN_OK = {"jobs": {"count": 1, "total": "1", "job": [SARAMIN_ITEM]}}


@pytest.fixture(autouse=True)
def _empty_payloads(monkeypatch):
    monkeypatch.setattr(saramin_api, "_EMPTY", {"jobs": {"job": []}}, raising=False)
    monkeypatch.setattr(publicjobs, "_EMPTY", {"result": []}, raising=False)


@pytest.fixture
def saramin_key(monkeypatch):
    monkeypatch.setattr(settings, "saramin_api_key", lambda: "test-key")


def test_saramin_api_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "saramin_api_key", lambda: None)
    jobs, warning = saramin_api.fetch_saramin_api_jobs(["생산"])
    assert jobs == []
    assert warning == ""


def test_saramin_api_parses_documented_shape(monkeypatch, saramin_key):
    _serve(monkeypatch, saramin_api, [SARAMIN_OK])
    jobs, warning = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)

    assert warning == ""
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "사람인API"
    assert job["company"] == "테스트산업(주)"
    assert job["position"] == "생산직 사원 채용"
    assert job["job_key"] == "saramin:50123456"
    assert job["location"] == "경기 평택시"
    assert job["career"] == "신입"
    assert job["employment"] == "정규직"
    assert job["deadline"] == "2026-09-20"
    assert "자동차부품" in job["sector"]


def test_saramin_api_sends_access_key(monkeypatch, saramin_key):
    calls = _serve(monkeypatch, saramin_api, [SARAMIN_OK])
    saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)
    assert calls[0]["access-key"] == "test-key"
    assert calls[0]["keywords"] == "생산"


def test_saramin_api_survives_missing_fields(monkeypatch, saramin_key):
    """필드가 빠져도 회사명과 공고명만 있으면 읽어야 한다."""
    minimal = {"company": {"detail": {"name": "회사"}}, "position": {"title": "공고"}}
    _serve(monkeypatch, saramin_api, [{"jobs": {"job": [minimal]}}])

    jobs, warning = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)
    assert warning == ""
    assert len(jobs) == 1
    assert jobs[0]["location"] == "전국"
    assert jobs[0]["deadline"] == ""


def test_saramin_api_skips_item_without_company(monkeypatch, saramin_key):
    _serve(monkeypatch, saramin_api, [{"jobs": {"job": [{"position": {"title": "제목만"}}]}}])
    jobs, warning = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)
    assert jobs == []
    # 항목은 왔는데 하나도 못 읽었으므로 조용히 넘기지 않는다
    assert "형태가 예상과 다릅니다" in warning


def test_saramin_api_reports_auth_error(monkeypatch, saramin_key):
    _serve(monkeypatch, saramin_api, [{"code": 2, "message": "사용 불가능한 access-key 입니다."}])
    jobs, warning = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)
    assert jobs == []
    assert "access-key" in warning


def test_saramin_api_handles_single_item_not_in_list(monkeypatch, saramin_key):
    """1건일 때 리스트가 아닌 객체로 오는 경우."""
    _serve(monkeypatch, saramin_api, [{"jobs": {"job": SARAMIN_ITEM}}])
    jobs, _ = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)
    assert len(jobs) == 1


def test_saramin_api_deduplicates(monkeypatch, saramin_key):
    _serve(monkeypatch, saramin_api, [{"jobs": {"job": [SARAMIN_ITEM, dict(SARAMIN_ITEM)]}}])
    jobs, _ = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1)
    assert len(jobs) == 1


def test_saramin_api_exclude(monkeypatch, saramin_key):
    _serve(monkeypatch, saramin_api, [SARAMIN_OK])
    jobs, _ = saramin_api.fetch_saramin_api_jobs(["생산"], pages=1, exclude=["자동차부품"])
    assert jobs == []


# ==========================================
# 공공기관 채용정보
# ==========================================
PUBLIC_ITEM = {
    "recrutPblntSn": "123456",
    "instNm": "한국테스트공사",
    "recrutPbancTtl": "2026년 하반기 신입사원(기술직) 채용",
    "ncsCdNmLst": "기계, 전기",
    "hireTypeNmLst": "정규직",
    "workRgnNmLst": "경기, 충남",
    "acbgCondNmLst": "학력무관",
    "recrutSeNm": "신입",
    "recrutNope": "20",
    "pbancBgngYmd": "20260901",
    "pbancEndYmd": "20260920",
    "srcUrl": "https://job.alio.go.kr/example",
}
PUBLIC_OK = {"resultCode": 200, "result": [PUBLIC_ITEM]}


@pytest.fixture
def public_key(monkeypatch):
    monkeypatch.setattr(settings, "public_jobs_key", lambda: "test-key")


def test_public_jobs_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "public_jobs_key", lambda: None)
    jobs, warning = publicjobs.fetch_public_jobs(["생산"])
    assert jobs == []
    assert warning == ""


def test_public_jobs_parses_documented_shape(monkeypatch, public_key):
    _serve(monkeypatch, publicjobs, [PUBLIC_OK])
    jobs, warning = publicjobs.fetch_public_jobs(None, pages=1)

    assert warning == ""
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "공공기관"
    assert job["category"] == "공공기관"
    assert job["company"] == "한국테스트공사"
    assert job["job_key"] == "alio:123456"
    assert job["deadline"] == "2026-09-20"  # 20260920 -> ISO
    assert job["location"] == "경기, 충남"
    assert job["employment"] == "정규직"
    assert job["headcount"] == "20"


def test_public_jobs_builds_link_when_missing(monkeypatch, public_key):
    item = {k: v for k, v in PUBLIC_ITEM.items() if k != "srcUrl"}
    _serve(monkeypatch, publicjobs, [{"resultCode": 200, "result": [item]}])
    jobs, _ = publicjobs.fetch_public_jobs(None, pages=1)
    assert "recrutPblntSn=123456" in jobs[0]["link"]


def test_public_jobs_filters_by_keyword(monkeypatch, public_key):
    _serve(monkeypatch, publicjobs, [PUBLIC_OK])
    jobs, _ = publicjobs.fetch_public_jobs(["기계"], pages=1)
    assert len(jobs) == 1

    _serve(monkeypatch, publicjobs, [PUBLIC_OK])
    jobs, _ = publicjobs.fetch_public_jobs(["전혀다른직무"], pages=1)
    assert jobs == []


def test_public_jobs_reports_error_code(monkeypatch, public_key):
    _serve(monkeypatch, publicjobs, [{"resultCode": 30, "resultMsg": "등록되지 않은 서비스키"}])
    jobs, warning = publicjobs.fetch_public_jobs(None, pages=1)
    assert jobs == []
    assert "서비스키" in warning


def test_public_jobs_survives_alternate_field_names(monkeypatch, public_key):
    """필드명이 조금 달라도 후보 목록으로 읽어낸다."""
    item = {"pblntInstNm": "다른공사", "pbancTtl": "채용 공고", "endYmd": "2026-10-01"}
    _serve(monkeypatch, publicjobs, [{"resultCode": 200, "result": [item]}])
    jobs, warning = publicjobs.fetch_public_jobs(None, pages=1)
    assert warning == ""
    assert jobs[0]["company"] == "다른공사"
    assert jobs[0]["deadline"] == "2026-10-01"


def test_public_jobs_warns_on_unparseable_items(monkeypatch, public_key):
    _serve(monkeypatch, publicjobs, [{"resultCode": 200, "result": [{"뜬금없는": "필드"}]}])
    jobs, warning = publicjobs.fetch_public_jobs(None, pages=1)
    assert jobs == []
    assert "형태가 예상과 다릅니다" in warning


def test_public_jobs_handles_nested_result(monkeypatch, public_key):
    """result 가 {'item': [...]} 형태로 오는 경우."""
    _serve(monkeypatch, publicjobs, [{"resultCode": 200, "result": {"item": [PUBLIC_ITEM]}}])
    jobs, _ = publicjobs.fetch_public_jobs(None, pages=1)
    assert len(jobs) == 1


def test_public_jobs_sends_service_key(monkeypatch, public_key):
    calls = _serve(monkeypatch, publicjobs, [PUBLIC_OK])
    publicjobs.fetch_public_jobs(None, pages=1)
    assert calls[0]["serviceKey"] == "test-key"
    assert calls[0]["resultType"] == "json"
    assert calls[0]["ongoingYn"] == "Y"


def test_public_category_is_in_config():
    """공공기관 탭이 실제로 존재해야 결과가 보인다."""
    from jobhelper.config import CATEGORIES

    assert publicjobs.CATEGORY in CATEGORIES
