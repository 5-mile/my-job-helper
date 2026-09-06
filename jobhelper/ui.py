"""카드 렌더링과 스타일. 모든 외부 텍스트는 HTML 이스케이프 후 삽입한다."""

from __future__ import annotations

from html import escape
from typing import Any

from .config import STATUS_COLORS

CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');

    html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Inter', 'Noto Sans KR', sans-serif !important;
    }

    .job-card-box {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 18px;
        background: rgba(255, 255, 255, 0.02);
        margin-bottom: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        transition: border-color .2s ease, background .2s ease;
    }
    .job-card-box:hover { border-color: #3B82F6; background: rgba(255, 255, 255, 0.04); }
    .job-card-box.is-urgent { border-left: 3px solid #F87171; }
    .job-card-box.is-saved { border-left: 3px solid #34D399; }

    .platform-badge {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 6px;
        font-size: 0.72rem;
        font-weight: 700;
        margin-right: 5px;
        margin-bottom: 8px;
    }

    .bg-large-corp { background-color: rgba(59, 130, 246, 0.15); color: #60A5FA; border: 1px solid rgba(59, 130, 246, 0.25); }
    .bg-mid-corp   { background-color: rgba(147, 51, 234, 0.15); color: #C084FC; border: 1px solid rgba(147, 51, 234, 0.25); }
    .bg-foreign    { background-color: rgba(20, 184, 166, 0.15); color: #2DD4BF; border: 1px solid rgba(20, 184, 166, 0.25); }
    .bg-saramin    { background-color: rgba(148, 163, 184, 0.15); color: #94A3B8; }
    .bg-blog       { background-color: rgba(16, 185, 129, 0.12); color: #34D399; }
    .bg-date       { background-color: rgba(255, 255, 255, 0.05); color: #E2E8F0; border: 1px solid rgba(255, 255, 255, 0.08); }
    .bg-loc        { background-color: rgba(255, 255, 255, 0.05); color: #CBD5E1; }
    .bg-welfare    { background-color: rgba(245, 158, 11, 0.12); color: #FBBF24; }
    .bg-jobplanet  { background-color: rgba(255, 255, 255, 0.04); color: #94A3B8; border: 1px solid rgba(255, 255, 255, 0.06); }
    .bg-urgent     { background-color: rgba(248, 113, 113, 0.18); color: #FCA5A5; border: 1px solid rgba(248, 113, 113, 0.3); }
    .bg-new        { background-color: rgba(52, 211, 153, 0.18); color: #6EE7B7; border: 1px solid rgba(52, 211, 153, 0.3); }
    .bg-nps        { background-color: rgba(56, 189, 248, 0.14); color: #7DD3FC; border: 1px solid rgba(56, 189, 248, 0.28); }

    .company-title {
        font-size: 1.35rem;
        font-weight: 800;
        color: #F8FAFC;
        margin: 4px 0 6px;
        letter-spacing: -0.02em;
    }
    .job-title {
        font-size: 1.0rem;
        font-weight: 500;
        color: #94A3B8;
        line-height: 1.45;
        margin-bottom: 2px;
    }
    .job-meta { font-size: 0.78rem; color: #64748B; margin-top: 8px; }
    .job-memo { font-size: 0.8rem; color: #CBD5E1; margin-top: 8px; padding: 8px 10px;
                border-left: 2px solid rgba(255,255,255,0.12); background: rgba(255,255,255,0.02); }

    @media (prefers-color-scheme: light) {
        .job-card-box { background: #FFFFFF; border: 1px solid #E2E8F0; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }
        .company-title { color: #0F172A; }
        .job-title { color: #475569; }
        .job-meta { color: #94A3B8; }
        .job-memo { color: #334155; background: #F8FAFC; }
        .bg-date, .bg-loc { background-color: #F1F5F9; color: #475569; border: 1px solid #E2E8F0; }
        .bg-large-corp { background-color: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE; }
        .bg-mid-corp   { background-color: #FDF4FF; color: #7E22CE; border: 1px solid #F5D0FE; }
        .bg-foreign    { background-color: #F0FDFA; color: #0F766E; border: 1px solid #99F6E4; }
        .bg-nps        { background-color: #F0F9FF; color: #0369A1; border: 1px solid #BAE6FD; }
    }

    div[data-testid="stHorizontalBlock"] button {
        width: 100%;
        border-radius: 8px;
        font-size: 0.85rem;
        font-weight: 600;
    }

    /* 카드 안 상세보기 링크 */
    .card-link {
        display: inline-block;
        margin-top: 10px;
        font-size: 0.82rem;
        font-weight: 600;
        color: #60A5FA;
        text-decoration: none;
    }
    .card-link:hover { text-decoration: underline; }

    /* 지원 현황 요약 스트립 (좁은 화면에서는 가로 스크롤) */
    .status-strip {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding: 2px 0 8px;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: thin;
    }
    .status-cell {
        flex: 1 0 auto;
        min-width: 78px;
        text-align: center;
        padding: 10px 8px;
        border-radius: 10px;
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.07);
    }
    .status-cell.is-zero { opacity: 0.45; }
    .status-num { font-size: 1.5rem; font-weight: 800; line-height: 1.1; }
    .status-label { font-size: 0.72rem; color: #94A3B8; margin-top: 2px; white-space: nowrap; }

    /* ============ 모바일 ============ */
    @media (max-width: 640px) {
        /* 좌우 여백을 줄여 카드 폭을 최대한 확보 */
        .block-container { padding-left: 0.7rem !important; padding-right: 0.7rem !important;
                           padding-top: 2.5rem !important; }

        .job-card-box { padding: 14px !important; border-radius: 10px !important; }
        .company-title { font-size: 1.15rem !important; }
        .job-title { font-size: 0.92rem !important; }
        .platform-badge { font-size: 0.68rem !important; padding: 3px 6px !important;
                          margin-right: 4px !important; margin-bottom: 6px !important; }
        .job-meta { font-size: 0.72rem !important; }

        .status-cell { min-width: 68px; padding: 8px 6px; }
        .status-num { font-size: 1.25rem; }
        .status-label { font-size: 0.67rem; }

        /* 버튼은 손가락으로 누를 수 있게 키운다 */
        div[data-testid="stHorizontalBlock"] button,
        div[data-testid="stVerticalBlock"] button { min-height: 42px !important; }

        /* 표·코드가 화면을 밀어내지 않게 */
        div[data-testid="stTable"], .stDataFrame, pre { overflow-x: auto !important; }
    }

    /* 어떤 화면에서도 가로 스크롤이 생기지 않게 */
    .job-card-box, .company-title, .job-title, .job-meta, .job-memo {
        overflow-wrap: anywhere;
        word-break: break-word;
    }

    @media (prefers-color-scheme: light) {
        .card-link { color: #1D4ED8; }
        .status-cell { background: #F8FAFC; border: 1px solid #E2E8F0; }
        .status-label { color: #64748B; }
    }
</style>
"""


def badge(text: str, style: str = "bg-date") -> str:
    return f'<span class="platform-badge {style}">{escape(str(text))}</span>'


def status_pill(status: str) -> str:
    color = STATUS_COLORS.get(status, "#94A3B8")
    return (
        f'<span class="platform-badge" style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;">{escape(status)}</span>'
    )


def status_strip(counts: dict[str, int]) -> str:
    """지원 현황 요약. st.metric 7개는 모바일에서 세로로 쌓여 화면을 다 먹는다.

    가로 스크롤되는 한 줄로 만들어 어느 화면에서도 한눈에 보이게 한다.
    """
    cells = []
    for status, count in counts.items():
        color = STATUS_COLORS.get(status, "#94A3B8")
        dim = "" if count else " is-zero"
        cells.append(
            f'<div class="status-cell{dim}">'
            f'<div class="status-num" style="color:{color};">{count}</div>'
            f'<div class="status-label">{escape(status)}</div>'
            f"</div>"
        )
    return f'<div class="status-strip">{"".join(cells)}</div>'


def company_badges(job: dict[str, Any]) -> list[str]:
    """국민연금 실데이터 기반 회사 규모·보수 뱃지.

    인증키가 없거나 조회되지 않은 회사는 아무 뱃지도 붙이지 않는다.
    (예전의 해시 기반 가짜 별점을 대신한다.)
    """
    info = job.get("company_info")
    if not info or not info.get("found"):
        return []

    badges = []
    employees = info.get("employees")
    if employees:
        badges.append(badge(f"👥 {employees:,}명", "bg-nps"))

    pay = info.get("avg_monthly_pay")
    if pay:
        man = round(pay / 10_000)
        suffix = "+" if pay >= 6_370_000 * 0.95 else ""
        badges.append(badge(f"💰 월평균 {man:,}만{suffix}", "bg-nps"))

    joined, left = info.get("joined_this_month"), info.get("left_this_month")
    if joined is not None and left is not None and (joined or left):
        badges.append(badge(f"🔄 입{joined}/퇴{left}", "bg-loc"))

    return badges


def job_card(
    job: dict[str, Any],
    *,
    is_new: bool = False,
    is_saved: bool = False,
    urgent_days: int | None = None,
) -> str:
    """검색 결과 카드 HTML."""
    classes = ["job-card-box"]
    if urgent_days is not None and 0 <= urgent_days <= 3:
        classes.append("is-urgent")
    elif is_saved:
        classes.append("is-saved")

    badges = []
    if is_new:
        badges.append(badge("🆕 NEW", "bg-new"))
    dday = job.get("date", "")
    is_hot = "D-DAY" in dday or dday.startswith("⏳ D-1") or dday.startswith("⏳ D-2")
    badges.append(badge(dday, "bg-urgent" if is_hot else "bg-date"))
    badges.append(badge(job.get("source", "사람인"), "bg-saramin"))
    if job.get("location"):
        badges.append(badge(f"📍 {job['location']}", "bg-loc"))
    if job.get("career"):
        badges.append(badge(f"👔 {job['career']}", "bg-loc"))
    if job.get("employment"):
        badges.append(badge(job["employment"], "bg-loc"))
    if job.get("salary"):
        badges.append(badge(f"💵 {job['salary']}", "bg-welfare"))
    badges.extend(company_badges(job))
    for welfare in (job.get("welfares") or [])[:3]:
        badges.append(badge(welfare, "bg-welfare"))
    if is_saved:
        badges.append(badge("보관함에 있음", "bg-blog"))

    meta = ""
    if job.get("sector"):
        meta = f'<div class="job-meta">{escape(job["sector"])}</div>'

    # 상세보기는 링크면 충분하다. 카드 안에 넣으면 모바일에서 버튼 줄이 하나 준다.
    link = ""
    if job.get("link"):
        link = (
            f'<a class="card-link" href="{escape(job["link"])}" '
            f'target="_blank" rel="noopener noreferrer">공고 상세보기 →</a>'
        )

    joined = " ".join(classes)
    return (
        f'<div class="{joined}">'
        f'<div>{"".join(badges)}</div>'
        f'<div class="company-title">{escape(job.get("company", ""))}</div>'
        f'<div class="job-title">{escape(job.get("position", ""))}</div>'
        f"{meta}{link}</div>"
    )


def blog_card(item: dict[str, Any], *, is_new: bool = False, is_saved: bool = False) -> str:
    badges = []
    if is_new:
        badges.append(badge("🆕 NEW", "bg-new"))
    badges.append(badge(item.get("corp_badge", ""), item.get("badge_style", "bg-mid-corp")))
    badges.append(badge(item.get("date", ""), "bg-date"))
    badges.append(badge(item.get("dday", ""), "bg-loc"))
    badges.extend(company_badges(item))
    if is_saved:
        badges.append(badge("보관함에 있음", "bg-blog"))

    css_class = "job-card-box is-saved" if is_saved else "job-card-box"
    link = ""
    if item.get("link"):
        link = (
            f'<a class="card-link" href="{escape(item["link"])}" '
            f'target="_blank" rel="noopener noreferrer">원문 보기 →</a>'
        )
    return (
        f'<div class="{css_class}">'
        f'<div>{"".join(badges)}</div>'
        f'<div class="company-title">{escape(item.get("company", ""))}</div>'
        f'<div class="job-title">{escape(item.get("position", ""))}</div>'
        f"{link}</div>"
    )


def saved_card(job: dict[str, Any]) -> str:
    badges = [
        status_pill(job.get("status", "관심")),
        badge(job.get("source", ""), "bg-saramin"),
    ]
    if job.get("date"):
        badges.append(badge(job["date"], "bg-date"))
    if job.get("location"):
        badges.append(badge(f"📍 {job['location']}", "bg-loc"))
    if job.get("applied_at"):
        badges.append(badge(f"🗓 지원 {job['applied_at']}", "bg-loc"))

    memo = ""
    if job.get("memo"):
        memo = f'<div class="job-memo">📝 {escape(job["memo"])}</div>'

    return (
        f'<div class="job-card-box">'
        f'<div>{"".join(badges)}</div>'
        f'<div class="company-title">{escape(job.get("company", ""))}</div>'
        f'<div class="job-title">{escape(job.get("position", ""))}</div>'
        f"{memo}</div>"
    )
