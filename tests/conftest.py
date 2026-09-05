"""테스트가 프로젝트 폴더의 실제 파일을 건드리지 못하게 막는 안전장치.

예전에 setup_cloud 테스트가 SECRETS_FILE을 격리하지 않아, pytest를 돌릴 때마다
실제 streamlit_secrets.toml 이 테스트용 접속 문자열로 덮어써졌다. 그 파일을
그대로 붙여넣어 배포가 실패했다. 개별 테스트가 깜빡해도 안전하도록 여기서
일괄로 임시 경로에 묶어 둔다.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolate_project_files(tmp_path, monkeypatch):
    """실제 secrets 파일과 jobs.db 대신 임시 경로를 쓰게 한다."""
    import setup_cloud
    from jobhelper import storage

    monkeypatch.setattr(
        setup_cloud, "SECRETS_FILE", str(tmp_path / "secrets.toml"), raising=False
    )
    monkeypatch.setattr(
        storage, "SQLITE_PATH", str(tmp_path / "jobs.db"), raising=False
    )
