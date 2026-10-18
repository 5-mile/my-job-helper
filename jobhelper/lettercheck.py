"""자소서 기계 검사: 글자 수, 문항 간 같은 숫자 반복, AI·상투 문장 티.

    python jobctl.py check --file letter.md [--limits 500,700]

규칙은 아래 두 저장소에서 생산직 자소서에 맞는 것만 골라 옮겼다.
- jasoseo-plugin (https://github.com/KangRyun/jasoseo-plugin, MIT, KangRyun):
  글자 수 세는 법, 문항 간 소재(숫자) 중복, 상투어·번역투·빈 결의 목록, 같은 종결 3연속
- career-docs (https://github.com/kordp888/career-docs-skill, Apache-2.0,
  Copyright 2026 Derrick Hwang): 한국어 AI 티 규칙과 E/W 등급
  "This product includes software developed by Derrick Hwang."

정규식은 1차 거름망이다. 0건이어도 사람이 다시 읽는다.
"""

from __future__ import annotations

import re
from collections import defaultdict

# (이름, 정규식, 등급, 고치는 법). E 는 반드시 고친다, W 는 다듬는다.
RULES = [
    ("긴 줄표", r"—", "E", "쉼표·콜론·마침표로 바꾼다"),
    ("상투어", r"최선을 다|끊임없이|누구보다|열정을|밑거름|발판|값진 경험|소중한 경험|많은 것을 배|"
              r"성장할 수 있었|시너지|뼈를 묻|최고의|핵심 인재|든든한|한층 성장|원활한 소통|팀워크를 발휘",
     "E", "그 말을 증명하는 장면이나 사실로 바꾼다"),
    ("빈 결의", r"기여하겠습니다|이바지하겠습니다|보답하겠습니다|함께 성장하|"
               r"(인재|전문가|일원|사람)(가|이|으로|로) (되겠습니다|거듭나겠습니다|성장하겠습니다)",
     "E", "입사 후 실제로 할 행동으로 바꾼다"),
    ("과장어", r"혁신적|획기적|패러다임|완벽[한히하]", "E", "구체적 사실로 바꾼다"),
    ("지킬 수 없는 약속", r"불량\s*0|불량률\s*0|100\s*%|무결점", "W", "실제 해낼 행동으로 (과거 사실이면 그대로)"),
    ("번역투", r"[을를]\s*통[해하]|에\s*있어서|에 의해|되어지|되어졌|으로부터|저의 경우|적인 측면",
     "W", "'~로', '~에서' 처럼 짧게"),
    ("신념 선언", r"(라고|이라고|다고) (믿습니다|확신합니다)", "W", "믿음 대신 그렇게 한 장면을 쓴다"),
    ("일반 명제", r"(것|품질|안전|원칙|신뢰|기록|기본)(이|은|는|이란) [가-힣 ,]+(이다|입니다)[.\s]",
     "W", "누구나 할 말이다. 내 경험의 결론으로 바꾼다"),
    ("해라체", r"(?m)(했다|한다|이다|였다|된다)\.\s*$", "W", "본문은 존댓말로"),
]

# 성과 숫자만 본다. "1년", "2교대" 같은 기간·근무 형태는 여러 문항에 나와도 괜찮다.
NUM_UNIT = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|배|건|ppm|PPM)")
_HEADER = re.compile(r"^(■|##\s|\[문항)")


def split_sections(text: str) -> list[tuple[str, str]]:
    """'■ 1. 지원동기', '## 문항 1', '[문항 1]' 줄로 나눈다. 머리줄이 없으면 전체가 한 문항."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if _HEADER.match(line.strip()):
            sections.append((line.strip(), []))
        elif sections:
            sections[-1][1].append(line)
        elif line.strip():
            sections.append(("전체", [line]))
    return [(h, "\n".join(body).strip()) for h, body in sections]


def count(body: str) -> tuple[int, int]:
    """(공백 포함, 공백 제외). 줄바꿈은 세지 않는다 — 지원 폼 대부분이 이 기준."""
    flat = body.replace("\r", "").replace("\n", "")
    return len(flat), len(re.sub(r"\s", "", flat))


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if len(s.strip()) > 5]


def check(text: str, limits: list[int] | None = None) -> dict:
    sections = split_sections(text)
    limits = limits or []
    out = {"sections": [], "repeated_numbers": {}, "errors": 0}
    seen = defaultdict(set)

    for i, (header, body) in enumerate(sections):
        with_sp, without_sp = count(body)
        info = {"header": header, "chars": with_sp, "chars_no_space": without_sp, "hits": []}
        limit = limits[i] if i < len(limits) else (limits[-1] if len(limits) == 1 else None)
        if limit:
            info["limit"] = limit
            if with_sp > limit:
                info["hits"].append(("E", "글자 수 초과", f"{with_sp}/{limit}자", "줄인다"))
            elif with_sp < limit * 0.8:
                info["hits"].append(("W", "글자 수 부족", f"{with_sp}/{limit}자", "제한의 90% 안팎까지 채운다"))

        for name, pat, sev, fix in RULES:
            for m in re.finditer(pat, body):
                s, e = max(0, m.start() - 15), min(len(body), m.end() + 10)
                info["hits"].append((sev, name, body[s:e].replace("\n", " "), fix))

        ss = _sentences(body)
        # "~습니다" 는 거의 모든 문장의 끝이라 어간까지 본다 ("확인했습니다" × 3).
        ends = [re.sub(r"[.!?\"')\]]+$", "", s)[-5:] for s in ss]
        for j in range(len(ends) - 2):
            if ends[j] == ends[j + 1] == ends[j + 2]:
                info["hits"].append(("W", "같은 종결 3연속", "…" + ss[j + 2][-25:], "문장 끝을 바꾼다"))
                break

        for m in NUM_UNIT.finditer(body):
            seen[re.sub(r"\s", "", m.group())].add(i)
        out["errors"] += sum(1 for h in info["hits"] if h[0] == "E")
        out["sections"].append(info)

    out["repeated_numbers"] = {n: sorted(ix) for n, ix in seen.items() if len(ix) > 1}
    return out


def report(result: dict) -> str:
    lines = []
    for info in result["sections"]:
        lim = f" / 제한 {info['limit']}" if "limit" in info else ""
        lines.append(f"{info['header']}: 공백 포함 {info['chars']}자 · 제외 {info['chars_no_space']}자{lim}")
        for sev, name, ctx, fix in sorted(info["hits"], key=lambda h: h[0]):
            lines.append(f"  [{sev}] {name}: …{ctx}… → {fix}")
    for num, ix in result["repeated_numbers"].items():
        heads = ", ".join(result["sections"][i]["header"] for i in ix)
        lines.append(f"[W] 같은 숫자 '{num}' 이 여러 문항에 나옴 ({heads}) → 한 문항에만 두고 다른 곳은 다른 소재로")
    lines.append("")
    lines.append(f"실패: 반드시 고칠 것(E) {result['errors']}건" if result["errors"] else "통과 (E 없음). W 는 읽어 보고 판단")
    return "\n".join(lines)

