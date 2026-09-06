"""API 키와 알림 설정을 한 곳에서 읽는다.

우선순위: 환경 변수 > .env 파일 > Streamlit secrets.
키가 없으면 조용히 None을 돌려주고, 해당 기능만 꺼진 채로 앱은 정상 동작한다.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    """의존성 없이 .env를 아주 단순하게 읽는다 (KEY=VALUE, # 주석)."""
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
    except OSError as exc:
        log.warning(".env를 읽지 못했습니다: %s", exc)
    return values


def _from_streamlit(name: str) -> str | None:
    try:
        import streamlit as st

        value = st.secrets.get(name)  # type: ignore[union-attr]
        return str(value) if value else None
    except Exception:
        # streamlit 밖(스케줄 스크립트)에서 돌거나 secrets.toml이 없는 경우
        return None


def get(name: str, default: str | None = None) -> str | None:
    """설정값 하나를 읽는다. 없으면 default."""
    value = os.environ.get(name) or _dotenv().get(name) or _from_streamlit(name)
    return value or default


def get_bool(name: str, default: bool = False) -> bool:
    raw = get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


# --- 개별 설정 --------------------------------------------------------------
def nps_service_key() -> str | None:
    """공공데이터포털 국민연금 사업장 API 인증키 (Decoding 키)."""
    return get("NPS_SERVICE_KEY")


def worknet_auth_key() -> str | None:
    """워크넷(고용24) 오픈API 인증키."""
    return get("WORKNET_AUTH_KEY")


def telegram_config() -> tuple[str, str] | None:
    token = get("TELEGRAM_BOT_TOKEN")
    chat_id = get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return token, chat_id
    return None


def email_config() -> dict[str, str] | None:
    """SMTP 발송 설정. 하나라도 빠지면 None."""
    conf = {
        "host": get("SMTP_HOST", "smtp.gmail.com") or "",
        "port": get("SMTP_PORT", "587") or "587",
        "user": get("SMTP_USER") or "",
        "password": get("SMTP_PASSWORD") or "",
        "to": get("ALERT_EMAIL_TO") or get("SMTP_USER") or "",
    }
    if conf["user"] and conf["password"] and conf["to"]:
        return conf
    return None


def saramin_api_key() -> str | None:
    """사람인 공식 오픈API 인증키."""
    return get("SARAMIN_API_KEY")


def public_jobs_key() -> str | None:
    """공공데이터포털 공공기관 채용정보 인증키."""
    return get("PUBLIC_JOBS_KEY")


def anthropic_api_key() -> str | None:
    """Claude API 키. 자소서·적합도 분석에만 쓰인다."""
    return get("ANTHROPIC_API_KEY")


def database_url() -> str | None:
    """PostgreSQL 접속 문자열. 없으면 로컬 SQLite를 쓴다."""
    return get("DATABASE_URL")


def missing_keys() -> list[str]:
    """설정되지 않은 선택 기능 목록 (UI 안내용)."""
    missing = []
    if not database_url():
        missing.append("DATABASE_URL (보관함 영구 저장 - 클라우드 배포 시 필수)")
    if not nps_service_key():
        missing.append("NPS_SERVICE_KEY (기업 규모·보수 정보)")
    if not worknet_auth_key():
        missing.append("WORKNET_AUTH_KEY (워크넷 공고)")
    if not saramin_api_key():
        missing.append("SARAMIN_API_KEY (사람인 공식 API)")
    if not public_jobs_key():
        missing.append("PUBLIC_JOBS_KEY (공공기관 채용정보)")
    if not anthropic_api_key():
        missing.append("ANTHROPIC_API_KEY (자소서 초안·적합도 분석)")
    if not telegram_config() and not email_config():
        missing.append("TELEGRAM_* 또는 SMTP_* (마감 알림)")
    return missing
