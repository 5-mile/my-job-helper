"""Windows 콘솔에서 이모지·한글이 깨지거나 죽지 않게 표준 출력을 손본다.

한국어 Windows의 기본 콘솔 코드페이지는 cp949라서, `✅` 같은 문자를 print 하면
UnicodeEncodeError로 스크립트가 통째로 죽는다. 점검 스크립트가 정작 점검 결과를
보여주다 죽으면 곤란하므로, 진입점에서 이 함수를 한 번 불러 준다.
"""

from __future__ import annotations

import sys


def setup() -> None:
    """stdout/stderr를 UTF-8로 바꾼다. 안 되면 표현 못 하는 글자만 대체한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 파이프로 감싸인 경우 등
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                reconfigure(errors="replace")
            except Exception:
                pass
