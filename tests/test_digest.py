"""새 공고 요약: 거르기 · 순서 · 한 번 보낸 건 다시 안 보내기 (conftest가 임시 SQLite로 묶는다)."""

from __future__ import annotations

from datetime import date

import pytest

from jobhelper import digest

TODAY = date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _fixed_regions(monkeypatch):
    # 기본 지역(경기·충청·경북)이 바뀌어도 아래 기대값이 흔들리지 않게 고정한다.
    monkeypatch.setenv("DIGEST_REGIONS", "음성,진천")
    monkeypatch.setenv("DIGEST_PREFERRED", "없음")


class _Diag:
    warning = ""


def _job(n, position, location="충북음성군", education="고졸↑", employment="정규직", deadline="2026-10-30"):
    return {
        "company": f"회사{n}", "position": position, "location": location, "education": education,
        "employment": employment, "deadline": deadline,
        "link": f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={n}&search_uuid=x",
    }


JOBS = [
    _job(1, "이차전지 양극재 생산 오퍼레이터"),
    _job(2, "화학 생산직", location="충북진천군"),
    _job(3, "생산직 사원", location="경북구미시"),              # 지역 밖
    _job(4, "이차전지 소재 연구원"),                          # 직무 다름
    _job(5, "화학 생산직", education="대졸↑"),                # 4년제 필수
    _job(6, "화학 생산직", deadline="2026-10-01"),            # 마감 지남
    _job(7, "화학 생산직", location="충북전체,진천군,청주시"),  # 지역 도배
    _job(8, "단기 생산 아르바이트"),
    _job(9, "2차전지 공장 설비보전 담당"),                    # 공무는 지원 안 함
    _job(10, "생산기술팀 공무 신입"),
    _job(1, "이차전지 양극재 생산 오퍼레이터"),                # 중복
]


def test_select_filters_and_orders():
    picked = digest.select(JOBS, ["음성", "진천"], TODAY)
    assert [j["company"] for j in picked] == ["회사1", "회사2"]  # 이차전지가 화학보다 위


def test_run_sends_once_and_never_again(monkeypatch):
    sent = []
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: sent.append((text, kb)) or True)
    fetch = lambda *a, **k: (JOBS, _Diag())

    first = digest.run(limit=5, today=TODAY, fetch=fetch)
    assert first["sent"] and first["new"] == 2
    text, kb = sent[0]
    assert "view?rec_idx=1" in text and "search_uuid" not in text  # 짧은 링크
    # ⭐ 버튼은 목록 순서와 같고, 텔레그램 제한(64바이트) 안이어야 한다.
    stars = [b["callback_data"] for b in kb["inline_keyboard"][0]]
    assert stars == ["s:saramin:1", "s:saramin:2"]
    assert all(len(c.encode()) <= 64 for c in stars)

    second = digest.run(limit=5, today=TODAY, fetch=fetch)
    assert second["new"] == 0 and len(sent) == 1


def test_leftovers_wait_in_pool_for_next_button(monkeypatch):
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: True)
    fetch = lambda *a, **k: (JOBS, _Diag())
    digest.run(limit=1, today=TODAY, fetch=fetch)
    # 1건만 보냈으니 나머지 1건은 '다음 5건' 버튼이 꺼낼 수 있어야 한다.
    left = digest.next_batch(5, TODAY)
    assert [j["company"] for j in left] == ["회사2"]
    assert left[0]["link"].endswith("rec_idx=2")


def test_failed_send_is_retried_next_time(monkeypatch):
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: False)
    fetch = lambda *a, **k: (JOBS, _Diag())
    assert digest.run(limit=5, today=TODAY, fetch=fetch)["sent"] is False
    # 못 보낸 건 기록하지 않았으니 다음 실행에 다시 나와야 한다.
    assert digest.run(limit=5, today=TODAY, fetch=fetch, dry_run=True)["new"] == 2


def test_old_sent_log_is_carried_over(monkeypatch):
    from jobhelper.storage import connect

    with connect() as conn:
        conn.execute("CREATE TABLE digest_sent (job_key TEXT PRIMARY KEY, sent_at TEXT)")
        conn.execute("INSERT INTO digest_sent VALUES ('saramin:1', '2026-10-07T09:00:00')")
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: True)
    result = digest.run(limit=5, today=TODAY, fetch=lambda *a, **k: (JOBS, _Diag()))
    # 예전에 이미 받은 회사1은 다시 오지 않는다.
    assert result["new"] == 1 and "회사1" not in result["message"]


def test_preferred_region_ranks_higher_within_allowed_range():
    jobs = [
        _job(21, "화학 생산직", location="경기화성시"),
        _job(22, "화학 생산직", location="충북음성군"),
        _job(23, "화학 생산직", location="경남김해시"),   # 범위 밖
        _job(24, "화학 생산직", location="서울강서구"),   # 범위 밖
    ]
    picked = digest.select(jobs, ["경기", "충북", "경북"], TODAY, ["음성"])
    assert [j["company"] for j in picked] == ["회사22", "회사21"]
