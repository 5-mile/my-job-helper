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
    monkeypatch.setenv("DIGEST_LETTERS", "1")


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
    {**_job(2, "화학 생산직", location="충북진천군"), "category": "외국계"},
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

    # 중견·외국계 공고에만 '자소서 쓸까요?' 를 예/아니오 버튼과 함께 묻는다 (회사2 = 외국계).
    assert len(sent) == 2
    ask_text, ask_kb = sent[1]
    assert "회사2" in ask_text and "자소서 쓸까요" in ask_text
    assert [b["callback_data"] for b in ask_kb["inline_keyboard"][0]] == ["y:saramin:2", "n:saramin:2"]

    second = digest.run(limit=5, today=TODAY, fetch=fetch)
    assert second["new"] == 0 and len(sent) == 2


def test_big_company_is_requested_without_asking(monkeypatch):
    from jobhelper import db

    sent = []
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: sent.append((text, kb)) or True)
    big = {**_job(11, "화학 생산직"), "company": "롯데케미칼(주)"}  # 회사명으로 다시 분류된다
    digest.run(limit=5, today=TODAY, fetch=lambda *a, **k: ([*JOBS, big], _Diag()))

    saved = {j["company"]: j for j in db.load_jobs()}
    assert saved["롯데케미칼(주)"]["memo"] == digest.REQUEST_TAG and saved["롯데케미칼(주)"]["status"] == "지원 예정"
    assert "대기업" in sent[0][0] and "롯데케미칼" in sent[0][0]
    assert all("롯데케미칼" not in t for t, _ in sent[1:])  # 목록·질문에 다시 나오지 않는다


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


def test_telegram_falls_back_to_db_config(monkeypatch):
    """GitHub Actions 처럼 .env 가 없어도 DB의 bot_config 로 보낼 수 있어야 한다."""
    from jobhelper import notify, settings
    from jobhelper.storage import connect

    monkeypatch.setattr(settings, "telegram_config", lambda: None)
    assert notify.telegram_config() is None  # 표가 없으면 조용히 None

    with connect() as conn:
        conn.execute("CREATE TABLE bot_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO bot_config VALUES ('bot_token', 'T'), ('chat_id', '42')")
    assert notify.telegram_config() == ("T", "42")


def test_lookalike_names_are_not_big_companies():
    from jobhelper.classify import classify_company

    assert classify_company("(주)삼성테이프") != "대기업"
    assert classify_company("(주)농심") == "대기업"


def test_expired_interest_jobs_are_purged_but_applied_ones_stay():
    from jobhelper import db

    db.init_db()
    for company, status, deadline in [("옛관심", "관심", "2026-10-01"), ("아직관심", "관심", "2026-10-30"),
                                      ("옛지원", "지원 완료", "2026-10-01"), ("상시", "관심", "")]:
        db.save_job({"company": company, "position": "생산", "status": status, "deadline": deadline})
    assert db.purge_expired(TODAY.isoformat()) == ["옛관심"]
    assert {j["company"] for j in db.load_jobs()} == {"아직관심", "옛지원", "상시"}


def test_pool_keeps_popularity_rank(monkeypatch):
    import json

    from jobhelper.storage import connect

    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: True)
    digest.run(limit=5, today=TODAY, fetch=lambda *a, **k: (JOBS, _Diag()))
    with connect() as conn:
        pops = {r["job_key"]: json.loads(r["data"]).get("pop") for r in conn.execute("SELECT job_key, data FROM digest_pool WHERE data <> '{}'")}
    assert pops["saramin:1"] == 0 and pops["saramin:2"] == 1  # 지원자 많은순 목록의 등장 순서


def test_bot_config_overrides_regions_and_limit():
    from jobhelper.storage import connect

    assert digest._regions() == ["음성", "진천"]  # 표가 없으면 환경 변수
    with connect() as conn:
        conn.execute("CREATE TABLE bot_config (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO bot_config VALUES ('digest_groups', '충북,충남·대전·세종'), ('digest_limit', '3')")
    assert digest._regions() == ["충북", "충남", "대전", "세종"]
    assert digest._bot_config("digest_limit") == "3"


def test_weekly_summary_lists_unapplied_deadlines():
    from jobhelper import db

    db.init_db()
    db.save_job({"company": "곧마감", "position": "생산", "status": "관심", "deadline": "2026-10-10"})
    db.save_job({"company": "이미지원", "position": "생산", "status": "지원 완료", "deadline": "2026-10-10"})
    text = digest.build_weekly(TODAY)
    assert "곧마감" in text and "이미지원" not in text and "지원 완료 1" in text


def test_recommend_picks_popular_unseen_and_skips_saved(monkeypatch):
    from jobhelper import db

    sent = []
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: sent.append(text) or True)
    pool = [{**_job(n, "화학 생산직"), "pop": p} for n, p in [(31, 5), (32, 1), (33, 3), (34, 2)]]
    digest.init_digest_pool()
    digest._add_to_pool([{**j, "key": digest.job_key(j), "score": 4} for j in pool])
    db.init_db()
    db.save_job({"company": "회사32", "position": "화학 생산직"})  # 이미 보관함에 있음
    result = digest.recommend(TODAY)
    assert sent[0].index("회사34") < sent[0].index("회사33") < sent[0].index("회사31")  # 인기순(pop 작은 순)
    assert "회사32" not in sent[0] and result["sent"] and digest.recommend(TODAY)["new"] == 0


def test_letters_are_off_by_default(monkeypatch):
    monkeypatch.delenv("DIGEST_LETTERS")
    sent = []
    monkeypatch.setattr(digest, "send_telegram", lambda text, kb=None: sent.append(text) or True)
    digest.run(limit=5, today=TODAY, fetch=lambda *a, **k: ([*JOBS, {**_job(11, "화학 생산직"), "company": "롯데케미칼(주)"}], _Diag()))
    assert not any("자소서" in t for t in sent)  # 자동 요청 알림도, '쓸까요?' 질문도 없다


def test_keyboard_wraps_star_buttons_in_rows_of_five():
    jobs = [{"key": f"saramin:{n}"} for n in range(1, 11)]
    rows = digest.keyboard(jobs)["inline_keyboard"]
    assert [len(r) for r in rows] == [5, 5, 3]  # ⭐ 5 + ⭐ 5 + 분류 3
