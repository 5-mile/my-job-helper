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

import ast
import importlib
import logging
import os
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

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)


def _derive_required(app_path: str = APP_PATH) -> dict[str, set[str]]:
    """app.py 를 읽어 `모듈.이름` 형태로 쓰이는 이름을 전부 뽑아낸다.

    위의 REQUIRED_ATTRS 를 손으로 관리하다 보면, 함수를 새로 추가할 때마다
    목록에 넣는 걸 잊어서 배포 후에야 AttributeError 로 드러난다. 실제로
    그 일이 반복돼서, 목록을 코드에서 직접 뽑도록 했다.

    `from jobhelper.config import CATEGORIES` 처럼 이름만 가져오는 경우는
    여기서 잡히지 않으므로(그건 import 시점에 바로 터진다) REQUIRED_ATTRS 가
    여전히 보완 역할을 한다.
    """
    try:
        with open(app_path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=app_path)
    except (OSError, SyntaxError) as exc:
        log.warning("app.py 를 읽지 못해 정적 목록만 씁니다: %s", exc)
        return {}

    # 지역 이름 -> jobhelper 하위 모듈 이름
    aliases: dict[str, str] = {}
    package_dir = os.path.dirname(os.path.abspath(__file__))

    def _is_module(dotted: str) -> bool:
        rel = dotted.split(".", 1)[1].replace(".", os.sep)
        base = os.path.join(package_dir, rel)
        return os.path.isfile(base + ".py") or os.path.isdir(base)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module or not node.module.startswith("jobhelper"):
                continue
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                if _is_module(dotted):
                    aliases[alias.asname or alias.name] = dotted
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("jobhelper") and alias.asname:
                    aliases[alias.asname] = alias.name

    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            found.setdefault(aliases[node.value.id], set()).add(node.attr)
    return found


def _required() -> dict[str, set[str]]:
    """정적 목록과 app.py 에서 뽑은 목록을 합친다."""
    merged: dict[str, set[str]] = {k: set(v) for k, v in REQUIRED_ATTRS.items()}
    for module_name, attrs in _derive_required().items():
        merged.setdefault(module_name, set()).update(attrs)
    return merged


REBOOT_HELP = (
    "코드는 새로 배포됐지만 서버가 옛 모듈을 그대로 쓰고 있습니다.\n\n"
    "**Manage app → 우측 위 ⋮ → Reboot app** 을 누르면 해결됩니다."
)


def _missing() -> list[str]:
    """로드된 모듈 중 필요한 이름이 빠진 것들."""
    stale = []
    for module_name, attrs in _required().items():
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
