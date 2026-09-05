"""보관함 이전 테스트.

대상 쪽은 test_storage.py 와 같은 방식으로 Postgres 분기를 타되 SQLite에
저장하는 가짜 연결을 쓴다. 즉 실제로 ON CONFLICT 구문이 오간다.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import db, migrate as migrate_mod, storage  # noqa: E402
from jobhelper.migrate import migrate, read_source  # noqa: E402
from tests.test_storage import _FakePgConnection  # noqa: E402


def _make_source(path: str, jobs: list[tuple], *, legacy: bool = False) -> None:
    """원본 SQLite를 만든다. legacy=True면 구버전(컬럼 적은) 스키마."""
    conn = sqlite3.connect(path)
    if legacy:
        conn.execute(
            "CREATE TABLE scrapped_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT, company TEXT, position TEXT, date TEXT, link TEXT)"
        )
        conn.executemany(
            "INSERT INTO scrapped_jobs (source, company, position, date, link) "
            "VALUES (?, ?, ?, ?, ?)",
            [(j[0], j[1], j[2], "", "") for j in jobs],
        )
    else:
        conn.execute(
            "CREATE TABLE scrapped_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT, company TEXT, position TEXT, date TEXT, link TEXT, "
            "location TEXT, category TEXT, rating REAL, welfares TEXT, deadline TEXT, "
            "status TEXT, memo TEXT, applied_at TEXT, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO scrapped_jobs (source, company, position, date, link, location, "
            "category, rating, welfares, deadline, status, memo, applied_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (src, company, position, "", "https://example.com", "경기", "대기업",
                 3.0, "", "2026-09-20", status, memo, "", "")
                for src, company, position, status, memo in jobs
            ],
        )
        conn.execute("CREATE TABLE seen_jobs (job_key TEXT PRIMARY KEY, first_seen TEXT)")
        conn.executemany(
            "INSERT INTO seen_jobs (job_key, first_seen) VALUES (?, ?)",
            [("saramin:1", "2026-09-01"), ("saramin:2", "2026-09-02")],
        )
        conn.execute(
            "CREATE TABLE company_info_cache (company TEXT PRIMARY KEY, employees INTEGER, "
            "avg_monthly_pay INTEGER, address TEXT, joined_this_month INTEGER, "
            "left_this_month INTEGER, data_month TEXT, found INTEGER, fetched_at TEXT)"
        )
        conn.execute(
            "INSERT INTO company_info_cache VALUES ('삼성전자', 1000, 5000000, '경기', "
            "10, 5, '202608', 1, '2026-09-01')"
        )
    conn.commit()
    conn.close()


SAMPLE = [
    ("사람인", "가나기업", "생산직", "지원 완료", "자소서 제출함"),
    ("사람인", "다라기업", "품질관리", "면접 진행", "1차 면접 9/10"),
    ("블로그", "마바기업", "설비보전", "관심", ""),
]


@pytest.fixture
def target(tmp_path, monkeypatch):
    """Postgres 분기를 타는 대상 저장소."""
    conn = _FakePgConnection(str(tmp_path / "target.db"), [])
    monkeypatch.setattr(storage, "is_postgres", lambda: True)
    monkeypatch.setattr(storage, "_connect_postgres", lambda: conn)
    return conn


# --- 원본 읽기 ---------------------------------------------------------------
def test_read_source_collects_all_tables(tmp_path):
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    data = read_source(path)
    assert set(data) == {"scrapped_jobs", "seen_jobs", "company_info_cache"}

    columns, rows = data["scrapped_jobs"]
    assert "id" not in columns  # id는 대상에서 새로 부여한다
    assert len(rows) == 3


def test_read_source_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_source(str(tmp_path / "없는파일.db"))


def test_read_source_handles_legacy_schema(tmp_path):
    """구버전 스키마(컬럼 적음)도 있는 컬럼만 읽어야 한다."""
    path = str(tmp_path / "legacy.db")
    _make_source(path, SAMPLE, legacy=True)

    data = read_source(path)
    columns, rows = data["scrapped_jobs"]
    assert "status" not in columns
    assert len(rows) == 3
    assert "seen_jobs" not in data  # 구버전엔 없는 표


# --- 이전 ---------------------------------------------------------------------
def test_migrate_moves_jobs_with_memo_and_status(tmp_path, target):
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    result = migrate(path)
    assert result.ok
    assert result.total_written == 3 + 2 + 1  # 공고 3 + seen 2 + 회사캐시 1

    moved = db.load_jobs()
    assert len(moved) == 3

    by_company = {j["company"]: j for j in moved}
    assert by_company["가나기업"]["status"] == "지원 완료"
    assert by_company["가나기업"]["memo"] == "자소서 제출함"
    assert by_company["다라기업"]["memo"] == "1차 면접 9/10"
    assert by_company["마바기업"]["status"] == "관심"


def test_migrate_is_idempotent(tmp_path, target):
    """두 번 돌려도 중복이 생기지 않아야 한다."""
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    migrate(path)
    second = migrate(path)

    assert len(db.load_jobs()) == 3
    jobs_entry = next(t for t in second.tables if t.table == "scrapped_jobs")
    assert jobs_entry.written == 0
    assert jobs_entry.skipped == 3


def test_migrate_does_not_clobber_target_by_default(tmp_path, target):
    """대상에 이미 있는 공고의 메모를 덮어쓰지 않는다."""
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    db.init_db()
    db.save_job({"company": "가나기업", "position": "생산직", "memo": "대상에서 쓴 메모",
                 "status": "최종 합격"})

    migrate(path)

    target_job = next(j for j in db.load_jobs() if j["company"] == "가나기업")
    assert target_job["memo"] == "대상에서 쓴 메모"
    assert target_job["status"] == "최종 합격"


def test_migrate_overwrite_replaces_target(tmp_path, target):
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    db.init_db()
    db.save_job({"company": "가나기업", "position": "생산직", "memo": "대상에서 쓴 메모"})

    migrate(path, overwrite=True)

    target_job = next(j for j in db.load_jobs() if j["company"] == "가나기업")
    assert target_job["memo"] == "자소서 제출함"
    assert target_job["status"] == "지원 완료"
    assert len(db.load_jobs()) == 3


def test_dry_run_writes_nothing(tmp_path, target):
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    db.init_db()
    result = migrate(path, dry_run=True)

    assert result.dry_run is True
    assert result.total_written == 6
    assert db.load_jobs() == []  # 실제로는 쓰지 않았다


def test_migrate_leaves_source_untouched(tmp_path, target):
    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)

    migrate(path)

    conn = sqlite3.connect(path)
    remaining = conn.execute("SELECT COUNT(*) FROM scrapped_jobs").fetchone()[0]
    conn.close()
    assert remaining == 3


def test_migrate_legacy_source(tmp_path, target):
    """구버전 원본도 있는 컬럼만으로 옮겨지고, 없는 값은 기본값이 된다."""
    path = str(tmp_path / "legacy.db")
    _make_source(path, SAMPLE, legacy=True)

    result = migrate(path)
    assert result.ok

    moved = db.load_jobs()
    assert len(moved) == 3
    assert moved[0]["status"] == "관심"  # 기본값


def test_migrate_reports_missing_tables(tmp_path, target):
    path = str(tmp_path / "legacy.db")
    _make_source(path, SAMPLE, legacy=True)

    result = migrate(path)
    seen = next(t for t in result.tables if t.table == "seen_jobs")
    assert seen.missing is True
    assert seen.written == 0


def test_migrate_empty_source(tmp_path, target):
    path = str(tmp_path / "empty.db")
    _make_source(path, [])

    result = migrate(path)
    assert result.ok
    assert db.load_jobs() == []


def test_migrate_uses_postgres_syntax(tmp_path, monkeypatch):
    """대상이 Postgres면 ON CONFLICT 구문으로 써야 한다."""
    log: list[str] = []
    conn = _FakePgConnection(str(tmp_path / "t.db"), log)
    monkeypatch.setattr(storage, "is_postgres", lambda: True)
    monkeypatch.setattr(storage, "_connect_postgres", lambda: conn)

    path = str(tmp_path / "src.db")
    _make_source(path, SAMPLE)
    migrate(path)

    joined = " ".join(log)
    assert "ON CONFLICT" in joined
    assert "%s" in joined
    assert "INSERT OR IGNORE" not in joined


def test_alert_log_is_not_migrated(tmp_path, target):
    """id가 새로 부여되므로 알림 기록은 일부러 옮기지 않는다."""
    assert "alert_log" not in migrate_mod._TABLES
