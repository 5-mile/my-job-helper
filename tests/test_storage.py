"""저장소 계층 테스트 — SQLite / PostgreSQL 두 방언 모두 검증한다.

실제 Postgres 서버 없이도 돌도록 두 가지 방법을 쓴다.

1. 생성된 Postgres SQL을 sqlglot으로 **문법 검증**한다.
2. `%s`와 `SERIAL` 만 되돌린 뒤 SQLite에서 **실행**한다.
   `ON CONFLICT ... DO UPDATE SET ... EXCLUDED.x` 는 SQLite도 지원하므로,
   Postgres 전용 분기의 동작을 그대로 확인할 수 있다.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import db, storage  # noqa: E402


# ==========================================
# 방언별 SQL 생성
# ==========================================
@pytest.fixture
def as_postgres(monkeypatch):
    monkeypatch.setattr(storage, "is_postgres", lambda: True)


@pytest.fixture
def as_sqlite(monkeypatch):
    monkeypatch.setattr(storage, "is_postgres", lambda: False)


def test_placeholder_translation(as_postgres):
    assert storage.translate("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_placeholder_untouched_on_sqlite(as_sqlite):
    query = "SELECT * FROM t WHERE a = ?"
    assert storage.translate(query) == query


def test_question_mark_inside_string_literal_is_preserved(as_postgres):
    """문자열 리터럴 안의 물음표까지 바꿔버리면 안 된다."""
    out = storage.translate("SELECT * FROM t WHERE note = '왜?' AND a = ?")
    assert out == "SELECT * FROM t WHERE note = '왜?' AND a = %s"


def test_autoincrement_pk(as_postgres):
    assert storage.autoincrement_pk() == "SERIAL PRIMARY KEY"


def test_autoincrement_pk_sqlite(as_sqlite):
    assert storage.autoincrement_pk() == "INTEGER PRIMARY KEY AUTOINCREMENT"


def test_insert_or_ignore_postgres(as_postgres):
    sql = storage.insert_or_ignore("t", ["a", "b"], ["a"])
    assert "ON CONFLICT (a) DO NOTHING" in sql
    assert "INSERT OR IGNORE" not in sql


def test_insert_or_ignore_sqlite(as_sqlite):
    sql = storage.insert_or_ignore("t", ["a", "b"], ["a"])
    assert sql.startswith("INSERT OR IGNORE INTO t")


def test_upsert_postgres_updates_non_key_columns(as_postgres):
    sql = storage.upsert("t", ["a", "b", "c"], ["a"])
    assert "ON CONFLICT (a) DO UPDATE SET" in sql
    assert "b = EXCLUDED.b" in sql
    assert "c = EXCLUDED.c" in sql
    assert "a = EXCLUDED.a" not in sql  # 키 자신은 갱신하지 않는다


def test_upsert_sqlite(as_sqlite):
    assert storage.upsert("t", ["a", "b"], ["a"]).startswith("INSERT OR REPLACE INTO t")


def test_backend_name_follows_database_url(monkeypatch):
    monkeypatch.setattr(storage, "database_url", lambda: "postgresql://x/y")
    assert storage.is_postgres() is True
    assert storage.backend_name() == "PostgreSQL"

    monkeypatch.setattr(storage, "database_url", lambda: None)
    assert storage.is_postgres() is False
    assert storage.backend_name() == "SQLite"


# ==========================================
# 생성된 Postgres SQL의 문법 검증
# ==========================================
def _postgres_statements() -> list[str]:
    return [
        f"CREATE TABLE IF NOT EXISTS scrapped_jobs (id {storage.autoincrement_pk()}, "
        "source TEXT, company TEXT, position TEXT, date TEXT, link TEXT)",
        "ALTER TABLE scrapped_jobs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT '관심'",
        "DELETE FROM scrapped_jobs WHERE id NOT IN "
        "(SELECT MIN(id) FROM scrapped_jobs GROUP BY company, position)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_unique ON scrapped_jobs (company, position)",
        storage.insert_or_ignore("scrapped_jobs", db._JOB_COLUMNS, ["company", "position"]),
        storage.insert_or_ignore("seen_jobs", ["job_key", "first_seen"], ["job_key"]),
        storage.insert_or_ignore("alert_log", ["job_id", "alert_date"], ["job_id", "alert_date"]),
        storage.upsert(
            "company_info_cache",
            ["company", "employees", "avg_monthly_pay", "address", "joined_this_month",
             "left_this_month", "data_month", "found", "fetched_at"],
            ["company"],
        ),
        "SELECT status, COUNT(*) AS n FROM scrapped_jobs GROUP BY status",
        "UPDATE scrapped_jobs SET status = ?, memo = ? WHERE id = ?",
    ]


def test_generated_postgres_sql_is_syntactically_valid(as_postgres):
    """sqlglot이 Postgres 방언으로 파싱하지 못하면 문법 오류다."""
    sqlglot = pytest.importorskip("sqlglot")

    for statement in _postgres_statements():
        translated = storage.translate(statement)
        # 드라이버 자리표시자는 파서가 모르므로 리터럴로 바꿔서 검사한다.
        probe = translated.replace("%s", "'x'")
        parsed = sqlglot.parse(probe, dialect="postgres")
        assert parsed and parsed[0] is not None, f"파싱 실패: {statement}"


# ==========================================
# Postgres 분기를 실제로 실행 (SQLite 위에서)
# ==========================================
class _FakePgCursor:
    """psycopg 커서 흉내. Postgres SQL을 SQLite가 이해하도록 최소 변환."""

    def __init__(self, cursor, log):
        self._cursor = cursor
        self._log = log

    @staticmethod
    def _adapt(query: str) -> str:
        query = query.replace("%s", "?")
        query = query.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        # SQLite에는 ADD COLUMN IF NOT EXISTS가 없다.
        query = re.sub(r"ADD COLUMN IF NOT EXISTS", "ADD COLUMN", query)
        return query

    def execute(self, query, params=()):
        self._log.append(query)
        try:
            self._cursor.execute(self._adapt(query), params)
        except sqlite3.OperationalError as exc:
            # 이미 있는 컬럼을 다시 추가하는 경우만 무시 (Postgres의 IF NOT EXISTS 흉내)
            if "duplicate column name" not in str(exc):
                raise
        return self

    def executemany(self, query, seq):
        self._log.append(query)
        self._cursor.executemany(self._adapt(query), seq)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _FakePgConnection:
    closed = False

    def __init__(self, path, log):
        self._raw = sqlite3.connect(path)
        self._raw.row_factory = sqlite3.Row
        self._log = log

    def cursor(self):
        return _FakePgCursor(self._raw.cursor(), self._log)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()


@pytest.fixture
def pg_like(tmp_path, monkeypatch):
    """Postgres 분기를 타되 저장은 SQLite에 하는 환경."""
    path = str(tmp_path / "pg.db")
    log: list[str] = []
    conn = _FakePgConnection(path, log)

    monkeypatch.setattr(storage, "is_postgres", lambda: True)
    monkeypatch.setattr(storage, "_connect_postgres", lambda: conn)
    return log


def test_full_flow_on_postgres_branch(pg_like):
    """init → save → update → count → delete 를 Postgres SQL로 수행한다."""
    db.init_db()

    job = {
        "source": "사람인", "company": "테스트기업", "position": "생산직",
        "date": "⏳ D-5", "link": "https://example.com", "location": "경기",
        "category": "일반/기타기업", "welfares": ["🍔 식사제공"], "deadline": "2026-09-20",
    }
    assert db.save_job(job) is True
    assert db.save_job(job) is False  # ON CONFLICT DO NOTHING 이 동작해야 한다

    jobs = db.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["company"] == "테스트기업"
    assert jobs[0]["welfares"] == ["🍔 식사제공"]

    db.update_job(jobs[0]["id"], status="지원 완료", memo="자소서 제출")
    assert db.load_jobs()[0]["status"] == "지원 완료"
    assert db.status_counts()["지원 완료"] == 1
    assert db.saved_keys() == {"테스트기업::생산직"}

    db.delete_job(jobs[0]["id"])
    assert db.load_jobs() == []

    # 실제로 Postgres 문법이 오갔는지 확인
    joined = " ".join(pg_like)
    assert "ON CONFLICT" in joined
    assert "%s" in joined


def test_mark_seen_on_postgres_branch(pg_like):
    db.init_db()
    assert db.mark_seen(["a", "b"]) == set()
    assert db.mark_seen(["a", "b", "c"]) == {"c"}
    assert db.mark_seen(["a", "b", "c"]) == set()


def test_company_cache_upsert_on_postgres_branch(pg_like):
    from jobhelper import company_info

    company_info.init_cache()
    first = company_info.CompanyInfo(name="테스트기업", employees=100, avg_monthly_pay=3_000_000)
    company_info._write_cache(first)

    # 같은 회사를 다시 쓰면 덮어써져야 한다 (ON CONFLICT DO UPDATE)
    second = company_info.CompanyInfo(name="테스트기업", employees=250, avg_monthly_pay=3_400_000)
    company_info._write_cache(second)

    cached = company_info._read_cache("테스트기업")
    assert cached is not None
    assert cached.employees == 250
    assert cached.avg_monthly_pay == 3_400_000


def test_alert_log_on_postgres_branch(pg_like):
    from datetime import date

    from jobhelper import notify

    db.init_db()
    notify.init_alert_log()
    today = date(2026, 9, 5)

    assert notify._already_sent(1, today) is False
    notify._mark_sent([1, 2], today)
    assert notify._already_sent(1, today) is True
    notify._mark_sent([1, 2], today)  # 중복 삽입이 터지지 않아야 한다
    assert notify._already_sent(2, today) is True


# ==========================================
# 백엔드 선택
# ==========================================
def test_explicit_db_path_ignores_postgres_setting(tmp_path, monkeypatch):
    """테스트나 CLI가 db_path를 주면 Postgres 설정과 무관하게 그 파일을 쓴다."""
    monkeypatch.setattr(storage, "is_postgres", lambda: True)
    path = str(tmp_path / "explicit.db")

    db.init_db(path)
    db.save_job({"company": "A", "position": "B"}, path)
    assert len(db.load_jobs(db_path=path)) == 1


def test_health_check_reports_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "database_url", lambda: None)
    monkeypatch.setattr(storage, "SQLITE_PATH", str(tmp_path / "h.db"))
    ok, message = storage.health_check()
    assert ok is True
    assert "SQLite" in message


# ==========================================
# 비밀번호 자리표시자 감지
# ==========================================
@pytest.mark.parametrize("url", [
    "postgresql://postgres.exampleprojectref:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
    "postgresql://postgres:[your-password]@db.x.supabase.co:5432/postgres",
    "postgresql://postgres:비밀번호@db.x.supabase.co:5432/postgres",
])
def test_url_needs_password_detects_placeholder(url):
    assert storage.url_needs_password(url) is True


def test_url_needs_password_false_for_real_password():
    real = "postgresql://postgres.exampleprojectref:s3cr3t@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
    assert storage.url_needs_password(real) is False


def test_url_needs_password_false_when_unset(monkeypatch):
    monkeypatch.setattr(storage, "database_url", lambda: None)
    assert storage.url_needs_password() is False


# ==========================================
# 접속 문자열 검증 (비밀번호 특수문자)
# ==========================================
_BASE = "postgresql://postgres.exampleprojectref:{}@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"


@pytest.mark.parametrize("pw", ["abc!def", "!start", "end!", "a!!b", "ok:colon", "fine#hash"])
def test_validate_url_accepts_safe_specials(pw):
    """! # ? : 는 인코딩 없이도 정상이다."""
    pytest.importorskip("psycopg")
    ok, message = storage.validate_url(_BASE.format(pw))
    assert ok, message


@pytest.mark.parametrize("pw,hint", [
    ("P@ssw0rd", "%40"),
    ("with/slash", "%2F"),
    ("with%pct", "%25"),
])
def test_validate_url_rejects_unencoded_specials(pw, hint):
    """@ / % 는 인코딩하지 않으면 조용히 잘못 해석되므로 먼저 잡는다."""
    pytest.importorskip("psycopg")
    ok, message = storage.validate_url(_BASE.format(pw))
    assert not ok
    assert hint in message


def test_validate_url_accepts_encoded_specials():
    pytest.importorskip("psycopg")
    ok, message = storage.validate_url(_BASE.format("P%40ssw0rd"))
    assert ok, message


def test_validate_url_empty():
    ok, message = storage.validate_url("")
    assert not ok
    assert "비어" in message


@pytest.mark.parametrize("pw,hint", [
    ("[mypassword!]", "대괄호까지 지워야"),
    ("my[pass]word", "%5B"),
])
def test_validate_url_catches_leftover_brackets(pw, hint):
    """[YOUR-PASSWORD]의 대괄호를 남긴 채 안쪽만 바꾸는 실수를 잡는다."""
    pytest.importorskip("psycopg")
    ok, message = storage.validate_url(_BASE.format(pw))
    assert not ok
    assert hint in message


def test_validate_url_accepts_password_with_exclamation():
    """! 는 percent-encoding 없이 그대로 동작한다 (실제 연결로 확인됨)."""
    pytest.importorskip("psycopg")
    ok, message = storage.validate_url(_BASE.format("abc123!xyz"))
    assert ok, message


# ==========================================
# 예시 호스트 / Direct connection 감지
# ==========================================
def test_validate_url_rejects_example_host():
    """문서의 예시 호스트를 그대로 붙여넣은 경우를 잡는다."""
    pytest.importorskip("psycopg")
    for host in ("db.x.supabase.co", "db.xxxxx.supabase.co"):
        ok, message = storage.validate_url(f"postgresql://postgres:pw@{host}:5432/postgres")
        assert not ok
        assert "예시 문자열" in message


def test_warn_direct_connection_flags_ipv6_only_host():
    """db.<ref>.supabase.co 는 IPv6 전용이라 Streamlit Cloud에서 실패한다."""
    pytest.importorskip("psycopg")
    warning = storage.warn_direct_connection(
        "postgresql://postgres:pw@db.mpbhyqmqbhcqyrzstsge.supabase.co:5432/postgres"
    )
    assert "IPv6" in warning
    assert "pooler" in warning


def test_warn_direct_connection_silent_for_pooler():
    pytest.importorskip("psycopg")
    assert storage.warn_direct_connection(_BASE.format("pw")) == ""


def test_warn_direct_connection_silent_when_unset():
    assert storage.warn_direct_connection("") == ""


# ==========================================
# pooler 사용자명 검증
# ==========================================
def test_validate_url_rejects_example_username():
    """postgres.abc 같은 예시 사용자명을 잡는다 (실제 배포에서 겪은 오류)."""
    pytest.importorskip("psycopg")
    url = "postgresql://postgres.abc:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
    ok, message = storage.validate_url(url)
    assert not ok
    assert "postgres.<프로젝트ID>" in message


def test_validate_url_rejects_bare_postgres_user_on_pooler():
    """pooler는 postgres.<ref> 형태를 요구한다."""
    pytest.importorskip("psycopg")
    url = "postgresql://postgres:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
    ok, message = storage.validate_url(url)
    assert not ok
    assert "postgres.<프로젝트ID>" in message


def test_validate_url_accepts_real_project_ref():
    pytest.importorskip("psycopg")
    url = ("postgresql://postgres.mpbhyqmqbhcqyrzstsge:pw@"
           "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres")
    ok, message = storage.validate_url(url)
    assert ok, message


def test_validate_url_ignores_username_shape_for_direct_host():
    """직접 연결 호스트에서는 사용자명이 postgres 하나뿐이라 검사하지 않는다."""
    pytest.importorskip("psycopg")
    ok, _ = storage.validate_url(
        "postgresql://postgres:pw@db.mpbhyqmqbhcqyrzstsge.supabase.co:5432/postgres"
    )
    assert ok
