"""SQLite / PostgreSQL 양쪽을 같은 인터페이스로 다루는 저장소 계층.

- 로컬에서는 설정 없이 SQLite(`jobs.db`)를 쓴다.
- ``DATABASE_URL`` 이 있으면 PostgreSQL에 연결한다.
  Streamlit Cloud는 파일시스템이 휘발성이라, 보관함을 유지하려면 이쪽이 필요하다.

SQL은 SQLite 문법(`?` 자리표시자)으로 쓰고, Postgres일 때 이 모듈이 변환한다.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from . import settings

log = logging.getLogger(__name__)

SQLITE_PATH = os.environ.get(
    "JOB_HELPER_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jobs.db"),
)

_pg_lock = threading.Lock()
_pg_conn: Any = None

# 현재 열려 있는 연결의 방언. SQL 생성 함수는 전역 설정이 아니라 이 값을 따른다.
# (DATABASE_URL이 있어도 db_path를 명시하면 SQLite로 붙으므로, 둘이 어긋나면 안 된다.)
_active_dialect: ContextVar[str | None] = ContextVar("active_dialect", default=None)

# `?` 를 `%s` 로 바꾸되, 문자열 리터럴 안의 물음표는 건드리지 않는다.
_PLACEHOLDER = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")


def database_url() -> str | None:
    """Postgres 접속 문자열. 없으면 SQLite를 쓴다는 뜻."""
    return settings.get("DATABASE_URL")


def is_postgres() -> bool:
    return bool(database_url())


def backend_name() -> str:
    return "PostgreSQL" if is_postgres() else "SQLite"


def _use_postgres() -> bool:
    """SQL을 만들 때 쓸 방언. 연결 중이면 그 연결의 방언을 따른다."""
    active = _active_dialect.get()
    if active is not None:
        return active == "postgres"
    return is_postgres()


def translate(query: str) -> str:
    """SQLite 문법으로 쓴 쿼리를 현재 백엔드에 맞게 바꾼다."""
    if not _use_postgres():
        return query
    return _PLACEHOLDER.sub("%s", query)


class _Cursor:
    """양쪽 드라이버 차이를 흡수하는 얇은 커서 래퍼."""

    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, query: str, params: tuple | list = ()) -> "_Cursor":
        self._cursor.execute(translate(query), params)
        return self

    def executemany(self, query: str, seq: list) -> "_Cursor":
        if seq:
            self._cursor.executemany(translate(query), seq)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class _Connection:
    def __init__(self, raw: Any):
        self._raw = raw

    def cursor(self) -> _Cursor:
        return _Cursor(self._raw.cursor())

    def execute(self, query: str, params: tuple | list = ()) -> _Cursor:
        return self.cursor().execute(query, params)

    def executemany(self, query: str, seq: list) -> _Cursor:
        return self.cursor().executemany(query, seq)


def _connect_postgres():
    """psycopg 연결을 만들고 재사용한다 (매 rerun마다 새로 붙으면 느리다)."""
    global _pg_conn
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "DATABASE_URL이 설정되어 있지만 psycopg가 없습니다. "
            "`pip install \"psycopg[binary]\"` 를 실행하세요."
        ) from exc

    if _pg_conn is not None and not _pg_conn.closed:
        return _pg_conn

    _pg_conn = psycopg.connect(
        database_url(), row_factory=dict_row, autocommit=False, connect_timeout=10
    )
    return _pg_conn


@contextmanager
def connect(db_path: str | None = None):
    """트랜잭션 하나를 열고 닫는다.

    ``db_path`` 를 주면 백엔드 설정과 무관하게 그 SQLite 파일을 쓴다(테스트용).
    """
    if db_path is None and is_postgres():
        token = _active_dialect.set("postgres")
        try:
            with _pg_lock:
                conn = _connect_postgres()
                try:
                    yield _Connection(conn)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        finally:
            _active_dialect.reset(token)
        return

    token = _active_dialect.set("sqlite")
    raw = sqlite3.connect(db_path or SQLITE_PATH)
    raw.row_factory = sqlite3.Row
    try:
        yield _Connection(raw)
        raw.commit()
    finally:
        raw.close()
        _active_dialect.reset(token)


# --- 방언 차이가 있는 SQL 조각 ----------------------------------------------
def autoincrement_pk() -> str:
    return "SERIAL PRIMARY KEY" if _use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def insert_or_ignore(table: str, columns: list[str], conflict: list[str]) -> str:
    """중복이면 조용히 넘어가는 INSERT."""
    cols = ", ".join(columns)
    marks = ", ".join("?" * len(columns))
    if _use_postgres():
        target = ", ".join(conflict)
        return f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT ({target}) DO NOTHING"
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})"


def upsert(table: str, columns: list[str], conflict: list[str]) -> str:
    """중복이면 덮어쓰는 INSERT."""
    cols = ", ".join(columns)
    marks = ", ".join("?" * len(columns))
    if _use_postgres():
        target = ", ".join(conflict)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in conflict)
        return (
            f"INSERT INTO {table} ({cols}) VALUES ({marks}) "
            f"ON CONFLICT ({target}) DO UPDATE SET {updates}"
        )
    return f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})"


def add_column_if_missing(conn: _Connection, table: str, column: str, ddl: str) -> None:
    """구버전 스키마에 컬럼을 덧붙인다 (양쪽 방언 모두 지원)."""
    if _use_postgres():
        # Postgres는 ADD COLUMN IF NOT EXISTS를 직접 지원한다.
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        return

    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def health_check() -> tuple[bool, str]:
    """현재 백엔드에 연결되는지 확인한다. UI 표시에 쓴다."""
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return True, f"{backend_name()} 연결 정상"
    except Exception as exc:
        return False, f"{backend_name()} 연결 실패: {exc}"
