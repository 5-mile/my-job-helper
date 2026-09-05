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


PASSWORD_PLACEHOLDERS = ("[YOUR-PASSWORD]", "[your-password]", "비밀번호")


def url_needs_password(url: str | None = None) -> bool:
    """접속 문자열에 비밀번호 자리표시자가 그대로 남아 있는지."""
    url = url if url is not None else database_url()
    if not url:
        return False
    return any(p in url for p in PASSWORD_PLACEHOLDERS)


def validate_url(url: str | None = None) -> tuple[bool, str]:
    """접속 문자열이 제대로 파싱되는지 확인한다.

    비밀번호에 @ / % 같은 문자가 인코딩 없이 들어가면 URL이 조용히 잘못
    해석되어, 나중에 인증 실패로만 보인다. 그걸 먼저 잡아낸다.
    (! # ? : 는 인코딩 없이도 정상 동작한다.)
    """
    url = url if url is not None else database_url()
    if not url:
        return False, "DATABASE_URL이 비어 있습니다."

    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return True, ""  # 드라이버가 없으면 여기서 판단하지 않는다

    try:
        parsed = conninfo_to_dict(url)
    except Exception:
        return False, (
            "접속 문자열을 해석하지 못했습니다. 비밀번호에 % 가 있다면 %25 로 "
            "바꿔주세요."
        )

    if not parsed.get("host"):
        return False, (
            "호스트를 읽지 못했습니다. 비밀번호에 / 가 있다면 %2F 로 바꿔주세요."
        )
    password = parsed.get("password")
    if not password:
        return False, (
            "비밀번호를 읽지 못했습니다. 비밀번호에 / 가 있다면 %2F 로 바꿔주세요. "
            "([YOUR-PASSWORD] 자리를 아직 안 바꾸셨는지도 확인해 보세요.)"
        )
    # 자리표시자의 대괄호만 남기고 안쪽 글자를 바꾸는 실수가 잦다.
    if password.startswith("[") and password.endswith("]"):
        return False, (
            "비밀번호가 대괄호로 감싸여 있습니다. [YOUR-PASSWORD]를 바꿀 때 "
            "대괄호까지 지워야 합니다. 대괄호를 빼고 비밀번호만 남기세요."
        )
    if "[" in password or "]" in password:
        return False, (
            "비밀번호에 대괄호가 들어 있습니다. 자리표시자의 [ ] 가 남아 있는지 "
            "확인하세요. 비밀번호에 실제로 [ 나 ] 가 쓰였다면 %5B / %5D 로 바꿔주세요."
        )
    if "@" in url.rsplit("@", 1)[0].split("://", 1)[-1].split(":", 1)[-1]:
        return False, (
            "비밀번호에 @ 가 들어 있는 것 같습니다. %40 으로 바꿔주세요."
        )

    host = parsed.get("host") or ""
    # 문서·예시에 쓰인 자리표시자 호스트를 그대로 붙여넣는 실수
    if re.fullmatch(r"db\.x+\.supabase\.co", host) or "프로젝트" in host:
        return False, (
            f"호스트가 예시 문자열입니다 ({host}). Supabase 대시보드의 "
            "[Connect] → Session pooler 에서 실제 접속 문자열을 복사하세요."
        )
    return True, ""


def warn_direct_connection(url: str | None = None) -> str:
    """Direct connection 호스트면 경고 문구를 돌려준다 (빈 문자열이면 정상).

    db.<ref>.supabase.co 는 IPv6 전용이라 Streamlit Cloud 같은 IPv4 환경에서
    'Name or service not known' 으로 실패한다. 로컬에서는 될 수도 있어서
    막지 않고 경고만 한다.
    """
    url = url if url is not None else database_url()
    if not url:
        return ""
    try:
        from psycopg.conninfo import conninfo_to_dict

        host = (conninfo_to_dict(url).get("host") or "")
    except Exception:
        return ""
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return (
            "Direct connection 호스트를 쓰고 있습니다. 이 주소는 IPv6 전용이라 "
            "Streamlit Cloud에서는 'Name or service not known'으로 실패합니다. "
            "[Connect] → Session pooler 의 주소"
            "(aws-0-....pooler.supabase.com)를 쓰세요."
        )
    return ""


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

    url = database_url()
    if url_needs_password(url):
        raise RuntimeError(
            "DATABASE_URL의 [YOUR-PASSWORD] 자리가 아직 실제 비밀번호로 "
            "바뀌지 않았습니다. .env 파일을 확인하세요."
        )

    # prepare_threshold=None: Supabase transaction pooler(6543)는 prepared
    # statement를 지원하지 않는다. session pooler/직접 연결에서는 영향이 없으므로
    # 어느 쪽을 쓰든 동작하도록 꺼 둔다.
    _pg_conn = psycopg.connect(
        url,
        row_factory=dict_row,
        autocommit=False,
        connect_timeout=10,
        prepare_threshold=None,
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
