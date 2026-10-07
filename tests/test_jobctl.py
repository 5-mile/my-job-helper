"""Claude Code 명령이 쓰는 jobctl.py 왕복 확인 (conftest가 임시 SQLite로 묶는다)."""

from __future__ import annotations

import io
import json

import jobctl
from jobhelper import db, profile as profile_mod


def _run(monkeypatch, capsys, argv, stdin=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin, ensure_ascii=False)))
    code = jobctl.main(argv)
    return code, capsys.readouterr().out


def _init():
    db.init_db()
    profile_mod.init_profile_tables()


def test_save_profile_merges_instead_of_wiping(monkeypatch, capsys):
    _init()
    _run(monkeypatch, capsys, ["save-profile"], {"career": "포스코 3년", "skills": "PLC"})
    _run(monkeypatch, capsys, ["save-profile"], {"certificates": "지게차"})

    _, out = _run(monkeypatch, capsys, ["profile"])
    data = json.loads(out)
    # 두 번째 저장이 첫 번째 값을 지우면 안 된다.
    assert data["profile"]["career"] == "포스코 3년"
    assert data["profile"]["certificates"] == "지게차"
    assert data["empty"] is False
    assert data["fabrication_rule"]


def test_save_job_then_saved_lists_it(monkeypatch, capsys):
    _init()
    job = {"company": "동서식품(주)", "position": "생산직 OP", "link": "https://x"}
    _, out = _run(monkeypatch, capsys, ["save"], job)
    assert "추가" in out
    _, out = _run(monkeypatch, capsys, ["save"], job)
    assert "이미" in out

    _, out = _run(monkeypatch, capsys, ["saved"])
    saved = json.loads(out)
    assert [j["company"] for j in saved] == ["동서식품(주)"]
    assert saved[0]["id"]


def test_save_letter_round_trip(monkeypatch, capsys, tmp_path):
    _init()
    f = tmp_path / "a.txt"
    f.write_text("  설비 보전 경험을 살려...  \n", encoding="utf-8")
    code, _ = _run(monkeypatch, capsys,
                   ["save-letter", "--company", "포스코", "--question", "지원 동기", "--file", str(f)])
    assert code == 0
    letters = profile_mod.load_cover_letters("포스코")
    assert letters[0]["answer"] == "설비 보전 경험을 살려..."

    # 기본 자소서는 문항 번호 순서대로 나와야 /apply 가 글 순서를 지킨다.
    for q in ("2. 직무 역량", "1. 지원동기"):
        f.write_text(q + " 본문", encoding="utf-8")
        _run(monkeypatch, capsys, ["save-letter", "--company", "기본", "--question", q, "--file", str(f)])
    _, out = _run(monkeypatch, capsys, ["letters", "--company", "기본"])
    assert [l["question"] for l in json.loads(out)] == ["1. 지원동기", "2. 직무 역량"]

    f.write_text("   ", encoding="utf-8")
    code, _ = _run(monkeypatch, capsys,
                   ["save-letter", "--company", "포스코", "--question", "지원 동기", "--file", str(f)])
    assert code == 1  # 빈 답변이 기존 답변을 덮어쓰면 안 된다


def test_done_request_strips_only_the_tag(monkeypatch, capsys):
    _init()
    _run(monkeypatch, capsys, ["save"], {"company": "머크", "position": "생산 OP", "memo": "[자소서 요청] 10/18 마감"})
    job_id = db.load_jobs()[0]["id"]
    code, _ = _run(monkeypatch, capsys, ["done-request", "--job-id", str(job_id)])
    assert code == 0
    assert db.load_jobs()[0]["memo"] == "10/18 마감"
