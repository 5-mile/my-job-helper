"""내 프로필과 자소서 보관.

자소서 초안과 적합도 분석은 모두 여기 저장된 '내 실제 경험'을 재료로 쓴다.
프로필이 비어 있으면 AI가 지어낼 수밖에 없으므로, 두 기능 모두 프로필을
먼저 요구한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .dates import now_iso
from .storage import autoincrement_pk, connect, upsert

PROFILE_ID = 1  # 1인용 앱이라 프로필은 한 벌만 둔다


@dataclass
class Episode:
    """경험 하나. 자소서에서 근거로 쓰이는 최소 단위."""

    title: str = ""
    situation: str = ""
    action: str = ""
    result: str = ""

    def is_empty(self) -> bool:
        return not any([self.title, self.situation, self.action, self.result])

    def to_text(self) -> str:
        parts = []
        if self.title:
            parts.append(f"[{self.title}]")
        if self.situation:
            parts.append(f"상황: {self.situation}")
        if self.action:
            parts.append(f"행동: {self.action}")
        if self.result:
            parts.append(f"결과: {self.result}")
        return "\n".join(parts)


@dataclass
class Profile:
    name: str = ""
    career: str = ""
    education: str = ""
    certificates: str = ""
    skills: str = ""
    desired_role: str = ""
    strengths: str = ""
    episodes: list[Episode] = field(default_factory=list)

    def filled_episodes(self) -> list[Episode]:
        return [e for e in self.episodes if not e.is_empty()]

    def is_usable(self) -> bool:
        """AI 기능을 쓸 만큼 채워졌는지."""
        return bool(self.career.strip() or self.filled_episodes())

    def missing_parts(self) -> list[str]:
        missing = []
        if not self.career.strip():
            missing.append("경력 사항")
        if not self.filled_episodes():
            missing.append("경험 에피소드 (최소 1개)")
        return missing

    def to_prompt_text(self) -> str:
        """모델에 넘길 프로필 요약. 여기 없는 사실은 쓰지 못하게 한다."""
        blocks = []
        pairs = [
            ("이름", self.name),
            ("희망 직무", self.desired_role),
            ("경력", self.career),
            ("학력", self.education),
            ("자격증", self.certificates),
            ("보유 기술", self.skills),
            ("본인이 생각하는 강점", self.strengths),
        ]
        for label, value in pairs:
            if value and value.strip():
                blocks.append(f"## {label}\n{value.strip()}")

        episodes = self.filled_episodes()
        if episodes:
            body = "\n\n".join(e.to_text() for e in episodes)
            blocks.append(f"## 경험 에피소드\n{body}")
        return "\n\n".join(blocks)


# ==========================================
# 저장소
# ==========================================
def init_profile_tables(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY,
                data TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS cover_letters (
                id {autoincrement_pk()},
                job_id INTEGER,
                company TEXT,
                question TEXT,
                answer TEXT,
                updated_at TEXT
            )
            """
        )


def _to_dict(profile: Profile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "career": profile.career,
        "education": profile.education,
        "certificates": profile.certificates,
        "skills": profile.skills,
        "desired_role": profile.desired_role,
        "strengths": profile.strengths,
        "episodes": [
            {"title": e.title, "situation": e.situation, "action": e.action, "result": e.result}
            for e in profile.episodes
        ],
    }


def _from_dict(data: dict[str, Any]) -> Profile:
    episodes = [
        Episode(
            title=e.get("title", ""),
            situation=e.get("situation", ""),
            action=e.get("action", ""),
            result=e.get("result", ""),
        )
        for e in data.get("episodes", [])
    ]
    return Profile(
        name=data.get("name", ""),
        career=data.get("career", ""),
        education=data.get("education", ""),
        certificates=data.get("certificates", ""),
        skills=data.get("skills", ""),
        desired_role=data.get("desired_role", ""),
        strengths=data.get("strengths", ""),
        episodes=episodes,
    )


def save_profile(profile: Profile, db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            upsert("user_profile", ["id", "data", "updated_at"], ["id"]),
            (PROFILE_ID, json.dumps(_to_dict(profile), ensure_ascii=False), now_iso()),
        )


def load_profile(db_path: str | None = None) -> Profile:
    """저장된 프로필. 없으면 빈 프로필."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT data FROM user_profile WHERE id = ?", (PROFILE_ID,)
        ).fetchone()
    if not row or not row["data"]:
        return Profile()
    try:
        return _from_dict(json.loads(row["data"]))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return Profile()


# ==========================================
# 자소서
# ==========================================
def save_cover_letter(
    job_id: int | None,
    company: str,
    question: str,
    answer: str,
    db_path: str | None = None,
) -> None:
    """같은 (회사, 문항)이면 덮어쓴다."""
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM cover_letters WHERE company = ? AND question = ?",
            (company, question),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cover_letters SET answer = ?, updated_at = ?, job_id = ? WHERE id = ?",
                (answer, now_iso(), job_id, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO cover_letters (job_id, company, question, answer, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, company, question, answer, now_iso()),
            )


def load_cover_letters(
    company: str | None = None, db_path: str | None = None
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if company:
            rows = conn.execute(
                "SELECT * FROM cover_letters WHERE company = ? ORDER BY id DESC", (company,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM cover_letters ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def delete_cover_letter(letter_id: int, db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM cover_letters WHERE id = ?", (letter_id,))
