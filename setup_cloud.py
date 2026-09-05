"""클라우드 저장소 설정을 한 번에 끝내는 스크립트.

DATABASE_URL만 준비되면 나머지(연결 확인 → 스키마 생성 → 보관함 이전 →
Secrets 문구 출력)를 순서대로 처리한다.

    python setup_cloud.py            # 이전 전에 한 번 확인을 묻는다
    python setup_cloud.py --yes      # 묻지 않고 진행
    python setup_cloud.py --overwrite  # 대상의 같은 공고를 원본 내용으로 교체

DATABASE_URL이 없으면 어디서 받아 어떻게 넣는지 안내하고 멈춘다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from jobhelper import settings, storage
from jobhelper.migrate import migrate

ENV_TEMPLATE = 'DATABASE_URL=postgresql://postgres:비밀번호@db.xxxxx.supabase.co:5432/postgres'


def _mask(url: str) -> str:
    if not url or "@" not in url:
        return url or "(없음)"
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":")[0] if ":" in creds else creds
    return f"{scheme}://{user}:****@{host}"


def _print_missing_url_guide() -> None:
    print("DATABASE_URL이 없습니다. 아래 순서로 준비해 주세요.\n")
    print("1. https://supabase.com 에서 로그인 후 새 프로젝트를 만듭니다.")
    print("   (프로젝트를 만들 때 정한 Database Password를 기억해 두세요.)")
    print("2. 좌측 하단 ⚙ Project Settings → Database 로 들어갑니다.")
    print("3. 'Connection string' 항목에서 URI 탭의 문자열을 복사합니다.")
    print("   postgresql://postgres:[YOUR-PASSWORD]@db.xxxxx.supabase.co:5432/postgres")
    print("4. [YOUR-PASSWORD] 자리를 1번에서 정한 실제 비밀번호로 바꿉니다.")
    print(f"\n5. 이 폴더의 .env 파일에 아래 한 줄을 넣습니다:\n\n   {ENV_TEMPLATE}\n")

    env_path = settings.ENV_FILE
    if not os.path.exists(env_path):
        print(f"   ※ .env가 아직 없습니다. 먼저 만드세요:  cp .env.example .env")
    else:
        print(f"   ※ .env 위치: {env_path}")

    print("\n6. 다시 실행:  python setup_cloud.py")


def _print_secrets_guide(url: str) -> None:
    print("\n" + "=" * 64)
    print("이제 Streamlit Cloud에도 같은 값을 넣어야 합니다.")
    print("=" * 64)
    print("\n1. 배포한 앱에서 우측 하단 'Manage app' 클릭")
    print("2. 우측 위 ⋮ → Settings → Secrets")
    print("3. 아래 내용을 붙여넣고 Save:\n")
    print("-" * 64)
    print(f'DATABASE_URL = "{url}"')
    print("-" * 64)
    print("\n4. 다시 ⋮ → Reboot app")
    print("\n   (Reboot는 꼭 하세요. Streamlit Cloud는 새 코드를 받아도 이미 올라간")
    print("    모듈을 재사용해서 ImportError가 나는 경우가 있습니다.)")
    print("\n앱 사이드바 '💾 저장소'에 'PostgreSQL · 보관함이 영구 저장됩니다'가")
    print("보이면 성공입니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="클라우드 저장소 설정 한 번에 실행")
    parser.add_argument("--yes", "-y", action="store_true", help="확인을 묻지 않고 진행")
    parser.add_argument("--overwrite", action="store_true",
                        help="대상에 이미 있는 공고도 원본 내용으로 덮어씀")
    parser.add_argument("--source", default=storage.SQLITE_PATH, help="원본 SQLite 파일")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    print("=" * 64)
    print(" 보관함 영구 저장 설정")
    print("=" * 64 + "\n")

    # --- 1. DATABASE_URL 확인 ---
    url = storage.database_url()
    if not url:
        _print_missing_url_guide()
        return 1
    print(f"[1/4] DATABASE_URL 확인 ✅  {_mask(url)}")

    # --- 2. 연결 확인 ---
    ok, message = storage.health_check()
    if not ok:
        print(f"[2/4] 연결 ❌\n\n{message}\n", file=sys.stderr)
        print(
            "확인해 볼 것:\n"
            "  · 비밀번호 자리에 [YOUR-PASSWORD]가 그대로 남아 있지 않은지\n"
            "  · 비밀번호에 @ : / 같은 문자가 있다면 URL 인코딩이 필요합니다\n"
            "  · psycopg 설치 여부:  pip install \"psycopg[binary]\"\n"
            "  · Supabase 프로젝트가 일시 정지(pause) 상태는 아닌지",
            file=sys.stderr,
        )
        return 1
    print(f"[2/4] 연결 ✅  {message}")

    # --- 3. 옮길 내용 확인 ---
    if not os.path.exists(args.source):
        print(f"[3/4] 옮길 데이터 없음 — 원본 파일이 없습니다 ({args.source})")
        print("      새로 시작하는 상태이므로 이전은 건너뜁니다.")
        moved = 0
    else:
        preview = migrate(args.source, dry_run=True)
        pending = preview.total_written
        if pending == 0:
            print("[3/4] 옮길 데이터 없음 — 로컬 보관함이 비어 있습니다.")
            moved = 0
        else:
            print(f"[3/4] 옮길 데이터 확인 — 총 {pending}건")
            for entry in preview.tables:
                if entry.read:
                    print(f"       · {entry.table}: {entry.read}건")

            if not args.yes:
                print()
                answer = input("      이 데이터를 옮길까요? [Y/n] ").strip().lower()
                if answer and answer not in ("y", "yes"):
                    print("      이전을 건너뜁니다.")
                    _print_secrets_guide(url)
                    return 0

            result = migrate(args.source, overwrite=args.overwrite)
            if not result.ok:
                print("\n[4/4] 이전 실패 ❌", file=sys.stderr)
                for entry in result.tables:
                    if entry.error:
                        print(f"       · {entry.table}: {entry.error}", file=sys.stderr)
                return 1
            moved = result.total_written
            skipped = result.total_skipped
            suffix = f" (이미 있어 건너뜀 {skipped}건)" if skipped else ""
            print(f"       이전 완료 ✅ {moved}건{suffix}")

    # --- 4. 결과 확인 ---
    from jobhelper import db

    db.init_db()
    total = len(db.load_jobs())
    print(f"[4/4] 확인 ✅  현재 클라우드 DB의 보관함: {total}건")

    _print_secrets_guide(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
