"""매일 아침 새 공고 요약.

원하는 지역의 공고를 모아, 프로필과 동떨어진 것을 걸러내고, 경력과 가까운 순으로
몇 건만 텔레그램으로 보낸다. 한 번 보낸 공고는 다시 보내지 않는다.

키워드로 거르는 1차 선별이다. 본문을 읽고 하는 깊은 채점은 Claude Code의 /rank 몫이다.
"""

from __future__ import annotations

import json
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

# 갈 수 있는 범위: 경기·충청·경북 (그 안의 대전·세종·대구 포함). 근무지 표기에 이 글자가 있어야 한다.
DEFAULT_REGIONS = "경기,충북,충남,대전,세종,경북,대구"
# 범위 안에서도 먼저 보고 싶은 곳. 점수를 올려 위로 보낸다.
DEFAULT_PREFERRED = "음성,진천,충주,증평,괴산,청주,이천,안성"
PREFERRED_BONUS = 2
DEFAULT_KEYWORDS = "음성 생산,진천 생산,충주 생산,이차전지,화학 생산,설비OP,생산직"
DEFAULT_LIMIT = 5

# 초대졸이라 4년제 이상 필수는 지원 자체가 안 된다.
_EDU_BLOCK = re.compile(r"^대졸|석사|박사")
_OFF = re.compile(
    r"연구|R&D|임원|영업|마케팅|사무|회계|인사|총무|MES|소프트웨어|디자인|설계|"
    r"운전기사|배송|택배|요양|간호|조리|영양|미화|경비|상담|"
    # 공무(설비보전·정비)는 지원하지 않기로 했다. 설비'OP'(운전)는 생산이라 남긴다.
    r"공무|보전|정비|유지보수"
)
_ON = re.compile(r"생산|OP|오퍼레이터|Operator|운전원|설비|공정|제조|화학|이차전지|배터리|보전|정비|유틸|품질|지게차|자재")
_TEMP = re.compile(r"아르바이트|단기")
# 경력(리튬 정제·화학 공정·설비 보전·지게차)과 가까울수록 높게.
_WEIGHTS = [
    (re.compile(r"이차전지|2차전지|배터리|양극|음극|전해|리튬"), 5),
    (re.compile(r"화학|케미|켐|정밀화학|합성|소재|정제"), 4),
    (re.compile(r"설비|유틸"), 3),
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


def select(
    jobs: list[dict[str, Any]], regions: list[str], today: date, preferred: list[str] = ()
) -> list[dict[str, Any]]:
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
        bonus = PREFERRED_BONUS if any(p in location for p in preferred) else 0
        picked.append({**job, "score": score(job) + bonus, "key": key})
    picked.sort(key=lambda j: -j["score"])
    return picked


# 텔레그램 봇(supabase/functions/telegram-bot)이 버튼 처리에 쓰는 필드. 봇은 이 JSON만 본다.
POOL_FIELDS = (
    "company", "position", "location", "career", "education", "employment",
    "deadline", "date", "category", "sector",
)


def init_digest_pool(db_path: str | None = None) -> None:
    """골라 둔 후보를 쌓아 두는 곳. 매일 보내고 남은 건 봇의 '다음 5건' 으로 꺼낸다."""
    with connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS digest_pool ("
            "job_key TEXT PRIMARY KEY, data TEXT, score INTEGER, deadline TEXT, "
            "collected_at TEXT, sent_at TEXT)"
        )
        # 예전 버전이 남긴 발송 기록을 옮겨, 이미 받은 공고가 다시 오지 않게 한다.
        try:
            old = conn.execute("SELECT job_key, sent_at FROM digest_sent").fetchall()
        except Exception:
            old = []
    if old:
        with connect(db_path) as conn:
            conn.executemany(
                insert_or_ignore("digest_pool", ["job_key", "data", "score", "deadline", "collected_at", "sent_at"], ["job_key"]),
                [(r["job_key"], "{}", 0, "", r["sent_at"], r["sent_at"]) for r in old],
            )


def _add_to_pool(jobs: list[dict[str, Any]], db_path: str | None = None) -> None:
    """새 후보만 넣는다. 이미 있는 건 그대로 두어 보낸 기록이 지워지지 않게 한다."""
    if not jobs:
        return
    stamp = now_iso()
    rows = []
    for j in jobs:
        data = {k: j.get(k) or "" for k in POOL_FIELDS}
        data["link"] = short_link(j)
        rows.append((j["key"], json.dumps(data, ensure_ascii=False), j["score"], j.get("deadline") or "", stamp, None))
    with connect(db_path) as conn:
        conn.executemany(
            insert_or_ignore("digest_pool", ["job_key", "data", "score", "deadline", "collected_at", "sent_at"], ["job_key"]),
            rows,
        )


def next_batch(limit: int, today: date, db_path: str | None = None) -> list[dict[str, Any]]:
    """아직 안 보낸 후보 중 점수 높은 순. 마감 지난 건 뺀다."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT job_key, data FROM digest_pool WHERE sent_at IS NULL "
            "AND (deadline = '' OR deadline >= ?) ORDER BY score DESC, collected_at DESC LIMIT ?",
            (today.isoformat(), limit),
        ).fetchall()
    return [{**json.loads(r["data"]), "key": r["job_key"]} for r in rows]


def _mark_sent(keys: list[str], db_path: str | None = None) -> None:
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.executemany("UPDATE digest_pool SET sent_at = ? WHERE job_key = ?", [(stamp, k) for k in keys])


def keyboard(jobs: list[dict[str, Any]]) -> dict:
    """공고별 ⭐ 버튼(보관함 저장) + 다음 5건. callback_data 는 64바이트 제한이라 키만 싣는다."""
    return {
        "inline_keyboard": [
            [{"text": f"⭐{n}", "callback_data": f"s:{j['key']}"} for n, j in enumerate(jobs, 1)],
            [{"text": "다음 5건 ▶", "callback_data": "m"}],
        ]
    }


def build_message(jobs: list[dict[str, Any]], title: str) -> str:
    lines = [f"📋 {title} {len(jobs)}건", ""]
    for n, j in enumerate(jobs, 1):
        location = (j.get("location") or "").split(",")[0]
        cond = " · ".join(x for x in (j.get("career"), j.get("education"), j.get("employment")) if x)
        lines += [
            f"{n}. {j.get('company')} · {location}",
            f"   {j.get('position')}",
            f"   {cond} · 마감 {j.get('deadline') or '상시/상세 확인'}",
            f"   {j.get('link') or short_link(j)}",
            "",
        ]
    lines.append("⭐ 번호를 누르면 보관함에 저장됩니다.")
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
    init_digest_pool(db_path)

    jobs, diagnostics = fetch(_csv("DIGEST_KEYWORDS", DEFAULT_KEYWORDS), "relation", 2, [])
    if diagnostics.warning:
        log.warning(diagnostics.warning)

    # ponytail: 풀은 지우지 않고 쌓인다 (하루 수십 건, 행당 수백 바이트). 커지면 오래된 sent 행 정리.
    _add_to_pool(select(jobs, _csv("DIGEST_REGIONS", DEFAULT_REGIONS), today, _csv("DIGEST_PREFERRED", DEFAULT_PREFERRED)), db_path)
    fresh = next_batch(limit, today, db_path)
    result: dict[str, Any] = {"collected": len(jobs), "new": len(fresh), "sent": False}
    if not fresh:
        return result

    result["message"] = build_message(fresh, f"새 공고 ({today.month}/{today.day})")
    if dry_run:
        return result
    if send_telegram(result["message"], keyboard(fresh)):
        _mark_sent([j["key"] for j in fresh], db_path)
        result["sent"] = True
    return result
