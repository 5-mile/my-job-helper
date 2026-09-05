"""저장소 연결 점검 스크립트.

설정한 DB에 실제로 붙어서 테이블 생성 → 저장 → 수정 → 삭제까지 한 바퀴 돌려보고
결과를 알려준다. Supabase 같은 외부 DB를 연결한 뒤 한 번 실행해 보면 된다.

    python check_db.py            # 현재 설정(DATABASE_URL 또는 SQLite)으로 점검
    python check_db.py --keep     # 점검용 데이터를 지우지 않고 남겨둠

임시로 만든 점검용 공고는 기본적으로 마지막에 지우므로 보관함이 더러워지지 않는다.
"""

from __future__ import annotations

import argparse
import sys

from jobhelper import db, storage

PROBE_COMPANY = "__연결점검용__"
PROBE_POSITION = "__삭제해도_됨__"


def _mask(url: str | None) -> str:
    """비밀번호를 가린 접속 문자열."""
    if not url:
        return "(없음 - SQLite 사용)"
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":")[0] if ":" in creds else creds
    return f"{scheme}://{user}:****@{host}"


def main() -> int:
    parser = argparse.ArgumentParser(description="저장소 연결 점검")
    parser.add_argument("--keep", action="store_true", help="점검용 데이터를 지우지 않음")
    args = parser.parse_args()

    print(f"백엔드      : {storage.backend_name()}")
    print(f"접속 정보   : {_mask(storage.database_url())}")
    if not storage.is_postgres():
        print(f"SQLite 경로 : {storage.SQLITE_PATH}")
    print()

    ok, message = storage.health_check()
    if not ok:
        print(f"❌ {message}", file=sys.stderr)
        print(
            "\nDATABASE_URL을 확인하세요. Supabase는 Project Settings → Database →\n"
            "Connection string → URI 값을 쓰고, [YOUR-PASSWORD] 자리를 실제 비밀번호로\n"
            "바꿔야 합니다. psycopg가 없다면 `pip install \"psycopg[binary]\"`.",
            file=sys.stderr,
        )
        return 1
    print(f"✅ {message}")

    steps: list[tuple[str, bool]] = []
    try:
        db.init_db()
        steps.append(("테이블 생성/마이그레이션", True))

        probe = {
            "source": "점검", "company": PROBE_COMPANY, "position": PROBE_POSITION,
            "date": "", "link": "", "location": "", "category": "",
            "welfares": [], "deadline": "",
        }
        # 이전 점검 잔여물이 있으면 먼저 치운다.
        for existing in db.load_jobs():
            if existing["company"] == PROBE_COMPANY:
                db.delete_job(existing["id"])

        steps.append(("공고 저장", db.save_job(probe)))
        steps.append(("중복 저장 차단", db.save_job(probe) is False))

        rows = [j for j in db.load_jobs() if j["company"] == PROBE_COMPANY]
        steps.append(("공고 조회", len(rows) == 1))

        if rows:
            job_id = rows[0]["id"]
            db.update_job(job_id, status="지원 완료", memo="연결 점검")
            updated = [j for j in db.load_jobs() if j["id"] == job_id]
            steps.append(
                ("상태·메모 수정", bool(updated) and updated[0]["status"] == "지원 완료")
            )

            if not args.keep:
                db.delete_job(job_id)
                gone = not any(j["id"] == job_id for j in db.load_jobs())
                steps.append(("삭제", gone))

        db.mark_seen(["__probe__"])
        steps.append(("NEW 뱃지 기록", True))

    except Exception as exc:
        print(f"\n❌ 점검 중 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print()
    for name, passed in steps:
        print(f"  {'✅' if passed else '❌'} {name}")

    failed = [n for n, p in steps if not p]
    print()
    if failed:
        print(f"❌ 실패한 단계: {', '.join(failed)}", file=sys.stderr)
        return 1

    saved_count = len(db.load_jobs())
    print(f"✅ 전부 정상입니다. 현재 보관함에 {saved_count}건이 저장되어 있습니다.")
    if storage.is_postgres():
        print("   앱을 재시작해도 이 데이터는 유지됩니다.")
    else:
        print("   ⚠️  SQLite는 Streamlit Cloud에서 재시작 시 초기화됩니다.")
        print("      영구 보관하려면 DATABASE_URL을 설정하세요 (README 참고).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
