"""로컬 SQLite 보관함을 외부 DB(PostgreSQL)로 옮기는 스크립트.

`DATABASE_URL` 을 설정한 뒤 한 번만 실행하면 된다. 원본 파일은 수정하지 않는다.

    python migrate_db.py --dry-run        # 무엇이 옮겨질지 먼저 확인
    python migrate_db.py                  # jobs.db -> DATABASE_URL
    python migrate_db.py --source 다른.db  # 다른 파일에서 옮기기
    python migrate_db.py --overwrite      # 대상에 이미 있는 공고도 덮어쓰기

기본 동작은 '대상에 없는 것만 추가'라서, 여러 번 실행해도 안전하다.
"""

from __future__ import annotations

import argparse
import logging
import sys

from jobhelper import storage
from jobhelper.migrate import migrate


def main() -> int:
    parser = argparse.ArgumentParser(description="보관함을 외부 DB로 이전")
    parser.add_argument(
        "--source",
        default=storage.SQLITE_PATH,
        help=f"원본 SQLite 파일 (기본: {storage.SQLITE_PATH})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="대상에 이미 있는 공고도 원본 내용으로 덮어씁니다",
    )
    parser.add_argument("--dry-run", action="store_true", help="옮기지 않고 계획만 출력")
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 로그")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    if not storage.is_postgres():
        print(
            "❌ DATABASE_URL이 설정되지 않아 옮길 대상이 없습니다.\n"
            "   .env 또는 환경 변수에 DATABASE_URL을 넣고 다시 실행하세요.\n"
            "   설정 방법은 README의 '보관함 영구 저장' 항목을 참고하세요.",
            file=sys.stderr,
        )
        return 1

    print(f"원본 : {args.source} (SQLite)")
    print(f"대상 : {storage.backend_name()}")
    if args.dry_run:
        print("모드 : dry-run (실제로 쓰지 않음)")
    elif args.overwrite:
        print("모드 : 덮어쓰기 (대상의 같은 공고를 원본 내용으로 교체)")
    else:
        print("모드 : 추가만 (대상에 이미 있는 공고는 건너뜀)")
    print()

    if not args.dry_run:
        ok, message = storage.health_check()
        if not ok:
            print(f"❌ {message}", file=sys.stderr)
            return 1
        print(f"✅ {message}\n")

    try:
        result = migrate(
            source_path=args.source,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ 이전 중 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    labels = {
        "scrapped_jobs": "보관함 공고",
        "seen_jobs": "이미 본 공고 기록",
        "company_info_cache": "회사 정보 캐시",
    }

    for entry in result.tables:
        label = labels.get(entry.table, entry.table)
        if entry.missing:
            print(f"  ―  {label}: 원본에 없음 (건너뜀)")
        elif entry.error:
            print(f"  ❌ {label}: {entry.error}")
        elif entry.read == 0:
            print(f"  ―  {label}: 옮길 데이터 없음")
        else:
            suffix = f", 건너뜀 {entry.skipped}건" if entry.skipped else ""
            verb = "옮길 예정" if result.dry_run else "옮김"
            print(f"  ✅ {label}: 원본 {entry.read}건 중 {entry.written}건 {verb}{suffix}")

    print()
    if not result.ok:
        print("❌ 일부 표를 옮기지 못했습니다. 위 메시지를 확인하세요.", file=sys.stderr)
        return 1

    if result.dry_run:
        print(f"dry-run 완료 — 총 {result.total_written}건이 옮겨집니다.")
        print("실제로 옮기려면 --dry-run 없이 다시 실행하세요.")
        return 0

    print(f"✅ 이전 완료 — 총 {result.total_written}건을 옮겼습니다.")
    if result.total_skipped:
        print(f"   (이미 대상에 있어 건너뛴 {result.total_skipped}건은 --overwrite로 덮어쓸 수 있습니다.)")
    print("\n확인: python check_db.py")
    print("알림 발송 기록은 옮기지 않으므로, 오늘 마감 알림이 한 번 더 갈 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
