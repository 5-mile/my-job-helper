"""네트워크 없이 돌아가는 핵심 로직 테스트."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import db  # noqa: E402
from jobhelper.classify import analyze, classify_company, extract_welfares  # noqa: E402
from jobhelper.dates import days_left, format_dday, parse_deadline, parse_rss_date  # noqa: E402
from jobhelper.scrapers.naver_blog import _clean_title  # noqa: E402
from jobhelper.ui import job_card  # noqa: E402

TODAY = date(2026, 9, 4)


# --- 분류 -------------------------------------------------------------------
def test_classify_large_corp():
    assert classify_company("삼성전자", "반도체 생산직") == "대기업"


def test_classify_foreign_corp():
    assert classify_company("ASML 코리아", "설비 엔지니어") == "외국계"


def test_classify_mid_corp():
    assert classify_company("오뚜기푸드", "생산관리") == "중견기업"


def test_classify_default():
    assert classify_company("가나다상사", "사무보조") == "일반/기타기업"


def test_analyze_returns_welfare_labels_only():
    """가짜 해시 별점은 제거되었고 복지 라벨만 남는다."""
    result = analyze("삼성전자", "기숙사 제공 생산직")
    assert isinstance(result, list)
    assert "🚌 기숙사/교통" in result


def test_extract_welfares():
    labels = extract_welfares("한국기업", "기숙사 제공, 중식 지원, 성과급 지급")
    assert "🚌 기숙사/교통" in labels
    assert "🍔 식사제공" in labels
    assert "💰 성과급" in labels


# --- 날짜 -------------------------------------------------------------------
def test_parse_deadline_mmdd():
    assert parse_deadline("~ 09/14(월)", TODAY) == date(2026, 9, 14)


def test_parse_deadline_rolls_to_next_year():
    assert parse_deadline("~ 01/05", date(2026, 12, 20)) == date(2027, 1, 5)


def test_parse_deadline_always_open():
    assert parse_deadline("채용시", TODAY) is None


def test_format_dday():
    assert format_dday(date(2026, 9, 4), today=TODAY) == "🔥 D-DAY"
    assert format_dday(date(2026, 9, 14), today=TODAY) == "⏳ D-10"
    assert format_dday(None, "상시채용", TODAY) == "🔁 상시채용"


def test_days_left_negative_for_past():
    assert days_left(date(2026, 9, 1), TODAY) == -3


def test_parse_rss_date():
    assert parse_rss_date("Thu, 04 Sep 2026 09:00:00 +0900") == date(2026, 9, 4)


# --- 블로그 제목 정제 -------------------------------------------------------
def test_clean_title_extracts_company():
    company, title = _clean_title("[대기업] [현대모비스] 생산기술직 채용 ~09/20")
    assert company == "현대모비스"
    assert "생산기술직" in title


# --- HTML 이스케이프 --------------------------------------------------------
def test_job_card_escapes_html():
    html = job_card({"company": "<script>alert(1)</script>", "position": "생산 & 품질", "date": ""})
    assert "<script>" not in html
    assert "&amp;" in html


# --- DB ---------------------------------------------------------------------
def _sample(company="테스트기업", position="생산직 채용"):
    return {
        "source": "사람인",
        "company": company,
        "position": position,
        "date": "⏳ D-10",
        "link": "https://example.com/1",
        "location": "경기 화성시",
        "category": "일반/기타기업",
        "welfares": ["🍔 식사제공"],
        "deadline": "2026-09-14",
    }


def test_db_roundtrip(tmp_path):
    path = str(tmp_path / "t.db")
    db.init_db(path)

    assert db.save_job(_sample(), path) is True
    assert db.save_job(_sample(), path) is False  # 중복 저장 차단

    jobs = db.load_jobs(db_path=path)
    assert len(jobs) == 1
    assert jobs[0]["welfares"] == ["🍔 식사제공"]
    assert jobs[0]["status"] == "관심"

    db.update_job(jobs[0]["id"], db_path=path, status="지원 완료", memo="자소서 제출")
    updated = db.load_jobs(db_path=path)[0]
    assert updated["status"] == "지원 완료"
    assert updated["memo"] == "자소서 제출"
    assert db.status_counts(path)["지원 완료"] == 1

    db.delete_job(updated["id"], db_path=path)
    assert db.load_jobs(db_path=path) == []


def test_db_migrates_legacy_schema(tmp_path):
    """구버전 스키마의 jobs.db가 있어도 데이터를 유지한 채 열려야 한다."""
    import sqlite3

    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE scrapped_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source TEXT, company TEXT, position TEXT, date TEXT, link TEXT)"
    )
    conn.execute(
        "INSERT INTO scrapped_jobs (source, company, position, date, link) "
        "VALUES ('사람인', '옛회사', '옛공고', 'D-3', 'https://example.com')"
    )
    # 중복 행도 하나 넣어 유니크 인덱스 생성 시 정리되는지 확인
    conn.execute(
        "INSERT INTO scrapped_jobs (source, company, position, date, link) "
        "VALUES ('사람인', '옛회사', '옛공고', 'D-3', 'https://example.com')"
    )
    conn.commit()
    conn.close()

    db.init_db(path)
    jobs = db.load_jobs(db_path=path)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "옛회사"
    assert jobs[0]["status"] == "관심"


def test_mark_seen_skips_first_run(tmp_path):
    path = str(tmp_path / "seen.db")
    db.init_db(path)

    # 첫 실행에서는 전부 NEW가 되지 않도록 비워 둔다.
    assert db.mark_seen(["a", "b"], path) == set()
    # 이후 새로 등장한 키만 NEW로 표시된다.
    assert db.mark_seen(["a", "b", "c"], path) == {"c"}
    assert db.mark_seen(["a", "b", "c"], path) == set()


def test_clean_title_leading_company_format():
    from jobhelper.scrapers.naver_blog import detect_tier

    raw = "볼보그룹코리아 기계조립 사원 채용 [창원][9월18일까지]"
    company, title = _clean_title(raw)
    assert company == "볼보그룹코리아"
    assert "9월18일까지" not in title
    assert detect_tier(raw) == "중견기업"


def test_detect_tier_from_tag():
    from jobhelper.scrapers.naver_blog import detect_tier

    assert detect_tier("[대기업] 현대트랜시스 현장기술직 채용") == "대기업"
    assert detect_tier("[알짜중소] 트리아펙스 오퍼레이터채용") == "일반/기타기업"


def test_parse_deadline_korean_format():
    assert parse_deadline("[9월18일까지]", TODAY) == date(2026, 9, 18)
