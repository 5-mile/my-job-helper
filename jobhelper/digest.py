"""매일 아침 새 공고 요약.

원하는 지역의 공고를 모아, 프로필과 동떨어진 것을 걸러내고, 경력과 가까운 순으로
몇 건만 텔레그램으로 보낸다. 한 번 보낸 공고는 다시 보내지 않는다.

키워드로 거르는 1차 선별이다. 본문을 읽고 하는 깊은 채점은 Claude Code의 /rank 몫이다.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from . import settings
from .dates import now_iso
from .notify import send_telegram
from .scrapers.saramin import fetch_saramin_jobs_detailed
from .storage import connect, insert_or_ignore

log = logging.getLogger(__name__)

DEFAULT_REGIONS = "음성,진천,충주,증평,괴산,청주,이천,안성"
DEFAULT_KEYWORDS = "음성 생산,진천 생산,충주 생산,이차전지,화학 생산,설비OP,생산직"
DEFAULT_LIMIT = 5

# 초대졸이라 4년제 이상 필수는 지원 자체가 안 된다.
_EDU_BLOCK = re.compile(r"^대졸|석사|박사")
_OFF = re.compile(
    r"연구|R&D|임원|영업|마케팅|사무|회계|인사|총무|MES|소프트웨어|디자인|설계|"
    r"운전기사|배송|택배|요양|간호|조리|영양|미화|경비|상담"
)
_ON = re.compile(r"생산|OP|오퍼레이터|Operator|운전원|설비|공정|제조|화학|이차전지|배터리|보전|정비|유틸|품질|지게차|자재")
_TEMP = re.compile(r"아르바이트|단기")
# 경력(리튬 정제·화학 공정·설비 보전·지게차)과 가까울수록 높게.
_WEIGHTS = [
    (re.compile(r"이차전지|2차전지|배터리|양극|음극|전해|리튬"), 5),
    (re.compile(r"화학|케미|켐|정밀화학|합성|소재|정제"), 4),
    (re.compile(r"설비|보전|정비|유지보수|유틸"), 3),
    (re.compile(r"OP|오퍼레이터|Operator|운전원|조작원"), 2),
    (re.compile(r"정규직"), 1),
    (re.compile(r"지게차|자재"), 1),
]


def _csv(name: str, default: str) -> list[str]:
    return [x.strip() for x in (settings.get(name) or default).split(",") if x.strip()]


def job_key(job: dict[str, Any]) -> str:
    m = re.search(r"rec_idx=(\d+)", job.get("link") or "")
    return f"saramin:{m.group(1)}" if m else f"{job.get('company')}|{job.get('position')}"


def short_link(job: dict[str, Any]) -> str:
    """검색 추적 꼬리표를 떼고 공고 번호만 남긴다 (메시지가 링크로 도배되지 않게)."""
    m = re.search(r"rec_idx=(\d+)", job.get("link") or "")
    return f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={m.group(1)}" if m else (job.get("link") or "")


def score(job: dict[str, Any]) -> int:
    text = f"{job.get('position') or ''} {job.get('sector') or ''} {job.get('employment') or ''}"
    s = sum(w for pattern, w in _WEIGHTS if pattern.search(text))
    return s - 2 if "파견" in (job.get("employment") or "") else s


def select(jobs: list[dict[str, Any]], regions: list[str], today: date) -> list[dict[str, Any]]:
    """지역·학력·직무·마감으로 거르고 점수순으로 정렬한다 (중복 제거 포함)."""
    picked, seen = [], set()
    for job in jobs:
        key = job_key(job)
        if key in seen:
            continue
        seen.add(key)
        position = job.get("position") or ""
        location = job.get("location") or ""
        if not any(r in location for r in regions):
            continue
        # "충북전체,진천군,청주시,…" 처럼 지역을 잔뜩 걸어둔 건 대개 알선업체 공고고,
        # 실제 근무지가 딴 곳인 경우가 많다 (예: 천안 공장을 충북 전역에 노출).
        if "전체" in location or location.count(",") >= 3:
            continue
        if _EDU_BLOCK.search(job.get("education") or ""):
            continue
        if _OFF.search(position) or _TEMP.search(position + (job.get("employment") or "")):
            continue
        if not _ON.search(f"{position} {job.get('sector') or ''}"):
            continue
        deadline = job.get("deadline") or ""
        if deadline and deadline < today.isoformat():
            continue
        picked.append({**job, "score": score(job), "key": key})
    picked.sort(key=lambda j: -j["score"])
    return picked


def init_digest_log(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS digest_sent (job_key TEXT PRIMARY KEY, sent_at TEXT)"
        )


def _unsent(jobs: list[dict[str, Any]], db_path: str | None = None) -> list[dict[str, Any]]:
    if not jobs:
        return []
    with connect(db_path) as conn:
        sent = {r["job_key"] for r in conn.execute("SELECT job_key FROM digest_sent").fetchall()}
    return [j for j in jobs if j["key"] not in sent]


def _mark_sent(keys: list[str], db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executemany(
            insert_or_ignore("digest_sent", ["job_key", "sent_at"], ["job_key"]),
            [(k, now_iso()) for k in keys],
        )


def build_message(jobs: list[dict[str, Any]], today: date) -> str:
    lines = [f"📋 새 공고 {len(jobs)}건 ({today.month}/{today.day})", ""]
    for n, j in enumerate(jobs, 1):
        location = (j.get("location") or "").split(",")[0]
        cond = " · ".join(x for x in (j.get("career"), j.get("education"), j.get("employment")) if x)
        lines += [
            f"{n}. {j.get('company')} · {location}",
            f"   {j.get('position')}",
            f"   {cond} · 마감 {j.get('deadline') or '상시/상세 확인'}",
            f"   {short_link(j)}",
            "",
        ]
    lines.append("자세히 보려면 Claude Code에서 /rank 또는 /apply <주소>")
    return "\n".join(lines)


def run(
    limit: int | None = None,
    dry_run: bool = False,
    today: date | None = None,
    db_path: str | None = None,
    fetch=fetch_saramin_jobs_detailed,
) -> dict[str, Any]:
    today = today or date.today()
    limit = limit or int(settings.get("DIGEST_LIMIT") or DEFAULT_LIMIT)
    init_digest_log(db_path)

    jobs, diagnostics = fetch(_csv("DIGEST_KEYWORDS", DEFAULT_KEYWORDS), "relation", 2, [])
    if diagnostics.warning:
        log.warning(diagnostics.warning)

    fresh = _unsent(select(jobs, _csv("DIGEST_REGIONS", DEFAULT_REGIONS), today), db_path)[:limit]
    result: dict[str, Any] = {"collected": len(jobs), "new": len(fresh), "sent": False}
    if not fresh:
        return result

    result["message"] = build_message(fresh, today)
    if dry_run:
        return result
    if send_telegram(result["message"]):
        _mark_sent([j["key"] for j in fresh], db_path)
        result["sent"] = True
    return result
