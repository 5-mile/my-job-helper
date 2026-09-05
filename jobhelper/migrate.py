"""로컬 SQLite 보관함을 설정된 저장소(PostgreSQL)로 옮긴다.

원본을 읽어 메모리에 담은 뒤 대상에 쓰기 때문에, 원본 파일은 건드리지 않는다.
같은 공고가 대상에 이미 있으면 기본적으로 건너뛴다(``overwrite=True`` 면 덮어씀).

옮기는 대상:
  - scrapped_jobs      보관함 + 지원 현황 (메모·상태·지원일 포함)
  - seen_jobs          NEW 뱃지용 '이미 본 공고' 기록
  - company_info_cache 국민연금 조회 캐시

``alert_log`` (알림 발송 기록)는 옮기지 않는다. 이 표는 ``scrapped_jobs.id`` 를
가리키는데, 이전 과정에서 id가 새로 부여되므로 그대로 옮기면 엉뚱한 공고를
가리키게 된다. 옮기지 않아서 생기는 영향은 이전 당일에 알림이 한 번 더 갈 수
있다는 것뿐이다.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .storage import connect, insert_or_ignore, upsert

log = logging.getLogger(__name__)

# 표 이름 -> (충돌 판정 컬럼, 옮기지 않을 컬럼)
_TABLES: dict[str, tuple[list[str], set[str]]] = {
    "scrapped_jobs": (["company", "position"], {"id"}),
    "seen_jobs": (["job_key"], set()),
    "company_info_cache": (["company"], set()),
}


@dataclass
class TableResult:
    table: str
    read: int = 0
    written: int = 0
    skipped: int = 0
    missing: bool = False
    error: str = ""


@dataclass
class MigrationResult:
    source: str = ""
    target: str = ""
    tables: list[TableResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total_written(self) -> int:
        return sum(t.written for t in self.tables)

    @property
    def total_skipped(self) -> int:
        return sum(t.skipped for t in self.tables)

    @property
    def ok(self) -> bool:
        return not any(t.error for t in self.tables)


def _source_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def read_source(source_path: str) -> dict[str, tuple[list[str], list[tuple]]]:
    """원본 SQLite에서 표별로 (컬럼목록, 행들)을 읽는다."""
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"원본 파일이 없습니다: {source_path}")

    data: dict[str, tuple[list[str], list[tuple]]] = {}
    conn = sqlite3.connect(source_path)
    try:
        for table, (_, exclude) in _TABLES.items():
            if not _table_exists(conn, table):
                continue
            columns = [c for c in _source_columns(conn, table) if c not in exclude]
            if not columns:
                continue
            cols = ", ".join(columns)
            rows = conn.execute(f"SELECT {cols} FROM {table}").fetchall()
            data[table] = (columns, rows)
    finally:
        conn.close()
    return data


def _existing_keys(table: str, conflict: list[str]) -> set[tuple]:
    """대상에 이미 있는 행의 키 집합. 건너뛴 개수를 세는 데 쓴다."""
    cols = ", ".join(conflict)
    try:
        with connect() as conn:
            rows = conn.execute(f"SELECT {cols} FROM {table}").fetchall()
    except Exception as exc:
        log.warning("%s 기존 키 조회 실패: %s", table, exc)
        return set()
    return {tuple(row[c] for c in conflict) for row in rows}


def migrate(
    source_path: str,
    overwrite: bool = False,
    dry_run: bool = False,
    target_name: str = "",
) -> MigrationResult:
    """원본 SQLite의 보관함을 현재 설정된 저장소로 옮긴다."""
    from . import company_info, db, storage

    result = MigrationResult(
        source=source_path,
        target=target_name or storage.backend_name(),
        dry_run=dry_run,
    )

    data = read_source(source_path)

    # 대상 스키마를 먼저 준비한다.
    if not dry_run:
        db.init_db()
        company_info.init_cache()

    for table, (conflict, _) in _TABLES.items():
        entry = TableResult(table=table)

        if table not in data:
            entry.missing = True
            result.tables.append(entry)
            continue

        columns, rows = data[table]
        entry.read = len(rows)

        if not rows:
            result.tables.append(entry)
            continue

        # 충돌 판정 컬럼이 원본에 없으면 안전하게 건너뛴다.
        if not all(c in columns for c in conflict):
            entry.error = f"원본에 {conflict} 컬럼이 없어 건너뜁니다"
            result.tables.append(entry)
            continue

        existing = _existing_keys(table, conflict) if not dry_run else set()
        indexes = [columns.index(c) for c in conflict]
        fresh = [r for r in rows if tuple(r[i] for i in indexes) not in existing]
        entry.skipped = len(rows) - len(fresh)

        if dry_run:
            entry.written = len(rows)
            result.tables.append(entry)
            continue

        target_rows = rows if overwrite else fresh
        if not target_rows:
            result.tables.append(entry)
            continue

        try:
            with connect() as conn:
                # SQL은 연결의 방언을 따르므로 반드시 연결 안에서 만든다.
                statement = (
                    upsert(table, columns, conflict)
                    if overwrite
                    else insert_or_ignore(table, columns, conflict)
                )
                conn.executemany(statement, [tuple(r) for r in target_rows])
            entry.written = len(target_rows)
        except Exception as exc:
            entry.error = f"{type(exc).__name__}: {exc}"
            log.error("%s 이전 실패: %s", table, exc)

        result.tables.append(entry)

    return result
