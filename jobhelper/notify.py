"""마감 임박 공고 알림.

보관함에 담아둔 공고 중 마감이 가까운 것을 텔레그램/이메일로 보낸다.
앱을 열지 않아도 되도록 ``python notify.py`` 로 따로 실행할 수 있고,
같은 공고를 매일 중복 발송하지 않도록 보낸 기록을 남긴다.
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import date
from email.message import EmailMessage
from typing import Any

import requests

from . import settings
from .config import REQUEST_TIMEOUT
from .db import connect, init_db, load_jobs
from .dates import days_left, now_iso, parse_deadline

log = logging.getLogger(__name__)

# 마감된 공고나 이미 결과가 나온 공고는 알릴 필요가 없다.
SKIP_STATUSES = {"최종 합격", "불합격"}


def init_alert_log(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_log (
                job_id INTEGER,
                alert_date TEXT,
                PRIMARY KEY (job_id, alert_date)
            )
            """
        )


def _already_sent(job_id: int, today: date, db_path: str | None = None) -> bool:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM alert_log WHERE job_id = ? AND alert_date = ?",
            (job_id, today.isoformat()),
        ).fetchone()
    return row is not None


def _mark_sent(job_ids: list[int], today: date, db_path: str | None = None) -> None:
    if not job_ids:
        return
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_log (job_id, alert_date) VALUES (?, ?)",
            [(jid, today.isoformat()) for jid in job_ids],
        )


def find_urgent_jobs(
    within_days: int = 3, today: date | None = None, db_path: str | None = None
) -> list[dict[str, Any]]:
    """마감이 ``within_days`` 일 이내로 남은 보관 공고를 찾는다."""
    today = today or date.today()
    urgent = []
    for job in load_jobs(db_path=db_path):
        if job.get("status") in SKIP_STATUSES:
            continue
        deadline = parse_deadline(job.get("deadline", ""), today)
        left = days_left(deadline, today)
        if left is None or left < 0 or left > within_days:
            continue
        job["days_left"] = left
        urgent.append(job)
    urgent.sort(key=lambda j: j["days_left"])
    return urgent


def build_message(jobs: list[dict[str, Any]], today: date | None = None) -> str:
    today = today or date.today()
    lines = [f"⏰ 마감 임박 공고 {len(jobs)}건 ({today.month}/{today.day} 기준)", ""]
    for job in jobs:
        left = job["days_left"]
        dday = "오늘 마감" if left == 0 else f"D-{left}"
        lines.append(f"• [{dday}] {job['company']} — {job['position']}")
        if job.get("status"):
            lines.append(f"  상태: {job['status']}")
        if job.get("link"):
            lines.append(f"  {job['link']}")
        lines.append("")
    return "\n".join(lines).strip()


# --- 발송 채널 --------------------------------------------------------------
def send_telegram(text: str) -> bool:
    conf = settings.telegram_config()
    if not conf:
        return False
    token, chat_id = conf
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.error("텔레그램 발송 실패: %s", exc)
        return False
    return True


def send_email(text: str, subject: str = "마감 임박 채용 공고") -> bool:
    conf = settings.email_config()
    if not conf:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = conf["user"]
    message["To"] = conf["to"]
    message.set_content(text)

    try:
        with smtplib.SMTP(conf["host"], int(conf["port"]), timeout=20) as server:
            server.starttls()
            server.login(conf["user"], conf["password"])
            server.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        log.error("메일 발송 실패: %s", exc)
        return False
    return True


def run(
    within_days: int = 3,
    today: date | None = None,
    db_path: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """알림 한 번 실행. 결과 요약을 딕셔너리로 돌려준다."""
    today = today or date.today()
    init_db(db_path)
    init_alert_log(db_path)

    candidates = find_urgent_jobs(within_days, today, db_path)
    jobs = [j for j in candidates if not _already_sent(j["id"], today, db_path)]

    result: dict[str, Any] = {
        "found": len(candidates),
        "to_send": len(jobs),
        "channels": [],
        "sent": False,
    }
    if not jobs:
        return result

    text = build_message(jobs, today)
    result["message"] = text
    if dry_run:
        return result

    if send_telegram(text):
        result["channels"].append("telegram")
    if send_email(text):
        result["channels"].append("email")

    if result["channels"]:
        _mark_sent([j["id"] for j in jobs], today, db_path)
        result["sent"] = True
    else:
        log.warning("발송 채널이 설정되지 않아 알림을 보내지 못했습니다.")
    return result
