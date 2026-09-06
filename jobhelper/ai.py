"""Claude API로 자소서 초안과 공고 적합도 분석을 만든다.

설계 원칙: **프로필에 있는 사실만 쓴다.**
없는 경력을 지어낸 자소서는 서류에서 걸리거나 면접에서 무너지므로, 프롬프트와
후처리 양쪽에서 막는다. 재료가 부족하면 지어내는 대신 무엇이 부족한지 알린다.

ANTHROPIC_API_KEY가 없으면 이 모듈의 기능만 꺼지고 앱은 정상 동작한다.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from . import settings
from .profile import Profile

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"

# 자소서는 길게 나올 수 있어 스트리밍으로 받는다 (HTTP 타임아웃 회피).
COVER_LETTER_MAX_TOKENS = 8000
ANALYSIS_MAX_TOKENS = 4000

FABRICATION_RULE = (
    "가장 중요한 규칙: 아래 '지원자 프로필'에 적힌 사실만 사용하세요. "
    "프로필에 없는 경력, 수치, 자격증, 회사명, 성과를 절대 지어내지 마세요. "
    "재료가 부족하면 빈약하게라도 사실만 쓰고, 무엇이 더 필요한지 따로 알려주세요."
)


def is_available() -> bool:
    return bool(settings.anthropic_api_key())


def _client():
    """API 키가 없으면 None."""
    key = settings.anthropic_api_key()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:  # pragma: no cover - 환경 의존
        log.warning("anthropic 패키지가 없습니다. pip install anthropic")
        return None
    return anthropic.Anthropic(api_key=key)


def _job_text(job: dict[str, Any]) -> str:
    """공고 하나를 모델이 읽을 텍스트로."""
    fields = [
        ("회사", job.get("company")),
        ("공고명", job.get("position")),
        ("직무 분야", job.get("sector")),
        ("근무지", job.get("location")),
        ("경력 조건", job.get("career")),
        ("학력 조건", job.get("education")),
        ("고용 형태", job.get("employment")),
        ("급여", job.get("salary")),
        ("마감", job.get("date")),
    ]
    lines = [f"- {label}: {value}" for label, value in fields if value]
    welfares = job.get("welfares") or []
    if welfares:
        lines.append(f"- 복지 키워드: {', '.join(welfares)}")
    info = job.get("company_info") or {}
    if info.get("found"):
        if info.get("employees"):
            lines.append(f"- 국민연금 가입자 수(참고): 약 {info['employees']:,}명")
        if info.get("avg_monthly_pay"):
            lines.append(f"- 추정 월평균 보수(참고): 약 {round(info['avg_monthly_pay']/10000):,}만원")
    return "\n".join(lines)


# ==========================================
# 적합도 분석
# ==========================================
class FitAnalysis(BaseModel):
    score: int = Field(description="0~100 적합도 점수")
    verdict: str = Field(description="'지원 추천' / '조건부 추천' / '보류' 중 하나")
    summary: str = Field(description="한 문장 요약")
    matches: list[str] = Field(description="프로필과 공고가 맞는 지점 (근거 포함)")
    gaps: list[str] = Field(description="부족하거나 확인이 필요한 조건")
    actions: list[str] = Field(description="지원 전에 준비하면 좋을 것")


def analyze_fit(profile: Profile, job: dict[str, Any]) -> FitAnalysis | None:
    """내 프로필과 공고를 비교한다. 키가 없거나 실패하면 None."""
    client = _client()
    if client is None:
        return None

    system = (
        "당신은 한국 채용 시장을 잘 아는 커리어 코치입니다. "
        "지원자 프로필과 채용 공고를 비교해 지원할 가치가 있는지 냉정하게 판단합니다. "
        "듣기 좋은 말보다 정확한 판단이 지원자에게 도움이 됩니다.\n\n"
        + FABRICATION_RULE
        + "\n\n공고에 명시되지 않은 조건은 추측하지 말고 gaps에 '공고에 미기재'로 적으세요."
    )
    user = (
        f"# 지원자 프로필\n{profile.to_prompt_text()}\n\n"
        f"# 채용 공고\n{_job_text(job)}\n\n"
        "이 공고에 지원하는 것이 적절한지 분석해 주세요."
    )

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=ANALYSIS_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=FitAnalysis,
        )
    except Exception as exc:
        log.error("적합도 분석 실패: %s", exc)
        raise

    return response.parsed_output


# ==========================================
# 자소서 초안
# ==========================================
DEFAULT_QUESTIONS = [
    "지원 동기",
    "성장 과정 및 성격의 장단점",
    "입사 후 포부",
    "직무 관련 경험",
]


def draft_cover_letter(
    profile: Profile,
    job: dict[str, Any],
    question: str,
    max_chars: int = 700,
    tone: str = "담백하고 구체적으로",
    on_text=None,
) -> str:
    """자소서 문항 하나의 초안을 만든다.

    ``on_text`` 를 주면 생성되는 대로 조각을 넘겨준다(스트리밍 표시용).
    """
    client = _client()
    if client is None:
        return ""

    system = (
        "당신은 한국 기업 자기소개서 작성을 돕는 조력자입니다. "
        "지원자가 실제로 겪은 일을 바탕으로, 그 회사와 직무에 맞게 초안을 씁니다.\n\n"
        + FABRICATION_RULE
        + "\n\n작성 지침:\n"
        "- 첫 문장에 결론이나 핵심 경험을 두고, 추상적인 각오로 시작하지 마세요.\n"
        "- '열정', '최선을 다하겠습니다' 같은 상투어 대신 구체적인 사실을 쓰세요.\n"
        "- 경험은 상황-행동-결과가 드러나게 쓰되, 수치는 프로필에 있는 것만 쓰세요.\n"
        "- 회사와 직무에 실제로 연결되는 부분을 짚으세요.\n"
        "- 완성된 글만 출력하고, 설명이나 머리말은 붙이지 마세요."
    )
    user = (
        f"# 지원자 프로필\n{profile.to_prompt_text()}\n\n"
        f"# 지원할 공고\n{_job_text(job)}\n\n"
        f"# 자소서 문항\n{question}\n\n"
        f"# 요구사항\n- 분량: 공백 포함 {max_chars}자 내외\n- 어조: {tone}\n\n"
        "위 문항에 대한 자기소개서 초안을 작성해 주세요."
    )

    chunks: list[str] = []
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=COVER_LETTER_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                chunks.append(text)
                if on_text:
                    on_text("".join(chunks))
            stream.get_final_message()
    except Exception as exc:
        log.error("자소서 생성 실패: %s", exc)
        raise

    return "".join(chunks).strip()


# ==========================================
# 면접 예상 질문
# ==========================================
class InterviewPrep(BaseModel):
    questions: list[str] = Field(description="예상 면접 질문")
    tough_questions: list[str] = Field(description="약점을 파고드는 까다로운 질문")
    talking_points: list[str] = Field(description="준비해 두면 좋을 답변 소재 (프로필 기반)")


def prepare_interview(
    profile: Profile, job: dict[str, Any], cover_letters: list[str] | None = None
) -> InterviewPrep | None:
    """공고와 내가 낸 자소서를 근거로 예상 질문을 만든다."""
    client = _client()
    if client is None:
        return None

    letters = "\n\n".join(cover_letters or [])
    system = (
        "당신은 한국 기업 채용 면접관의 관점을 잘 아는 면접 코치입니다.\n\n"
        + FABRICATION_RULE
        + "\n\n지원자가 실제로 받을 법한 질문을 만들되, 프로필의 약한 부분을 "
        "면접관이 어떻게 파고들지도 짚어주세요."
    )
    user = (
        f"# 지원자 프로필\n{profile.to_prompt_text()}\n\n"
        f"# 지원 공고\n{_job_text(job)}\n\n"
        + (f"# 제출한 자소서\n{letters}\n\n" if letters else "")
        + "이 지원자가 받을 면접 질문을 예상해 주세요."
    )

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=ANALYSIS_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=InterviewPrep,
        )
    except Exception as exc:
        log.error("면접 질문 생성 실패: %s", exc)
        raise

    return response.parsed_output
