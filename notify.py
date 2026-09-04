"""마감 임박 알림 실행 스크립트.

앱을 열지 않아도 돌도록 만든 진입점이다. 윈도우 작업 스케줄러나 cron에 걸어둔다.

    python notify.py                # 3일 이내 마감 공고 알림
    python notify.py --days 5       # 5일 이내
    python notify.py --dry-run      # 보내지 않고 내용만 출력
"""

from __future__ import annotations

import argparse
import logging
import sys

from jobhelper.notify import run


def main() -> int:
    parser = argparse.ArgumentParser(description="마감 임박 채용 공고 알림")
    parser.add_argument("--days", type=int, default=3, help="며칠 이내 마감을 알릴지 (기본 3)")
    parser.add_argument("--dry-run", action="store_true", help="발송하지 않고 내용만 출력")
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 로그")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    result = run(within_days=args.days, dry_run=args.dry_run)

    if result["found"] == 0:
        print(f"마감 {args.days}일 이내인 보관 공고가 없습니다.")
        return 0

    if result["to_send"] == 0:
        print(f"마감 임박 {result['found']}건이 있지만 오늘 이미 알림을 보냈습니다.")
        return 0

    print(result.get("message", ""))

    if args.dry_run:
        print("\n[dry-run] 실제로 발송하지 않았습니다.")
        return 0

    if result["sent"]:
        print(f"\n발송 완료: {', '.join(result['channels'])}")
        return 0

    print(
        "\n발송 채널이 설정되지 않았습니다. "
        ".env에 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 또는 SMTP_* 값을 넣어주세요.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
