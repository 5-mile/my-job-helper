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

# Postgres에 못 붙었을 때 이 세션 동안 SQLite로 물러난 이유. 비어 있으면 정상.
# (보관함은 못 쓰지만 공고 검색은 DB 없이도 되므로 앱 전체를 죽이지 않는다.)
_fallback_reason: str = ""

# 현재 열려 있는 연결의 방언. SQL 생성 함수는 전역 설정이 아니라 이 값을 따른다.
# (DATABASE_URL이 있어도 db_path를 명시하면 SQLite로 붙으므로, 둘이 어긋나면 안 된다.)
_active_dialect: ContextVar[str | None] = ContextVar("active_dialect", default=None)

# `?` 를 `%s` 로 바꾸되, 문자열 리터럴 안의 물음표는 건드리지 않는다.
_PLACEHOLDER = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")


def database_url() -> str | None:
    """Postgres 접속 문자열. 없으면 SQLite를 쓴다는 뜻."""
    return settings.get("DATABASE_URL")


# Session pooler 주소: postgresql://postgres.<프로젝트ref>:<비번>@aws-N-<리전>.pooler.supabase.com:<포트>/postgres
_POOLER_RE = re.compile(
    r"^(?P<scheme>postgres(?:ql)?://)postgres\.(?P<ref>[a-z0-9]+):(?P<pw>[^@]+)@"
    r"[^/]*pooler\.supabase\.com:\d+(?P<tail>/.*)?$"
)


def direct_url_from_pooler(url: str | None = None) -> str | None:
    """pooler 주소에서 같은 프로젝트의 직접 연결 주소를 만들어 준다.

    프로젝트를 Restore 한 직후에는 pooler 쪽 테넌트 등록이 늦어 `tenant not found`
    가 나는 일이 있다. 그럴 때 직접 연결로 우회하면 데이터는 그대로 쓸 수 있다.
    다만 직접 연결은 IPv6 전용이라 Streamlit Cloud에서는 통하지 않는다.
    """
    url = url if url is not None else database_url()
    if not url:
        return None
    m = _POOLER_RE.match(url.strip())
    if not m:
        return None
    return "{scheme}postgres:{pw}@db.{ref}.supabase.co:5432{tail}".format(
        scheme=m.group("scheme"),
        pw=m.group("pw"),
        ref=m.group("ref"),
        tail=m.group("tail") or "/postgres",
    )


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

    # pooler를 쓸 때 사용자명은 반드시 postgres.<프로젝트ID> 형태여야 한다.
    # 프로젝트 ID는 20자 안팎의 소문자라, 짧으면 예시를 붙여넣은 것이다.
    user = parsed.get("user") or ""
    if "pooler.supabase.com" in host:
        if user == "postgres":
            return False, (
                "pooler를 쓸 때는 사용자명이 postgres 가 아니라 "
                "postgres.<프로젝트ID> 여야 합니다. [Connect] → Session pooler 의 "
                "문자열을 그대로 복사하세요."
            )
        ref = user.split(".", 1)[1] if "." in user else ""
        if not re.fullmatch(r"[a-z]{16,}", ref):
            return False, (
                f"사용자명이 올바르지 않습니다 ({user}). postgres.<프로젝트ID> 형태여야 "
                "하며, 프로젝트 ID는 20자 안팎의 소문자입니다. 예시 문자열을 "
                "붙여넣지 말고 [Connect] → Session pooler 의 값을 복사하세요."
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


def fallback_reason() -> str:
    """SQLite로 물러난 이유. 정상이면 빈 문자열."""
    return _fallback_reason


def use_sqlite_fallback(reason: str) -> None:
    """이 세션 동안 SQLite를 쓰도록 전환한다."""
    global _fallback_reason
    _fallback_reason = reason
    log.warning("Postgres 연결에 실패해 SQLite로 전환합니다: %s", reason)


def clear_fallback() -> None:
    global _fallback_reason
    _fallback_reason = ""


def explain_connection_error(exc: Exception | str) -> str:
    """psycopg 오류를 사람이 읽고 조치할 수 있는 문구로 바꾼다."""
    text = str(exc)

    if "tenant" in text and "not found" in text:
        return (
            "Supabase 프로젝트를 찾을 수 없습니다. 무료 플랜은 **7일간 접속이 없으면 "
            "자동으로 일시 정지**되고 주소가 내려갑니다.\n\n"
            "supabase.com/dashboard 에서 프로젝트를 열어 **Restore/Resume** 를 누르면 "
            "데이터 그대로 다시 켜집니다.\n\n"
            "**방금 Restore 하셨다면 몇 분 기다렸다 새로고침**해 보세요. 프로젝트가 "
            "켜진 뒤에도 pooler가 이를 알아차리는 데 시간이 걸려, 그동안은 같은 "
            "오류가 나옵니다.\n\n"
            "한참 지나도 그대로라면 접속 주소가 바뀐 경우입니다. Project Settings → "
            "Database → Connection string → **Session pooler** 주소를 새로 복사해 "
            "`python setup_cloud.py` 로 다시 넣어 주세요. 프로젝트를 지우셨다면 "
            "새로 만든 뒤 같은 방법으로 설정하면 됩니다."
        )
    if "password authentication failed" in text:
        return (
            "비밀번호가 맞지 않습니다. `[YOUR-PASSWORD]` 의 대괄호를 지웠는지, "
            "특수문자(@ / %)를 인코딩했는지 확인하세요."
        )
    if "Name or service not known" in text or "could not translate host name" in text:
        return (
            "호스트 주소를 찾을 수 없습니다. `db.xxx.supabase.co`(IPv6 전용) 대신 "
            "`aws-0-....pooler.supabase.com`(Session pooler) 주소를 쓰세요."
        )
    if "timeout" in text.lower():
        return "연결이 시간 내에 되지 않았습니다. 네트워크나 방화벽을 확인하세요."
    return "데이터베이스에 연결하지 못했습니다."


def is_postgres() -> bool:
    if _fallback_reason:
        return False
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
    def _open(target: str):
        return psycopg.connect(
            target,
            row_factory=dict_row,
            autocommit=False,
            connect_timeout=10,
            prepare_threshold=None,
        )

    try:
        _pg_conn = _open(url)
    except Exception as exc:
        # Restore 직후에는 pooler가 프로젝트를 아직 모를 수 있다(tenant not found).
        # 같은 자격 증명으로 직접 연결이 되면 데이터를 그대로 쓸 수 있으므로 한 번 더 시도한다.
        fallback = direct_url_from_pooler(url)
        if not fallback:
            raise
        log.warning("pooler 연결 실패, 직접 연결로 재시도합니다: %s", exc)
        try:
            _pg_conn = _open(fallback)
        except Exception:
            raise exc from None
        log.info("직접 연결로 접속했습니다 (IPv6 전용이라 Streamlit Cloud에서는 통하지 않습니다).")
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


def enable_rls_on_public_tables() -> list[str]:
    """public 스키마 테이블에 Row Level Security를 켠다 (Postgres일 때만).

    Supabase는 public 스키마를 REST API로 그대로 노출한다. RLS가 꺼져 있으면
    프로젝트의 anon 키를 아는 사람이 보관함을 읽거나 지울 수 있다. 정책을 하나도
    두지 않은 채 RLS만 켜면 REST 경로는 완전히 막힌다.

    이 앱은 `postgres` 역할로 직접 붙고 그 역할은 BYPASSRLS라, 켜도 동작에는
    영향이 없다. 테이블을 새로 만들어도 잊지 않도록 시작할 때마다 돌린다.

    켠 테이블 이름을 돌려준다(이미 켜져 있던 것은 제외).
    """
    if not is_postgres():
        return []

    changed: list[str] = []
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND rowsecurity = false"
            ).fetchall()
            names = [r["tablename"] if isinstance(r, dict) else r[0] for r in rows]
            for name in names:
                # 테이블 이름은 우리가 만든 것뿐이지만, 식별자는 바인딩할 수 없으므로
                # 모양을 한 번 확인하고 넘긴다.
                if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                    log.warning("예상 밖의 테이블 이름이라 건너뜁니다: %r", name)
                    continue
                conn.execute(f'ALTER TABLE public."{name}" ENABLE ROW LEVEL SECURITY')
                changed.append(name)
    except Exception as exc:  # pragma: no cover - 권한 없는 환경
        # 보안 강화는 실패해도 앱을 멈출 이유가 없다. 로그만 남긴다.
        log.warning("RLS를 켜지 못했습니다: %s", exc)
        return []
    if changed:
        log.info("RLS를 켰습니다: %s", ", ".join(changed))
    return changed
