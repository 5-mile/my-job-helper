"""국민연금 연동 · 워크넷 · 알림 · 수집 진단 테스트 (네트워크 없이 동작)."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import company_info, db, notify, settings  # noqa: E402
from jobhelper.company_info import CompanyInfo, normalize_company  # noqa: E402
from jobhelper.scrapers.saramin import FetchDiagnostics, _select_items  # noqa: E402
from jobhelper.scrapers.worknet import _parse_wanted  # noqa: E402
from jobhelper.ui import company_badges  # noqa: E402

TODAY = date(2026, 9, 5)


# --- 회사명 정규화 -----------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(주)삼성전자", "삼성전자"),
        ("주식회사 그린제약", "그린제약"),
        ("㈜프라임테크놀러지", "프라임테크놀러지"),
        ("현대모비스(주)", "현대모비스"),
        ("(유) 볼보그룹코리아", "볼보그룹코리아"),
    ],
)
def test_normalize_company(raw, expected):
    assert normalize_company(raw) == expected


# --- 국민연금 응답 파싱 ------------------------------------------------------
SAMPLE_NPS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><body><items>
  <item>
    <dataCrtYm>202608</dataCrtYm>
    <wkplNm>테스트기업</wkplNm>
    <jnngpCnt>250</jnngpCnt>
    <crrmmNtcAmt>76500000</crrmmNtcAmt>
    <nwAcqzrCnt>5</nwAcqzrCnt>
    <lssJnngpCnt>3</lssJnngpCnt>
    <wkplRoadNmDtlAddr>경기도 화성시</wkplRoadNmDtlAddr>
  </item>
</items></body></response>
"""


def test_parse_nps_item_derives_average_pay():
    item = ET.fromstring(SAMPLE_NPS_XML).find(".//item")
    info = company_info._parse_item(item, "테스트기업")

    assert info.employees == 250
    # 76,500,000 / 250 / 0.09 = 3,400,000원
    assert info.avg_monthly_pay == 3_400_000
    assert info.joined_this_month == 5
    assert info.left_this_month == 3
    assert "화성시" in info.address


def test_parse_nps_item_rejects_absurd_pay():
    """고지금액이 이상하면 보수 추정을 포기한다 (잘못된 숫자를 보여주지 않는다)."""
    xml = SAMPLE_NPS_XML.replace("<crrmmNtcAmt>76500000</crrmmNtcAmt>",
                                 "<crrmmNtcAmt>999999999999</crrmmNtcAmt>")
    item = ET.fromstring(xml).find(".//item")
    info = company_info._parse_item(item, "테스트기업")
    assert info.employees == 250
    assert info.avg_monthly_pay is None


def test_company_info_labels():
    info = CompanyInfo(name="A", employees=1200, avg_monthly_pay=4_100_000,
                       joined_this_month=10, left_this_month=2)
    assert "1,200명" in info.size_label
    assert "410만원" in info.pay_label
    assert "입사 10" in info.turnover_label

    empty = CompanyInfo(name="B")
    assert empty.size_label == "규모 정보 없음"
    assert empty.pay_label == "보수 정보 없음"
    assert empty.turnover_label == ""


def test_high_pay_is_marked_as_capped():
    """국민연금 상한에 걸린 회사는 '이상'임을 표시해야 한다."""
    info = CompanyInfo(name="A", employees=100, avg_monthly_pay=6_370_000)
    assert info.pay_label.endswith("+")


def test_fetch_company_info_without_key_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "nps_service_key", lambda: None)
    assert company_info.fetch_company_info("삼성전자") is None


def test_enrich_jobs_is_noop_without_key(monkeypatch):
    monkeypatch.setattr(settings, "nps_service_key", lambda: None)
    jobs = [{"company": "삼성전자", "position": "생산직"}]
    assert company_info.enrich_jobs(jobs) == jobs
    assert "company_info" not in jobs[0]


# --- UI 뱃지 ----------------------------------------------------------------
def test_company_badges_empty_when_not_found():
    assert company_badges({"company_info": {"found": False}}) == []
    assert company_badges({}) == []


def test_company_badges_render_real_numbers():
    badges = company_badges(
        {"company_info": {"found": True, "employees": 1234, "avg_monthly_pay": 3_800_000,
                          "joined_this_month": 4, "left_this_month": 1}}
    )
    joined = " ".join(badges)
    assert "1,234명" in joined
    assert "380만" in joined


# --- 워크넷 파싱 -------------------------------------------------------------
SAMPLE_WORKNET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<wantedRoot><wanted>
  <company>테스트산업</company>
  <title>생산직 사원 모집</title>
  <salTpNm>연봉</salTpNm>
  <sal>3000만원</sal>
  <region>경기 평택시</region>
  <empTpNm>정규직</empTpNm>
  <career>신입</career>
  <minEdubg>고졸</minEdubg>
  <closeDt>2026-09-20</closeDt>
  <wantedInfoUrl>https://example.com/w/1</wantedInfoUrl>
  <wantedAuthNo>K123456</wantedAuthNo>
</wanted></wantedRoot>
"""


def test_parse_worknet_item():
    node = ET.fromstring(SAMPLE_WORKNET_XML).find(".//wanted")
    job = _parse_wanted(node)

    assert job["source"] == "워크넷"
    assert job["company"] == "테스트산업"
    assert job["job_key"] == "worknet:K123456"
    assert job["salary"] == "연봉 3000만원"
    assert job["deadline"] == "2026-09-20"
    assert job["location"] == "경기 평택시"
    assert "rating" not in job


def test_parse_worknet_item_missing_fields():
    node = ET.fromstring("<wanted><company>회사만</company></wanted>")
    assert _parse_wanted(node) is None


def test_worknet_without_key_returns_empty(monkeypatch):
    from jobhelper.scrapers import worknet

    monkeypatch.setattr(settings, "worknet_auth_key", lambda: None)
    assert worknet.fetch_worknet_jobs(["생산"]) == []


# --- 수집 진단 ---------------------------------------------------------------
def test_diagnostics_reports_total_failure():
    diag = FetchDiagnostics(pages_requested=3, pages_failed=3)
    assert not diag.healthy
    assert "연결하지 못했습니다" in diag.warning


def test_diagnostics_reports_markup_change():
    diag = FetchDiagnostics(pages_requested=3, pages_failed=0, items_found=0)
    assert not diag.healthy
    assert "구조가 바뀌었을" in diag.warning


def test_diagnostics_reports_partial_failure():
    diag = FetchDiagnostics(pages_requested=3, pages_failed=1, items_found=20)
    assert diag.healthy
    assert "일부 공고가 빠졌" in diag.warning


def test_diagnostics_silent_when_healthy():
    diag = FetchDiagnostics(pages_requested=3, pages_failed=0, items_found=40)
    assert diag.healthy
    assert diag.warning == ""


def test_selector_fallback_finds_items():
    """기본 셀렉터가 사라져도 대체 셀렉터로 공고를 찾아낸다."""
    from bs4 import BeautifulSoup

    html = '<div class="list_item"><span>A</span></div><div class="list_item"><span>B</span></div>'
    items, selector = _select_items(BeautifulSoup(html, "html.parser"))
    assert len(items) == 2
    assert selector == ".list_item"


def test_selector_returns_empty_when_nothing_matches():
    from bs4 import BeautifulSoup

    items, selector = _select_items(BeautifulSoup("<div class='nope'></div>", "html.parser"))
    assert items == []
    assert selector == ""


# --- 알림 --------------------------------------------------------------------
def _saved(company, deadline, status="관심"):
    return {
        "source": "사람인", "company": company, "position": f"{company} 생산직",
        "date": "", "link": "https://example.com", "location": "", "category": "",
        "welfares": [], "deadline": deadline, "status": status,
    }


def test_find_urgent_jobs(tmp_path):
    path = str(tmp_path / "n.db")
    db.init_db(path)
    db.save_job(_saved("임박기업", "2026-09-06"), path)      # D-1
    db.save_job(_saved("오늘기업", "2026-09-05"), path)      # D-DAY
    db.save_job(_saved("여유기업", "2026-10-30"), path)      # 한참 남음
    db.save_job(_saved("지난기업", "2026-09-01"), path)      # 이미 마감
    db.save_job(_saved("떨어진기업", "2026-09-06", "불합격"), path)

    urgent = notify.find_urgent_jobs(within_days=3, today=TODAY, db_path=path)
    names = [j["company"] for j in urgent]

    assert names == ["오늘기업", "임박기업"]  # 남은 일수 오름차순
    assert "여유기업" not in names
    assert "지난기업" not in names
    assert "떨어진기업" not in names  # 결과가 나온 공고는 제외


def test_build_message_includes_dday_and_link(tmp_path):
    jobs = [{"company": "A사", "position": "생산직", "days_left": 0,
             "status": "지원 완료", "link": "https://example.com/a"}]
    text = notify.build_message(jobs, TODAY)
    assert "오늘 마감" in text
    assert "A사" in text
    assert "https://example.com/a" in text


def test_alert_is_not_sent_twice_a_day(tmp_path):
    path = str(tmp_path / "a.db")
    db.init_db(path)
    notify.init_alert_log(path)
    db.save_job(_saved("임박기업", "2026-09-06"), path)

    first = notify.run(within_days=3, today=TODAY, db_path=path, dry_run=True)
    assert first["to_send"] == 1

    # 발송 기록을 남기면 같은 날에는 다시 보내지 않는다.
    job_id = db.load_jobs(db_path=path)[0]["id"]
    notify._mark_sent([job_id], TODAY, path)

    second = notify.run(within_days=3, today=TODAY, db_path=path, dry_run=True)
    assert second["found"] == 1
    assert second["to_send"] == 0


def test_run_without_channels_reports_not_sent(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    db.init_db(path)
    db.save_job(_saved("임박기업", "2026-09-06"), path)
    monkeypatch.setattr(settings, "telegram_config", lambda: None)
    monkeypatch.setattr(settings, "email_config", lambda: None)

    result = notify.run(within_days=3, today=TODAY, db_path=path)
    assert result["to_send"] == 1
    assert result["sent"] is False
    assert result["channels"] == []


# --- 설정 --------------------------------------------------------------------
def test_settings_env_takes_priority(monkeypatch):
    monkeypatch.setenv("NPS_SERVICE_KEY", "from-env")
    assert settings.get("NPS_SERVICE_KEY") == "from-env"


def test_settings_missing_returns_none(monkeypatch):
    monkeypatch.delenv("A_KEY_THAT_DOES_NOT_EXIST", raising=False)
    assert settings.get("A_KEY_THAT_DOES_NOT_EXIST") is None


def test_get_bool(monkeypatch):
    monkeypatch.setenv("FLAG_ON", "true")
    monkeypatch.setenv("FLAG_OFF", "0")
    assert settings.get_bool("FLAG_ON") is True
    assert settings.get_bool("FLAG_OFF") is False
    assert settings.get_bool("FLAG_UNSET", default=True) is True


# --- 사람인 정렬 파라미터 --------------------------------------------------
def test_search_url_uses_recruit_sort():
    """사람인은 `sort` 를 무시한다. `recruitSort` 로 보내야 정렬이 먹는다."""
    from jobhelper.scrapers.saramin import SEARCH_URL

    url = SEARCH_URL.format(keyword="생산", sort="reg_dt", page=2)
    assert "recruitSort=reg_dt" in url
    assert "&sort=" not in url
    assert "recruitPage=2" in url


def test_sort_options_use_valid_codes():
    """UI에 노출되는 정렬 코드가 사람인이 실제로 받는 값이어야 한다."""
    from jobhelper.config import SARAMIN_ALL_SORTS, SARAMIN_SORT_OPTIONS

    valid = {"relation", "reg_dt", "closing_dt"}
    assert set(SARAMIN_SORT_OPTIONS.values()) <= valid
    assert set(SARAMIN_ALL_SORTS) == valid


def test_multiple_sorts_multiply_requests(monkeypatch):
    """정렬을 여러 개 주면 정렬 × 페이지 만큼 요청한다."""
    from jobhelper.scrapers import saramin

    calls = []

    def fake_page(keyword, sort_code, page):
        calls.append((keyword, sort_code, page))
        return [], "", ""

    monkeypatch.setattr(saramin, "_fetch_page", fake_page)
    _, diag = saramin.fetch_saramin_jobs_detailed(["생산"], ["relation", "reg_dt"], pages=3)

    assert len(calls) == 6  # 정렬 2종 × 3페이지
    assert diag.pages_requested == 6
    assert {c[1] for c in calls} == {"relation", "reg_dt"}


def test_single_sort_string_still_works(monkeypatch):
    from jobhelper.scrapers import saramin

    calls = []
    monkeypatch.setattr(
        saramin, "_fetch_page",
        lambda k, s, p: (calls.append((k, s, p)), ([], "", ""))[1],
    )
    saramin.fetch_saramin_jobs_detailed(["생산"], "reg_dt", pages=2)

    assert len(calls) == 2
    assert {c[1] for c in calls} == {"reg_dt"}


def test_empty_sort_falls_back_to_default(monkeypatch):
    from jobhelper.scrapers import saramin

    calls = []
    monkeypatch.setattr(
        saramin, "_fetch_page",
        lambda k, s, p: (calls.append((k, s, p)), ([], "", ""))[1],
    )
    saramin.fetch_saramin_jobs_detailed(["생산"], [], pages=1)

    assert [c[1] for c in calls] == ["relation"]
