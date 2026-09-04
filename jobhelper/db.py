"""스크랩 보관함 + 지원 현황 트래커용 SQLite 레이어.

기존 버전의 ``scrapped_jobs`` 테이블을 그대로 이어받아 컬럼만 덧붙이므로
예전 jobs.db 파일이 있어도 데이터를 잃지 않는다.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable

from .config import APPLICATION_STATUSES
from .dates import now_iso

DB_PATH = os.environ.get(
    "JOB_HELPER_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs.db"),
)

# 기존 테이블에 없던 컬럼들 (이름, DDL 타입/기본값)
_EXTRA_COLUMNS = [
    ("location", "TEXT DEFAULT ''"),
    ("category", "TEXT DEFAULT ''"),
    ("rating", "REAL DEFAULT 3.0"),
    ("welfares", "TEXT DEFAULT ''"),
    ("deadline", "TEXT DEFAULT ''"),
    ("status", "TEXT DEFAULT '관심'"),
    ("memo", "TEXT DEFAULT ''"),
    ("applied_at", "TEXT DEFAULT ''"),
    ("created_at", "TEXT DEFAULT ''"),
]


@contextmanager
def connect(db_path: str | None = None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """테이블을 만들고, 구버전 DB라면 부족한 컬럼을 채워 넣는다."""
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS scrapped_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                company TEXT,
                position TEXT,
                date TEXT,
                link TEXT
            )
            """
        )
        existing = {row["name"] for row in cur.execute("PRAGMA table_info(scrapped_jobs)")}
        for name, ddl in _EXTRA_COLUMNS:
            if name not in existing:
                cur.execute(f"ALTER TABLE scrapped_jobs ADD COLUMN {name} {ddl}")

        # 같은 (회사, 공고)가 두 번 저장되지 않도록 정리 후 유니크 인덱스 부여
        cur.execute(
            """
            DELETE FROM scrapped_jobs
            WHERE id NOT IN (
                SELECT MIN(id) FROM scrapped_jobs GROUP BY company, position
            )
            """
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_unique "
            "ON scrapped_jobs (company, position)"
        )

        # 이미 본 공고를 기억해 두었다가 새 공고에 NEW 뱃지를 붙이는 데 쓴다.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_jobs (
                job_key TEXT PRIMARY KEY,
                first_seen TEXT
            )
            """
        )


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job["welfares"] = [w for w in (job.get("welfares") or "").split("|") if w]
    job["rating"] = job.get("rating") or 3.0
    job["status"] = job.get("status") or APPLICATION_STATUSES[0]
    return job


def save_job(job: dict[str, Any], db_path: str | None = None) -> bool:
    """공고를 보관함에 넣는다. 이미 있으면 False."""
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO scrapped_jobs
                (source, company, position, date, link, location, category,
                 rating, welfares, deadline, status, memo, applied_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.get("source", ""),
                job.get("company", ""),
                job.get("position", ""),
                job.get("date", ""),
                job.get("link", ""),
                job.get("location", ""),
                job.get("category", ""),
                float(job.get("rating") or 3.0),
                "|".join(job.get("welfares") or []),
                job.get("deadline", ""),
                job.get("status", APPLICATION_STATUSES[0]),
                job.get("memo", ""),
                job.get("applied_at", ""),
                now_iso(),
            ),
        )
        return cur.rowcount > 0


def load_jobs(status: str | None = None, db_path: str | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        cur = conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM scrapped_jobs WHERE status = ? ORDER BY id DESC", (status,)
            )
        else:
            cur.execute("SELECT * FROM scrapped_jobs ORDER BY id DESC")
        return [_row_to_job(r) for r in cur.fetchall()]


def update_job(job_id: int, db_path: str | None = None, **fields: Any) -> None:
    """상태·메모·지원일 등을 갱신한다."""
    allowed = {name for name, _ in _EXTRA_COLUMNS}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE scrapped_jobs SET {assignments} WHERE id = ?",
            (*updates.values(), job_id),
        )


def delete_job(job_id: int, db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM scrapped_jobs WHERE id = ?", (job_id,))


def saved_keys(db_path: str | None = None) -> set[str]:
    """이미 보관함에 있는 공고의 ``회사::공고명`` 키 집합."""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT company, position FROM scrapped_jobs").fetchall()
    return {f"{r['company']}::{r['position']}" for r in rows}


def status_counts(db_path: str | None = None) -> dict[str, int]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM scrapped_jobs GROUP BY status"
        ).fetchall()
    counts = {s: 0 for s in APPLICATION_STATUSES}
    for row in rows:
        counts[row["status"] or APPLICATION_STATUSES[0]] = row["n"]
    return counts


def mark_seen(job_keys: Iterable[str], db_path: str | None = None) -> set[str]:
    """처음 보는 공고 키를 기록하고, 그중 '이번에 새로 등장한' 키만 돌려준다."""
    keys = list(dict.fromkeys(job_keys))
    if not keys:
        return set()
    with connect(db_path) as conn:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(keys))
        known = {
            row["job_key"]
            for row in cur.execute(
                f"SELECT job_key FROM seen_jobs WHERE job_key IN ({placeholders})", keys
            )
        }
        fresh = [k for k in keys if k not in known]
        if fresh:
            stamp = now_iso()
            cur.executemany(
                "INSERT OR IGNORE INTO seen_jobs (job_key, first_seen) VALUES (?, ?)",
                [(k, stamp) for k in fresh],
            )
        # 보관함이 비어 있는 첫 실행에서는 전부 NEW가 되어버리므로 그때는 표시하지 않는다.
        total_seen = cur.execute("SELECT COUNT(*) AS n FROM seen_jobs").fetchone()["n"]
    if total_seen == len(fresh):
        return set()
    return set(fresh)
