"""공고 이력·트렌드·다수 공고 회사 감지 테스트."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import insights  # noqa: E402

TODAY = date(2026, 9, 30)


def _job(key: str, company: str = "테스트산업", **extra):
    base = {
        "job_key": key,
        "company": company,
        "position": f"{company} 생산직 채용",
        "sector": "",
        "location": "경기 화성시",
        "employment": "정규직",
        "career": "신입",
        "welfares": [],
    }
    base.update(extra)
    return base


# ==========================================
# 목격 이력
# ==========================================
def test_first_sighting_recorded(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)

    insights.record_sightings([_job("a")], TODAY, path)
    found = insights.load_sightings(["a"], path)

    assert found["a"].first_seen == TODAY.isoformat()
    assert found["a"].seen_days == 1


def test_same_day_sighting_counted_once(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)

    insights.record_sightings([_job("a")], TODAY, path)
    insights.record_sightings([_job("a")], TODAY, path)
    insights.record_sightings([_job("a")], TODAY, path)

    assert insights.load_sightings(["a"], path)["a"].seen_days == 1


def test_later_day_increments_and_keeps_first_seen(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)

    first = TODAY - timedelta(days=10)
    insights.record_sightings([_job("a")], first, path)
    insights.record_sightings([_job("a")], TODAY, path)

    sighting = insights.load_sightings(["a"], path)["a"]
    assert sighting.first_seen == first.isoformat()
    assert sighting.last_seen == TODAY.isoformat()
    assert sighting.seen_days == 2
    assert sighting.days_listed(TODAY) == 10


def test_long_listing_threshold(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)

    old = TODAY - timedelta(days=insights.LONG_LISTING_DAYS)
    fresh = TODAY - timedelta(days=3)
    insights.record_sightings([_job("old")], old, path)
    insights.record_sightings([_job("fresh")], fresh, path)

    found = insights.load_sightings(["old", "fresh"], path)
    assert found["old"].is_long_listing(TODAY) is True
    assert found["fresh"].is_long_listing(TODAY) is False


def test_annotate_history_adds_fields(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)
    insights.record_sightings([_job("a")], TODAY - timedelta(days=30), path)

    jobs = insights.annotate_history([_job("a")], TODAY, path)
    assert jobs[0]["days_listed"] == 30
    assert jobs[0]["long_listing"] is True


def test_annotate_history_leaves_unknown_jobs_alone(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)

    jobs = insights.annotate_history([_job("never-seen")], TODAY, path)
    assert "days_listed" not in jobs[0]


def test_record_sightings_handles_empty(tmp_path):
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)
    insights.record_sightings([], TODAY, path)  # 터지지 않아야 한다
    assert insights.load_sightings([], path) == {}


def test_record_sightings_chunks_large_batches(tmp_path):
    """IN 절이 너무 길어지지 않게 나눠 조회하는지 (SQLite 변수 상한)."""
    path = str(tmp_path / "i.db")
    insights.init_insight_tables(path)

    many = [_job(f"k{i}") for i in range(1200)]
    insights.record_sightings(many, TODAY, path)
    found = insights.load_sightings([j["job_key"] for j in many], path)
    assert len(found) == 1200


# ==========================================
# 다수 공고 회사
# ==========================================
def test_bulk_poster_flagged_by_count():
    jobs = [_job(f"k{i}", "에이치알포유") for i in range(10)] + [_job("x", "평범한회사")]
    annotated = insights.annotate_agencies(jobs, threshold=8)

    bulk = [j for j in annotated if j["bulk_poster"]]
    assert len(bulk) == 10
    assert bulk[0]["company_posting_count"] == 10

    normal = [j for j in annotated if j["company"] == "평범한회사"][0]
    assert normal["bulk_poster"] is False


def test_agency_name_hint_lowers_threshold():
    """이름에 '컨설팅' 등이 들어가면 3건만 되어도 표시한다."""
    jobs = [_job(f"k{i}", "휴머레인컨설팅") for i in range(3)]
    annotated = insights.annotate_agencies(jobs, threshold=8)
    assert all(j["bulk_poster"] for j in annotated)


def test_agency_name_hint_needs_multiple_postings():
    jobs = [_job("k0", "휴머레인컨설팅")]
    annotated = insights.annotate_agencies(jobs, threshold=8)
    assert annotated[0]["bulk_poster"] is False


def test_top_bulk_posters_sorted():
    jobs = (
        [_job(f"a{i}", "많이올린곳") for i in range(12)]
        + [_job(f"b{i}", "조금올린곳") for i in range(9)]
        + [_job("c", "한번만")]
    )
    rows = insights.top_bulk_posters(jobs, threshold=8)
    assert rows[0] == ("많이올린곳", 12)
    assert ("한번만", 1) not in rows


# ==========================================
# 트렌드
# ==========================================
def test_skill_trends_counts_keywords():
    jobs = [
        _job("a", position="지게차 운전원 채용", sector="물류"),
        _job("b", position="용접 기능공 모집", sector="지게차 가능자 우대"),
    ]
    rows = dict(insights.skill_trends(jobs))
    assert rows["지게차"] == 2
    assert rows["용접"] == 1
    assert "크레인" not in rows  # 0건은 빼고 돌려준다


def test_location_trends_uses_first_token():
    jobs = [_job("a", location="경기 화성시"), _job("b", location="경기 평택시"),
            _job("c", location="충북 청주시")]
    rows = dict(insights.location_trends(jobs))
    assert rows["경기"] == 2
    assert rows["충북"] == 1


def test_location_trends_skips_blank():
    assert insights.location_trends([_job("a", location="")]) == []


def test_employment_and_career_trends():
    jobs = [_job("a", employment="정규직", career="신입"),
            _job("b", employment="계약직", career="신입"),
            _job("c", employment="", career="")]
    assert dict(insights.employment_trends(jobs))["정규직"] == 1
    assert dict(insights.employment_trends(jobs))["미기재"] == 1
    assert dict(insights.career_trends(jobs))["신입"] == 2


# ==========================================
# 요약
# ==========================================
def test_summary_counts():
    jobs = [
        _job("a", long_listing=True, days_listed=30, bulk_poster=True),
        _job("b", long_listing=False, days_listed=2, bulk_poster=False),
        _job("c"),
    ]
    summary = insights.summarize(jobs)
    assert summary.total == 3
    assert summary.long_listings == 1
    assert summary.bulk_poster_jobs == 1
    assert summary.direct_hire_estimate == 2


def test_summary_of_empty_list():
    summary = insights.summarize([])
    assert summary.total == 0
    assert summary.direct_hire_estimate == 0


# ==========================================
# UI
# ==========================================
def test_trend_bars_render():
    from jobhelper.ui import trend_bars

    html = trend_bars([("용접", 18), ("가스", 9)])
    assert "용접" in html and "18" in html
    assert "width:100%" in html  # 최대값은 꽉 찬 막대
    assert "width:50%" in html


def test_trend_bars_empty():
    from jobhelper.ui import trend_bars

    assert trend_bars([]) == ""


def test_trend_bars_escapes_labels():
    from jobhelper.ui import trend_bars

    assert "<script>" not in trend_bars([("<script>", 1)])


def test_card_shows_insight_badges():
    from jobhelper.ui import job_card

    html = job_card({
        "company": "A", "position": "B", "date": "",
        "long_listing": True, "days_listed": 45,
        "bulk_poster": True, "company_posting_count": 35,
    })
    assert "45일째 게시" in html
    assert "이 회사 공고 35건" in html


def test_card_without_insights_has_no_badges():
    from jobhelper.ui import job_card

    html = job_card({"company": "A", "position": "B", "date": ""})
    assert "일째 게시" not in html
    assert "이 회사 공고" not in html
