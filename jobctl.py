"""Claude Code 명령(/rank, /apply)이 앱 데이터를 읽고 쓰는 창구.

앱의 AI 탭은 Anthropic API 키가 있어야 돌지만, Claude Code 안에서는 구독으로
같은 일을 할 수 있다. 이 스크립트는 그때 필요한 데이터만 JSON으로 꺼내 주고,
결과(보관, 자소서)를 앱과 같은 DB에 돌려 넣는다.

    python jobctl.py profile                       # 프로필 + 작성 규칙
    python jobctl.py save-profile --file p.json    # 프로필 저장(일부 항목만도 가능)
    python jobctl.py search 생산 품질 --pages 2    # 사람인 실시간 수집
    python jobctl.py detail <공고주소>             # 공고 본문
    python jobctl.py saved                         # 보관함
    python jobctl.py save --file job.json          # 보관함에 추가
    python jobctl.py letters --company 기본          # 저장된 자소서 (기본 자소서 등)
    python jobctl.py save-letter --company 포스코 --question "지원 동기" --file a.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

from jobhelper import ai, console, db, profile as profile_mod
from jobhelper.config import REQUEST_TIMEOUT, SARAMIN_ALL_SORTS, USER_AGENT
from jobhelper.scrapers.saramin import fetch_saramin_jobs_detailed

console.setup()

# 채점에 쓰는 필드만. 전부 내보내면 Claude Code 문맥을 쓸데없이 잡아먹는다.
JOB_FIELDS = (
    "company", "position", "sector", "location", "career", "education",
    "employment", "salary", "date", "deadline", "link",
)


def _read_json(path: str | None):
    """--file 이 있으면 파일에서, 없으면 표준입력에서. (PowerShell엔 < 리다이렉트가 없다.)"""
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


def _out(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1))


def cmd_profile(_args) -> int:
    p = profile_mod.load_profile()
    _out({
        "profile": profile_mod._to_dict(p),
        "empty": not (p.career or p.skills or p.episodes),
        "fabrication_rule": ai.FABRICATION_RULE,
        "default_questions": ai.DEFAULT_QUESTIONS,
    })
    return 0


def cmd_save_profile(_args) -> int:
    # 기존 값 위에 덮어쓴다. 일부 항목만 보내도 나머지는 지워지지 않는다.
    data = {**profile_mod._to_dict(profile_mod.load_profile()), **_read_json(_args.file)}
    profile_mod.save_profile(profile_mod._from_dict(data))
    print("프로필을 저장했습니다. 앱의 '내 프로필' 탭에도 그대로 보입니다.")
    return 0


def cmd_search(args) -> int:
    sorts = list(SARAMIN_ALL_SORTS) if args.all_sorts else "relation"
    jobs, diag = fetch_saramin_jobs_detailed(args.keywords, sorts, args.pages, args.exclude)
    if diag.warning:
        print(diag.warning, file=sys.stderr)
    _out([
        {"i": i, **{k: j.get(k) for k in JOB_FIELDS if j.get(k)}}
        for i, j in enumerate(jobs[: args.limit])
    ])
    return 0


# 공고 페이지 본문은 iframe(view-detail)에 따로 있다. 바깥 페이지는 메뉴뿐이다.
DETAIL_URL = "https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={}"
DETAIL_MAX_CHARS = 6000


def cmd_detail(args) -> int:
    m = re.search(r"rec_idx=(\d+)", args.target) or re.fullmatch(r"(\d+)", args.target)
    if not m:
        print("사람인 공고 주소나 rec_idx 번호가 아닙니다.", file=sys.stderr)
        return 1
    resp = requests.get(DETAIL_URL.format(m.group(1)), headers={"User-Agent": USER_AGENT},
                        timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = " ".join(BeautifulSoup(resp.text, "lxml").get_text(" ").split())
    if len(text) < 200:
        # 본문이 통이미지인 공고. 지어내지 말고 사용자에게 붙여넣기를 부탁해야 한다.
        print("IMAGE_ONLY: 본문이 이미지라 글자를 읽을 수 없습니다.")
        return 2
    print(text[:DETAIL_MAX_CHARS])
    return 0


def cmd_saved(args) -> int:
    _out([
        {"id": j["id"], "status": j["status"], "memo": j.get("memo") or "",
         **{k: j.get(k) for k in JOB_FIELDS if j.get(k)}}
        for j in db.load_jobs(args.status)
    ])
    return 0


def cmd_save(_args) -> int:
    job = _read_json(_args.file)
    job.setdefault("source", "Claude Code")
    added = db.save_job(job)
    print("보관함에 추가했습니다." if added else "이미 보관함에 있습니다.")
    return 0


REQUEST_TAG = "[자소서 요청]"  # 텔레그램 봇의 '✍️ 자소서 부탁' 이 메모에 붙이는 표시


def cmd_done_request(args) -> int:
    job = next((j for j in db.load_jobs() if j["id"] == args.job_id), None)
    if job is None:
        print(f"보관함에 id {args.job_id} 공고가 없습니다.", file=sys.stderr)
        return 1
    db.update_job(args.job_id, memo=(job.get("memo") or "").replace(REQUEST_TAG, "").strip())
    print(f"자소서 요청 표시를 지웠습니다: {job['company']}")
    return 0


def cmd_letters(args) -> int:
    # 문항 제목이 "1. ...", "2. ..." 로 시작하므로 제목순이 곧 글의 순서다.
    letters = sorted(profile_mod.load_cover_letters(args.company), key=lambda l: l["question"])
    _out([{"company": l["company"], "question": l["question"], "answer": l["answer"]} for l in letters])
    return 0


def cmd_save_letter(args) -> int:
    with open(args.file, encoding="utf-8") as fh:
        answer = fh.read().strip()
    if not answer:
        print("빈 답변은 저장하지 않습니다.", file=sys.stderr)
        return 1
    profile_mod.save_cover_letter(args.job_id, args.company, args.question, answer)
    print(f"저장했습니다: {args.company} / {args.question} ({len(answer)}자)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("profile").set_defaults(fn=cmd_profile)
    s = sub.add_parser("save-profile")
    s.add_argument("--file", help="UTF-8 JSON 파일 (없으면 표준입력)")
    s.set_defaults(fn=cmd_save_profile)

    s = sub.add_parser("search")
    s.add_argument("keywords", nargs="+")
    s.add_argument("--pages", type=int, default=2)
    s.add_argument("--all-sorts", action="store_true", help="정렬 3종을 합쳐 더 많이")
    s.add_argument("--exclude", nargs="*", default=[])
    s.add_argument("--limit", type=int, default=60)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("detail")
    s.add_argument("target", help="사람인 공고 주소 또는 rec_idx")
    s.set_defaults(fn=cmd_detail)

    s = sub.add_parser("saved")
    s.add_argument("--status")
    s.set_defaults(fn=cmd_saved)

    s = sub.add_parser("save")
    s.add_argument("--file", help="UTF-8 JSON 파일 (없으면 표준입력)")
    s.set_defaults(fn=cmd_save)

    s = sub.add_parser("done-request", help="자소서 요청 표시 지우기")
    s.add_argument("--job-id", type=int, required=True)
    s.set_defaults(fn=cmd_done_request)

    s = sub.add_parser("letters")
    s.add_argument("--company", help="예: 기본 (없으면 전부)")
    s.set_defaults(fn=cmd_letters)

    s = sub.add_parser("save-letter")
    s.add_argument("--company", required=True)
    s.add_argument("--question", required=True)
    s.add_argument("--file", required=True, help="답변이 든 UTF-8 텍스트 파일")
    s.add_argument("--job-id", type=int)
    s.set_defaults(fn=cmd_save_letter)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
