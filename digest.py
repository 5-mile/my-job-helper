"""매일 아침 새 공고 요약을 텔레그램으로 보낸다.

    python digest.py              # 새 공고 상위 5건 발송
    python digest.py --limit 10   # 10건
    python digest.py --dry-run    # 보내지 않고 내용만 출력

지역과 검색어는 .env 의 DIGEST_REGIONS, DIGEST_KEYWORDS 로 바꿀 수 있다 (쉼표 구분).
"""

from __future__ import annotations

import argparse
import logging
import sys

from jobhelper import console
from jobhelper.digest import run

console.setup()


def main() -> int:
    parser = argparse.ArgumentParser(description="새 공고 요약 발송")
    parser.add_argument("--limit", type=int, help="보낼 공고 수 (기본 5)")
    parser.add_argument("--dry-run", action="store_true", help="발송하지 않고 내용만 출력")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    result = run(limit=args.limit, dry_run=args.dry_run)
    print(f"수집 {result['collected']}건 → 새로 보낼 공고 {result['new']}건")
    if not result["new"]:
        return 0
    print(result["message"])
    if args.dry_run:
        print("\n[dry-run] 실제로 발송하지 않았습니다.")
        return 0
    if result["sent"]:
        print("\n텔레그램 발송 완료")
        return 0
    print("\n텔레그램 발송 실패: .env 의 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 확인하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
