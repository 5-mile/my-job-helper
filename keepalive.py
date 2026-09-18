"""데이터베이스를 깨워 두는 스크립트.

Supabase 무료 플랜은 **7일간 접속이 없으면 프로젝트를 자동으로 일시 정지**한다.
정지되면 주소가 내려가고, 앱은 `tenant not found` 로 붙지 못한다. 되살리려면
대시보드에서 직접 Restore 를 눌러야 한다.

그래서 GitHub Actions가 매일 이걸 한 번 실행한다. 쿼리 한 번이면 '접속이 있었다'
로 쳐 주므로 정지되지 않는다.

연결에 실패하면 0이 아닌 값으로 끝나므로, Actions 탭에 빨간 X가 남는다.
알림 메일도 오니 DB가 죽은 걸 며칠 뒤에야 알게 되는 일이 없다.

    python keepalive.py
"""

from __future__ import annotations

import sys

from jobhelper import console, storage

console.setup()


def main() -> int:
    if not storage.is_postgres():
        print("DATABASE_URL이 없습니다. 깨울 대상이 없으므로 넘어갑니다.")
        return 0

    try:
        with storage.connect() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
        assert row is not None
    except Exception as exc:
        print("❌ 데이터베이스에 붙지 못했습니다.")
        print(f"   {exc}")
        print()
        print(storage.explain_connection_error(exc))
        return 1

    print("✅ 데이터베이스가 깨어 있습니다. 7일 자동 정지 시계가 초기화됐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
