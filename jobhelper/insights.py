"""수집한 공고에서 시간과 통계가 만들어주는 정보를 뽑아낸다.

세 가지를 다룬다.

1. **장기 게시 감지** — 같은 공고가 몇 주째 올라와 있으면, 사람이 계속 안 붙거나
   오래 못 버티는 자리일 수 있다. 지원 전에 알면 좋은 신호다.
2. **자격증·지역 트렌드** — 지금 시장이 무엇을 요구하는지 집계해, 뭘 준비하고
   어디를 볼지 정하는 데 쓴다.
3. **다수 공고 회사 표시** — 한 회사가 검색 결과에 수십 건씩 올리면 채용대행·
   파견업체인 경우가 많다. 직접고용을 찾는다면 걸러낼 근거가 된다.

추가 API 키가 필요 없다. 이미 수집하는 데이터와 DB만 쓴다.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .storage import connect, upsert

log = logging.getLogger(__name__)

# 며칠 이상 계속 게시되면 눈에 띄게 표시할지
LONG_LISTING_DAYS = 21

# 검색 결과에서 한 회사가 이만큼 넘게 올렸으면 '다수 공고'로 표시
AGENCY_POSTING_THRESHOLD = 8

# 회사명에 이런 단어가 있으면 채용대행/파견일 가능성이 높다 (보조 신호)
AGENCY_NAME_HINTS = [
    "컨설팅", "서치", "아웃소싱", "인력", "잡코리아", "HR", "에이치알",
    "휴먼", "잡매칭", "커리어", "헤드헌", "파견", "도급", "용역",
]

# 생산·기술직 공고에서 실제로 자주 보이는 자격증·기술
TRACKED_SKILLS = [
    "지게차", "위험물", "전기기능사", "용접", "산업안전", "기계정비", "가스",
    "품질경영", "비파괴", "건설기계", "프레스", "크레인", "보일러", "설비보전",
    "CNC", "선반", "밀링", "사출", "금형", "도장", "조립", "검사", "3교대",
    "주야2교대", "기숙사", "통근버스",
]


# ==========================================
# 공고 목격 이력
# ==========================================
def init_insight_tables(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_sightings (
                job_key TEXT PRIMARY KEY,
                company TEXT,
                position TEXT,
                first_seen TEXT,
                last_seen TEXT,
                seen_days INTEGER
            )
            """
        )


def record_sightings(
    jobs: list[dict[str, Any]], today: date | None = None, db_path: str | None = None
) -> None:
    """이번에 본 공고들을 이력에 기록한다. 같은 날 여러 번 봐도 하루로 센다."""
    if not jobs:
        return
    today = today or date.today()
    stamp = today.isoformat()

    with connect(db_path) as conn:
        keys = [j["job_key"] for j in jobs if j.get("job_key")]
        if not keys:
            return

        existing: dict[str, dict[str, Any]] = {}
        # IN 절이 너무 길어지지 않게 나눠서 조회한다.
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT job_key, first_seen, last_seen, seen_days "
                f"FROM job_sightings WHERE job_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                existing[row["job_key"]] = dict(row)

        payload = []
        for job in jobs:
            key = job.get("job_key")
            if not key:
                continue
            prior = existing.get(key)
            if prior is None:
                payload.append((key, job.get("company", ""), job.get("position", ""),
                                stamp, stamp, 1))
            elif prior["last_seen"] != stamp:
                payload.append((key, job.get("company", ""), job.get("position", ""),
                                prior["first_seen"] or stamp, stamp,
                                (prior["seen_days"] or 0) + 1))
            # 같은 날 다시 봤으면 아무것도 하지 않는다

        if payload:
            conn.executemany(
                upsert(
                    "job_sightings",
                    ["job_key", "company", "position", "first_seen", "last_seen", "seen_days"],
                    ["job_key"],
                ),
                payload,
            )


@dataclass
class Sighting:
    job_key: str
    first_seen: str
    last_seen: str
    seen_days: int

    def days_listed(self, today: date | None = None) -> int:
        """처음 본 날부터 오늘까지 며칠째인지."""
        try:
            first = date.fromisoformat(self.first_seen)
        except (ValueError, TypeError):
            return 0
        return max(0, ((today or date.today()) - first).days)

    def is_long_listing(self, today: date | None = None) -> bool:
        return self.days_listed(today) >= LONG_LISTING_DAYS


def load_sightings(
    job_keys: Iterable[str], db_path: str | None = None
) -> dict[str, Sighting]:
    keys = [k for k in dict.fromkeys(job_keys) if k]
    if not keys:
        return {}

    found: dict[str, Sighting] = {}
    with connect(db_path) as conn:
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT job_key, first_seen, last_seen, seen_days "
                f"FROM job_sightings WHERE job_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                found[row["job_key"]] = Sighting(
                    job_key=row["job_key"],
                    first_seen=row["first_seen"] or "",
                    last_seen=row["last_seen"] or "",
                    seen_days=row["seen_days"] or 1,
                )
    return found


def annotate_history(
    jobs: list[dict[str, Any]], today: date | None = None, db_path: str | None = None
) -> list[dict[str, Any]]:
    """각 공고에 게시 기간 정보를 붙인다."""
    sightings = load_sightings((j.get("job_key", "") for j in jobs), db_path)
    for job in jobs:
        sighting = sightings.get(job.get("job_key", ""))
        if not sighting:
            continue
        days = sighting.days_listed(today)
        job["days_listed"] = days
        job["long_listing"] = sighting.is_long_listing(today)
    return jobs


# ==========================================
# 다수 공고 회사 (채용대행·파견 추정)
# ==========================================
def _looks_like_agency(company: str) -> bool:
    return any(hint.lower() in company.lower() for hint in AGENCY_NAME_HINTS)


def annotate_agencies(
    jobs: list[dict[str, Any]], threshold: int = AGENCY_POSTING_THRESHOLD
) -> list[dict[str, Any]]:
    """검색 결과 안에서 한 회사가 올린 공고 수를 세어 표시한다.

    '파견업체다'라고 단정하지 않는다. 공고 수는 사실이고, 판단은 사용자 몫이다.
    """
    counts = Counter(job.get("company", "") for job in jobs if job.get("company"))
    for job in jobs:
        company = job.get("company", "")
        count = counts.get(company, 0)
        job["company_posting_count"] = count
        job["bulk_poster"] = count >= threshold or (
            count >= 3 and _looks_like_agency(company)
        )
    return jobs


def top_bulk_posters(
    jobs: list[dict[str, Any]], threshold: int = AGENCY_POSTING_THRESHOLD, limit: int = 10
) -> list[tuple[str, int]]:
    counts = Counter(job.get("company", "") for job in jobs if job.get("company"))
    return [(c, n) for c, n in counts.most_common(limit) if n >= threshold]


# ==========================================
# 트렌드 집계
# ==========================================
def _job_text(job: dict[str, Any]) -> str:
    return " ".join(
        str(job.get(field, "")) for field in ("position", "sector", "employment", "welfares")
    )


def skill_trends(jobs: list[dict[str, Any]], limit: int = 12) -> list[tuple[str, int]]:
    """공고에 등장한 자격증·기술 빈도. 무엇을 준비할지 정하는 데 쓴다."""
    blob = " ".join(_job_text(job) for job in jobs)
    counts = {skill: blob.count(skill) for skill in TRACKED_SKILLS}
    return [(k, v) for k, v in Counter(counts).most_common(limit) if v]


def location_trends(jobs: list[dict[str, Any]], limit: int = 10) -> list[tuple[str, int]]:
    """지역별 공고 수. `경기 화성시` 처럼 붙어 오기도 해 앞 토큰만 쓴다."""
    counter: Counter[str] = Counter()
    for job in jobs:
        raw = (job.get("location") or "").strip()
        if not raw:
            continue
        counter[raw.split()[0]] += 1
    return counter.most_common(limit)


def employment_trends(jobs: list[dict[str, Any]], limit: int = 8) -> list[tuple[str, int]]:
    counter = Counter(
        (job.get("employment") or "미기재").strip() for job in jobs
    )
    return counter.most_common(limit)


def career_trends(jobs: list[dict[str, Any]], limit: int = 8) -> list[tuple[str, int]]:
    counter = Counter((job.get("career") or "미기재").strip() for job in jobs)
    return counter.most_common(limit)


@dataclass
class Summary:
    total: int = 0
    long_listings: int = 0
    bulk_poster_jobs: int = 0
    tracked_history: int = 0

    @property
    def direct_hire_estimate(self) -> int:
        """다수 공고 회사를 뺀 건수 (직접고용 추정)."""
        return max(0, self.total - self.bulk_poster_jobs)


def summarize(jobs: list[dict[str, Any]]) -> Summary:
    return Summary(
        total=len(jobs),
        long_listings=sum(1 for j in jobs if j.get("long_listing")),
        bulk_poster_jobs=sum(1 for j in jobs if j.get("bulk_poster")),
        tracked_history=sum(1 for j in jobs if j.get("days_listed") is not None),
    )
