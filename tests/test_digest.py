"""새 공고 요약: 거르기 · 순서 · 한 번 보낸 건 다시 안 보내기 (conftest가 임시 SQLite로 묶는다)."""

from __future__ import annotations

from datetime import date

from jobhelper import digest

TODAY = date(2026, 10, 7)


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
    _job(1, "이차전지 양극재 생산 오퍼레이터"),                # 중복
]


def test_select_filters_and_orders():
    picked = digest.select(JOBS, ["음성", "진천"], TODAY)
    assert [j["company"] for j in picked] == ["회사1", "회사2"]  # 이차전지가 화학보다 위


def test_run_sends_once_and_never_again(monkeypatch):
    sent = []
    monkeypatch.setattr(digest, "send_telegram", lambda text: sent.append(text) or True)
    fetch = lambda *a, **k: (JOBS, _Diag())

    first = digest.run(limit=5, today=TODAY, fetch=fetch)
    assert first["sent"] and first["new"] == 2
    assert "rec_idx=1\n" in sent[0] + "\n" and "search_uuid" not in sent[0]  # 짧은 링크

    second = digest.run(limit=5, today=TODAY, fetch=fetch)
    assert second["new"] == 0 and len(sent) == 1


def test_failed_send_is_retried_next_time(monkeypatch):
    monkeypatch.setattr(digest, "send_telegram", lambda text: False)
    fetch = lambda *a, **k: (JOBS, _Diag())
    assert digest.run(limit=5, today=TODAY, fetch=fetch)["sent"] is False
    # 못 보낸 건 기록하지 않았으니 다음 실행에 다시 나와야 한다.
    assert digest.run(limit=5, today=TODAY, fetch=fetch, dry_run=True)["new"] == 2
