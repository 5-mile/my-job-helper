"""텔레그램 버튼 봇 배포·연결.

    python telegram_bot.py setup    # 봇 함수를 Supabase에 올리고 텔레그램과 연결 (다시 돌려도 됨)
    python telegram_bot.py status   # 연결 상태 확인

필요한 값 (.env):
    DATABASE_URL           Supabase 접속 문자열 (프로젝트 ID를 여기서 읽는다)
    TELEGRAM_BOT_TOKEN     @BotFather 토큰
    TELEGRAM_CHAT_ID       내 채팅 ID (이 채팅에서 온 것만 처리한다)
    SUPABASE_ACCESS_TOKEN  supabase.com/dashboard/account/tokens 에서 만든 토큰 (배포할 때만 필요)

봇 코드는 supabase/functions/telegram-bot/index.ts 다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys

import requests

from jobhelper import console, digest, settings, storage

console.setup()

FUNCTION = "telegram-bot"
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase", "functions", FUNCTION, "index.ts")
TIMEOUT = 60


def _project_ref() -> str:
    m = re.search(r"postgres\.([a-z0-9]+)[:@]", settings.get("DATABASE_URL") or "")
    if not m:
        sys.exit("DATABASE_URL 에서 Supabase 프로젝트 ID를 찾지 못했습니다 (postgres.<ID> 형태여야 합니다).")
    return m.group(1)


def _need(name: str) -> str:
    value = settings.get(name)
    if not value:
        sys.exit(f".env 에 {name} 가 없습니다. 이 파일 맨 위 설명을 보세요.")
    return value


def _tg(token: str, method: str, **params) -> dict:
    return requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=TIMEOUT).json()


def _save_config(values: dict[str, str]) -> None:
    with storage.connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS bot_config (key TEXT PRIMARY KEY, value TEXT)")
        for key, value in values.items():
            conn.execute("DELETE FROM bot_config WHERE key = ?", (key,))
            conn.execute("INSERT INTO bot_config (key, value) VALUES (?, ?)", (key, value))


def setup() -> int:
    if not storage.is_postgres():
        sys.exit("봇은 Supabase DB를 써야 합니다. DATABASE_URL 을 확인하세요.")
    ref, token = _project_ref(), _need("TELEGRAM_BOT_TOKEN")
    chat_id, access = _need("TELEGRAM_CHAT_ID"), _need("SUPABASE_ACCESS_TOKEN")

    # 1. 봇이 읽는 표를 만든다. 비밀값은 매번 새로 만들어 이전 것을 무효로 한다.
    digest.init_digest_pool()
    webhook_secret = secrets.token_urlsafe(32)
    # bot_token 도 넣어 두면 GitHub Actions가 DATABASE_URL 만으로 매일 요약을 보낸다 (Secrets 추가 불필요).
    _save_config({"chat_id": str(chat_id), "webhook_secret": webhook_secret, "bot_token": token})
    storage.enable_rls_on_public_tables()  # bot_config·digest_pool 도 REST로 못 읽게
    print("1/4 DB 준비 완료")

    # 2. 함수 배포. 텔레그램은 JWT를 보내지 않으므로 검증을 끄고, 대신 비밀값 헤더로 막는다.
    with open(SOURCE, "rb") as fh:
        resp = requests.post(
            f"https://api.supabase.com/v1/projects/{ref}/functions/deploy",
            params={"slug": FUNCTION},
            headers={"Authorization": f"Bearer {access}"},
            files={
                "metadata": (None, json.dumps({"name": FUNCTION, "entrypoint_path": "index.ts", "verify_jwt": False}), "application/json"),
                "file": ("index.ts", fh, "application/typescript"),
            },
            timeout=TIMEOUT,
        )
    if resp.status_code >= 300:
        sys.exit(f"함수 배포 실패 ({resp.status_code}): {resp.text[:300]}")
    print("2/4 봇 함수 배포 완료")

    # 3. 텔레그램이 버튼·메시지를 이 함수로 보내게 한다.
    url = f"https://{ref}.supabase.co/functions/v1/{FUNCTION}"
    r = _tg(token, "setWebhook", url=url, secret_token=webhook_secret,
            allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    if not r.get("ok"):
        sys.exit(f"텔레그램 연결 실패: {r.get('description')}")
    print("3/4 텔레그램 연결 완료")

    # 4. 자체 점검: 비밀값 없이는 막히고, 있으면 보관함 목록을 돌려줘야 한다.
    blocked = requests.post(url, json={}, timeout=TIMEOUT).status_code
    probe = requests.post(
        url, timeout=TIMEOUT,
        headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
        json={"message": {"chat": {"id": int(chat_id)}, "text": "📁 보관함"}},
    )
    ok = blocked == 403 and probe.ok and probe.json().get("method") == "sendMessage"
    print(f"4/4 점검 {'통과' if ok else '실패'} (외부 차단 {blocked}, 응답 {probe.status_code})")
    if not ok:
        print(probe.text[:300])
        return 1

    _tg(token, "sendMessage", chat_id=chat_id, text="✅ 버튼 봇이 연결됐습니다. 아래 메뉴를 눌러 보세요.",
        reply_markup={"keyboard": [[{"text": "📋 공고 더 보기"}, {"text": "⏰ 마감 임박"}, {"text": "📁 보관함"}]],
                      "resize_keyboard": True, "is_persistent": True})
    print("\n텔레그램에 메뉴를 보냈습니다. 이제 SUPABASE_ACCESS_TOKEN 은 지워도 됩니다 (다시 배포할 때만 필요).")
    return 0


def status() -> int:
    info = _tg(_need("TELEGRAM_BOT_TOKEN"), "getWebhookInfo").get("result", {})
    print("연결 주소:", info.get("url") or "(없음 — setup 필요)")
    print("대기 중인 업데이트:", info.get("pending_update_count"))
    if info.get("last_error_message"):
        print("마지막 오류:", info["last_error_message"])
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["setup", "status"])
    sys.exit({"setup": setup, "status": status}[parser.parse_args().command]())
