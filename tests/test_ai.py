"""프로필 저장과 AI 기능 테스트 (실제 API 호출 없음).

API를 실제로 부르면 돈이 들고 결과가 매번 달라지므로, 클라이언트를 가짜로
바꿔서 **프롬프트에 무엇이 들어가는지**와 **키가 없을 때 안전하게 꺼지는지**를
검증한다. 특히 "프로필에 없는 사실을 지어내지 말라"는 제약이 실제로 프롬프트에
들어가는지 확인한다.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobhelper import ai, settings  # noqa: E402
from jobhelper.profile import (  # noqa: E402
    Episode,
    Profile,
    delete_cover_letter,
    init_profile_tables,
    load_cover_letters,
    load_profile,
    save_cover_letter,
    save_profile,
)

SAMPLE_JOB = {
    "id": 1,
    "company": "테스트산업",
    "position": "생산직 사원 모집",
    "sector": "자동차부품 조립",
    "location": "경기 평택시",
    "career": "신입",
    "education": "고졸",
    "employment": "정규직",
    "salary": "연봉 3000만원",
    "date": "⏳ D-10",
    "welfares": ["🍔 식사제공", "🚌 기숙사/교통"],
    "company_info": {"found": True, "employees": 1234, "avg_monthly_pay": 3_800_000},
}


def _profile() -> Profile:
    return Profile(
        name="홍길동",
        career="OO산업 생산직 2년",
        education="OO공고 기계과",
        certificates="지게차운전기능사",
        skills="설비 점검",
        desired_role="생산관리",
        strengths="기록 관리",
        episodes=[
            Episode(
                title="설비 고장 대응",
                situation="조립 라인 정지가 잦았음",
                action="점검 체크리스트를 만들어 매일 확인",
                result="정지 시간 30분 → 10분",
            )
        ],
    )


# ==========================================
# 프로필 모델
# ==========================================
def test_profile_prompt_text_contains_facts():
    text = _profile().to_prompt_text()
    assert "OO산업 생산직 2년" in text
    assert "지게차운전기능사" in text
    assert "정지 시간 30분 → 10분" in text


def test_profile_prompt_text_skips_empty_fields():
    text = Profile(career="경력만 있음").to_prompt_text()
    assert "경력" in text
    assert "자격증" not in text


def test_empty_episode_is_excluded():
    prof = Profile(career="x", episodes=[Episode(), Episode(title="진짜")])
    assert len(prof.filled_episodes()) == 1


def test_profile_usability_gate():
    assert Profile().is_usable() is False
    assert "경력 사항" in Profile().missing_parts()

    assert Profile(career="2년").is_usable() is True
    assert Profile(episodes=[Episode(title="x")]).is_usable() is True


# ==========================================
# 프로필 저장
# ==========================================
def test_profile_roundtrip(tmp_path):
    path = str(tmp_path / "p.db")
    init_profile_tables(path)

    save_profile(_profile(), path)
    loaded = load_profile(path)

    assert loaded.name == "홍길동"
    assert loaded.desired_role == "생산관리"
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].result == "정지 시간 30분 → 10분"


def test_profile_overwrites_not_duplicates(tmp_path):
    path = str(tmp_path / "p.db")
    init_profile_tables(path)

    save_profile(Profile(name="첫번째"), path)
    save_profile(Profile(name="두번째"), path)

    assert load_profile(path).name == "두번째"


def test_load_profile_when_empty(tmp_path):
    path = str(tmp_path / "p.db")
    init_profile_tables(path)
    assert load_profile(path).name == ""
    assert load_profile(path).is_usable() is False


# ==========================================
# 자소서 저장
# ==========================================
def test_cover_letter_roundtrip(tmp_path):
    path = str(tmp_path / "c.db")
    init_profile_tables(path)

    save_cover_letter(1, "테스트산업", "지원 동기", "첫 번째 답변", path)
    letters = load_cover_letters("테스트산업", path)
    assert len(letters) == 1
    assert letters[0]["answer"] == "첫 번째 답변"

    # 같은 (회사, 문항)이면 덮어쓴다
    save_cover_letter(1, "테스트산업", "지원 동기", "고친 답변", path)
    letters = load_cover_letters("테스트산업", path)
    assert len(letters) == 1
    assert letters[0]["answer"] == "고친 답변"

    # 문항이 다르면 별개
    save_cover_letter(1, "테스트산업", "입사 후 포부", "다른 답변", path)
    assert len(load_cover_letters("테스트산업", path)) == 2

    delete_cover_letter(letters[0]["id"], path)
    assert len(load_cover_letters("테스트산업", path)) == 1


def test_cover_letters_filtered_by_company(tmp_path):
    path = str(tmp_path / "c.db")
    init_profile_tables(path)
    save_cover_letter(1, "A사", "지원 동기", "a", path)
    save_cover_letter(2, "B사", "지원 동기", "b", path)

    assert len(load_cover_letters("A사", path)) == 1
    assert len(load_cover_letters(None, path)) == 2


# ==========================================
# 키가 없을 때 안전하게 꺼지는지
# ==========================================
def test_ai_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", lambda: None)
    assert ai.is_available() is False
    assert ai.analyze_fit(_profile(), SAMPLE_JOB) is None
    assert ai.draft_cover_letter(_profile(), SAMPLE_JOB, "지원 동기") == ""
    assert ai.prepare_interview(_profile(), SAMPLE_JOB) is None


def test_ai_available_with_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", lambda: "sk-ant-test")
    assert ai.is_available() is True


# ==========================================
# 공고 → 프롬프트 변환
# ==========================================
def test_job_text_includes_key_fields():
    text = ai._job_text(SAMPLE_JOB)
    assert "테스트산업" in text
    assert "경기 평택시" in text
    assert "연봉 3000만원" in text
    assert "1,234명" in text


def test_job_text_skips_missing_fields():
    text = ai._job_text({"company": "회사만"})
    assert "회사만" in text
    assert "근무지" not in text


# ==========================================
# 프롬프트 내용 검증 (가짜 클라이언트)
# ==========================================
class _FakeMessages:
    def __init__(self, recorder):
        self.recorder = recorder

    def parse(self, **kwargs):
        self.recorder.update(kwargs)

        class _R:
            parsed_output = ai.FitAnalysis(
                score=70, verdict="지원 추천", summary="맞습니다",
                matches=["설비 경험"], gaps=["교대 근무 경험 미기재"], actions=["자격증 사본 준비"],
            )

        return _R()

    def stream(self, **kwargs):
        self.recorder.update(kwargs)

        class _Stream:
            text_stream = ["초안 ", "본문입니다."]

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def get_final_message(self_inner):
                return None

        return _Stream()


class _FakeClient:
    def __init__(self, recorder):
        self.messages = _FakeMessages(recorder)


@pytest.fixture
def recorded(monkeypatch):
    recorder: dict = {}
    monkeypatch.setattr(settings, "anthropic_api_key", lambda: "sk-ant-test")
    monkeypatch.setattr(ai, "_client", lambda: _FakeClient(recorder))
    return recorder


def test_fit_prompt_forbids_fabrication(recorded):
    ai.analyze_fit(_profile(), SAMPLE_JOB)
    assert "지어내지" in recorded["system"]
    assert recorded["model"] == "claude-opus-5"


def test_fit_prompt_includes_profile_and_job(recorded):
    ai.analyze_fit(_profile(), SAMPLE_JOB)
    user = recorded["messages"][0]["content"]
    assert "OO산업 생산직 2년" in user
    assert "테스트산업" in user


def test_fit_returns_parsed_analysis(recorded):
    result = ai.analyze_fit(_profile(), SAMPLE_JOB)
    assert result.score == 70
    assert result.verdict == "지원 추천"
    assert "설비 경험" in result.matches


def test_cover_letter_prompt_forbids_fabrication(recorded):
    ai.draft_cover_letter(_profile(), SAMPLE_JOB, "지원 동기", max_chars=500)
    assert "지어내지" in recorded["system"]
    user = recorded["messages"][0]["content"]
    assert "지원 동기" in user
    assert "500자" in user


def test_cover_letter_returns_streamed_text(recorded):
    text = ai.draft_cover_letter(_profile(), SAMPLE_JOB, "지원 동기")
    assert text == "초안 본문입니다."


def test_cover_letter_streams_progress(recorded):
    seen = []
    ai.draft_cover_letter(_profile(), SAMPLE_JOB, "지원 동기", on_text=seen.append)
    assert seen == ["초안 ", "초안 본문입니다."]


def test_interview_prompt_includes_cover_letters(recorded):
    ai.prepare_interview(_profile(), SAMPLE_JOB, ["제가 쓴 자소서 본문"])
    user = recorded["messages"][0]["content"]
    assert "제가 쓴 자소서 본문" in user
