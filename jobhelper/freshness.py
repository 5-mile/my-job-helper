"""Streamlit Cloud의 '오래된 모듈' 문제를 스스로 복구한다.

Streamlit Cloud는 git push로 새 코드를 받아도 파이썬 프로세스를 새로 띄우지
않는 경우가 있다. 그러면 새 app.py 가 이미 메모리에 올라간 옛 jobhelper 모듈을
호출하면서 ImportError / AttributeError 로 죽는다. 사용자는 원인을 알 수 없고
Manage app -> Reboot 을 눌러야만 풀린다.

여기서는 app.py 가 필요로 하는 함수가 실제로 있는지 먼저 확인하고, 없으면
모듈을 다시 읽어들여 스스로 고친다. 그래도 안 되면 무엇을 해야 하는지
사람이 읽을 수 있는 문구를 돌려준다.
"""

from __future__ import annotations

import importlib
import logging
import sys

log = logging.getLogger(__name__)

# app.py 가 쓰는 것 중, 나중에 추가되어 옛 모듈에는 없을 수 있는 이름들
REQUIRED_ATTRS: dict[str, tuple[str, ...]] = {
    "jobhelper.ui": ("trend_bars", "status_strip", "company_badges", "job_card"),
    "jobhelper.insights": ("init_insight_tables", "record_sightings", "annotate_agencies"),
    "jobhelper.storage": ("validate_url", "warn_direct_connection"),
    "jobhelper.profile": ("init_profile_tables", "load_profile"),
    "jobhelper.ai": ("is_available", "draft_cover_letter"),
    "jobhelper.config": ("SARAMIN_ALL_SORTS", "CATEGORIES"),
    "jobhelper.scrapers.saramin": ("fetch_saramin_jobs_detailed",),
    "jobhelper.scrapers.saramin_api": ("fetch_saramin_api_jobs",),
    "jobhelper.scrapers.publicjobs": ("fetch_public_jobs",),
}

REBOOT_HELP = (
    "코드는 새로 배포됐지만 서버가 옛 모듈을 그대로 쓰고 있습니다.\n\n"
    "**Manage app → 우측 위 ⋮ → Reboot app** 을 누르면 해결됩니다."
)


def _missing() -> list[str]:
    """로드된 모듈 중 필요한 이름이 빠진 것들."""
    stale = []
    for module_name, attrs in REQUIRED_ATTRS.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue  # 아직 import 전이면 정상적으로 새로 읽힌다
        if any(not hasattr(module, attr) for attr in attrs):
            stale.append(module_name)
    return stale


def ensure_fresh() -> str:
    """오래된 모듈이 있으면 다시 읽는다.

    문제가 없거나 스스로 고쳤으면 빈 문자열, 못 고쳤으면 안내 문구를 돌려준다.
    """
    stale = _missing()
    if not stale:
        return ""

    log.warning("오래된 모듈을 감지해 다시 읽습니다: %s", ", ".join(stale))

    # 의존성이 얕은 것부터 다시 읽어야 서로 어긋나지 않는다.
    order = [
        "jobhelper.config", "jobhelper.settings", "jobhelper.dates",
        "jobhelper.classify", "jobhelper.storage", "jobhelper.db",
        "jobhelper.company_info", "jobhelper.notify", "jobhelper.migrate",
        "jobhelper.profile", "jobhelper.ai", "jobhelper.insights", "jobhelper.ui",
        "jobhelper.scrapers.saramin", "jobhelper.scrapers.saramin_api",
        "jobhelper.scrapers.naver_blog", "jobhelper.scrapers.worknet",
        "jobhelper.scrapers.publicjobs", "jobhelper.scrapers",
    ]
    for module_name in order:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        try:
            importlib.reload(module)
        except Exception as exc:
            log.error("%s 재로드 실패: %s", module_name, exc)

    still = _missing()
    if still:
        log.error("재로드 후에도 오래된 모듈이 남았습니다: %s", ", ".join(still))
        return REBOOT_HELP
    log.info("모듈을 새로 읽어 정상화했습니다.")
    return ""
