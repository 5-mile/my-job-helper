"""통합 채용 정보 대시보드 - Streamlit 진입점."""

from __future__ import annotations

import csv
import io
from datetime import date

import streamlit as st

from jobhelper import (
    ai, company_info, db, freshness, insights, notify, profile as profile_mod,
    settings, storage, ui,
)
from jobhelper.config import (
    APPLICATION_STATUSES,
    CATEGORIES,
    DEFAULT_BLOG_IDS,
    SARAMIN_ALL_SORTS,
    SARAMIN_SORT_OPTIONS,
)
from jobhelper.dates import days_left, parse_deadline
from jobhelper.scrapers.naver_blog import fetch_blog_feed
from jobhelper.scrapers.saramin import fetch_saramin_jobs_detailed, group_by_category
from jobhelper.scrapers.worknet import fetch_worknet_jobs
from jobhelper.scrapers.saramin_api import fetch_saramin_api_jobs
from jobhelper.scrapers.publicjobs import fetch_public_jobs

st.set_page_config(
    page_title="통합 채용 정보 및 지원 현황 관리",
    page_icon="💼",
    layout="wide",
)
# Streamlit Cloud가 새 코드를 받고도 옛 모듈을 재사용하면 여기서 되살린다.
# (그냥 두면 ImportError / AttributeError 트레이스백만 나오고 원인을 알 수 없다.)
_stale_help = freshness.ensure_fresh()
if _stale_help:
    st.error("⚠️ 서버가 옛 코드를 쓰고 있습니다")
    st.info(_stale_help)
    st.stop()

st.markdown(ui.CSS, unsafe_allow_html=True)

# 한 번에 그리는 카드 수. 모바일에서 수백 장을 한꺼번에 그리면 눈에 띄게 느려진다.
PAGE_SIZE = 20

# 저장소 연결은 앱 시작에 꼭 필요하므로, 실패하면 원인을 화면에 설명하고 멈춘다.
# (그냥 두면 psycopg 트레이스백만 나와서 무엇이 잘못됐는지 알기 어렵다.)
_db_valid, _db_why = storage.validate_url() if storage.is_postgres() else (True, "")
if not _db_valid:
    st.error(f"❌ DATABASE_URL 설정 오류 — {_db_why}")
    st.info(
        "Streamlit Cloud라면 **Manage app → Settings → Secrets** 의 DATABASE_URL을 "
        "고친 뒤 **Reboot app** 하세요. 로컬이라면 `.env`를 고치고 다시 실행하세요."
    )
    st.stop()

_direct_warning = storage.warn_direct_connection() if storage.is_postgres() else ""

try:
    db.init_db()
    company_info.init_cache()
    notify.init_alert_log()
    profile_mod.init_profile_tables()
    insights.init_insight_tables()
except Exception as exc:
    st.error(f"❌ 데이터베이스에 연결하지 못했습니다 — {type(exc).__name__}")
    if _direct_warning:
        st.warning(f"⚠️ {_direct_warning}")
    with st.expander("자세한 오류"):
        st.code(str(exc))
    st.info(
        "확인할 것\n\n"
        "- Secrets의 DATABASE_URL 호스트가 `...pooler.supabase.com` 인지 "
        "(`db.xxx.supabase.co`는 IPv6 전용이라 여기서 연결되지 않습니다)\n"
        "- 비밀번호에서 `[ ]` 대괄호를 지웠는지\n"
        "- Supabase 프로젝트가 일시 정지 상태는 아닌지"
    )
    st.stop()


# ==========================================
# 데이터 수집 (캐시)
# ==========================================
@st.cache_data(ttl=600, show_spinner="사람인 공고를 수집하는 중입니다...")
def load_saramin(
    keywords: tuple[str, ...], sort_codes: tuple[str, ...], pages: int, exclude: tuple[str, ...]
):
    jobs, diagnostics = fetch_saramin_jobs_detailed(
        list(keywords), list(sort_codes), pages, list(exclude)
    )
    return jobs, diagnostics.warning


@st.cache_data(ttl=600, show_spinner="워크넷 공고를 수집하는 중입니다...")
def load_worknet(keywords: tuple[str, ...], pages: int, exclude: tuple[str, ...]):
    return fetch_worknet_jobs(list(keywords), pages=pages, exclude=list(exclude))


@st.cache_data(ttl=600, show_spinner="사람인 공식 API로 수집하는 중입니다...")
def load_saramin_api(keywords: tuple[str, ...], pages: int, exclude: tuple[str, ...]):
    return fetch_saramin_api_jobs(list(keywords), pages=pages, exclude=list(exclude))


@st.cache_data(ttl=600, show_spinner="공공기관 채용정보를 수집하는 중입니다...")
def load_public_jobs(keywords: tuple[str, ...], pages: int, exclude: tuple[str, ...]):
    return fetch_public_jobs(list(keywords), pages=pages, exclude=list(exclude))


@st.cache_data(ttl=600, show_spinner="블로그 피드를 정제하는 중입니다...")
def load_blog(blog_ids: tuple[str, ...], limit: int):
    return fetch_blog_feed(list(blog_ids), limit)


@st.cache_data(ttl=86400, show_spinner="회사 규모·보수 정보를 조회하는 중입니다...")
def enrich(jobs: list, limit: int):
    return company_info.enrich_jobs(jobs, limit)


# ==========================================
# 사이드바 - 검색 조건
# ==========================================
st.sidebar.markdown("### 🔎 검색 조건")
keyword_input = st.sidebar.text_input(
    "검색어 (쉼표로 여러 개)", value="생산", help="예: 생산, 품질, 설비"
)
exclude_input = st.sidebar.text_input(
    "제외 키워드 (쉼표로 여러 개)", value="", help="공고명·회사명·직무에 이 단어가 들어가면 숨깁니다"
)
sort_display = st.sidebar.selectbox("정렬 조건", list(SARAMIN_SORT_OPTIONS))
pages = st.sidebar.slider("검색어당 수집 페이지 수", 1, 15, 3)

wide_net = st.sidebar.checkbox(
    "정렬 3종 합쳐서 더 많이 수집",
    value=False,
    help="사람인은 정렬마다 다른 공고를 보여줍니다. 3종을 합치면 약 3배가 모이지만 "
    "수집 시간도 3배가 됩니다.",
)
sort_codes = tuple(SARAMIN_ALL_SORTS) if wide_net else (SARAMIN_SORT_OPTIONS[sort_display],)
st.sidebar.caption(f"사람인 요청 {len(sort_codes) * pages}건 × 검색어 수")

st.sidebar.markdown("**공고 소스**")
has_worknet = bool(settings.worknet_auth_key())
has_saramin_api = bool(settings.saramin_api_key())
has_public = bool(settings.public_jobs_key())

use_scrape = st.sidebar.checkbox("사람인 (웹 수집)", value=True)
use_saramin_api = st.sidebar.checkbox(
    "사람인 공식 API", value=has_saramin_api, disabled=not has_saramin_api,
    help="SARAMIN_API_KEY가 필요합니다" if not has_saramin_api else None,
)
use_worknet = st.sidebar.checkbox(
    "워크넷", value=has_worknet, disabled=not has_worknet,
    help="WORKNET_AUTH_KEY가 필요합니다" if not has_worknet else None,
)
use_public = st.sidebar.checkbox(
    "공공기관 (잡알리오)", value=has_public, disabled=not has_public,
    help="PUBLIC_JOBS_KEY가 필요합니다" if not has_public else None,
)

st.sidebar.markdown("### 🎚️ 필터")
user_location = st.sidebar.text_input("희망 근무 지역 (우선 배치)", value="")
only_with_deadline = st.sidebar.checkbox("마감일이 있는 공고만 보기", value=False)
urgent_only = st.sidebar.checkbox("마감 7일 이내만 보기", value=False)
hide_saved = st.sidebar.checkbox("이미 보관한 공고 숨기기", value=False)
hide_bulk = st.sidebar.checkbox(
    "다수 공고 회사 숨기기",
    value=False,
    help="한 회사가 검색 결과에 여러 건 올린 경우입니다. 채용대행·파견업체인 "
    "경우가 많아, 직접고용을 찾는다면 걸러낼 수 있습니다.",
)
agency_threshold = st.sidebar.number_input(
    "다수 공고 기준 (건)", min_value=3, max_value=50,
    value=insights.AGENCY_POSTING_THRESHOLD, step=1,
    help="한 회사가 이 건수 이상 올리면 표시합니다.",
)
hide_stale = st.sidebar.checkbox(
    f"{insights.LONG_LISTING_DAYS}일 이상 게시된 공고 숨기기",
    value=False,
    help="오래 걸려 있는 공고는 사람이 잘 안 붙는 자리일 수 있습니다. "
    "이력이 쌓인 뒤부터 판별됩니다.",
)

has_nps = bool(settings.nps_service_key())
min_employees = st.sidebar.number_input(
    "최소 직원 수 (국민연금 가입자)",
    min_value=0,
    max_value=100000,
    value=0,
    step=10,
    disabled=not has_nps,
    help="NPS_SERVICE_KEY를 .env에 넣으면 켜집니다" if not has_nps else company_info.CAVEAT,
)
if has_nps:
    st.sidebar.caption(company_info.CAVEAT)

st.sidebar.markdown("### 📰 블로그 피드")
blog_input = st.sidebar.text_input("네이버 블로그 ID (쉼표 구분)", value=", ".join(DEFAULT_BLOG_IDS))
blog_limit = st.sidebar.slider("피드 개수", 5, 40, 15)

if st.sidebar.button("🔄 캐시 비우고 새로 수집"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("### 💾 저장소")
if storage.is_postgres():
    st.sidebar.success(f"{storage.backend_name()} · 보관함이 영구 저장됩니다")
else:
    st.sidebar.info(
        "SQLite (로컬 파일) — Streamlit Cloud에 배포한 경우 재시작 때마다 "
        "보관함이 초기화됩니다. `DATABASE_URL`을 설정하면 영구 저장됩니다."
    )

missing = settings.missing_keys()
if missing:
    with st.sidebar.expander("⚙️ 설정되지 않은 기능", expanded=False):
        for item in missing:
            st.markdown(f"- {item}")
        st.caption("`.env.example`을 `.env`로 복사한 뒤 값을 채우고 앱을 다시 시작하세요.")

keywords = tuple(k.strip() for k in keyword_input.split(",") if k.strip())
excludes = tuple(e.strip() for e in exclude_input.split(",") if e.strip())
blog_ids = tuple(b.strip() for b in blog_input.split(",") if b.strip())


# ==========================================
# 상단 - 지원 현황 요약
# ==========================================
st.markdown(
    '<div style="font-size:1.6rem; font-weight:700; color:#3B82F6;">'
    "통합 채용 공고 &amp; 지원 현황 대시보드</div>",
    unsafe_allow_html=True,
)
st.caption("사람인·워크넷 검색과 전문 블로그 피드를 한 화면에서 보고, 스크랩한 공고의 지원 진행 상황까지 관리합니다.")

counts = db.status_counts()
st.markdown(
    ui.status_strip({s: counts.get(s, 0) for s in APPLICATION_STATUSES}),
    unsafe_allow_html=True,
)

# 마감 임박 알림 배너
today = date.today()
urgent_saved = notify.find_urgent_jobs(within_days=3, today=today)
if urgent_saved:
    names = ", ".join(f"{j['company']}(D-{j['days_left']})" for j in urgent_saved[:4])
    more = f" 외 {len(urgent_saved) - 4}건" if len(urgent_saved) > 4 else ""
    st.warning(f"⏰ 보관함에 마감 임박 공고가 있습니다 — {names}{more}")

st.divider()


# ==========================================
# 데이터 준비
# ==========================================
saved = db.saved_keys()

jobs: list = []
source_stats: list[tuple[str, int, str]] = []  # (소스명, 건수, 경고)

if keywords:
    if use_scrape:
        got, warn = load_saramin(keywords, sort_codes, pages, excludes)
        jobs.extend(got)
        source_stats.append(("사람인 (웹)", len(got), warn))
    if use_saramin_api and has_saramin_api:
        got, warn = load_saramin_api(keywords, pages, excludes)
        jobs.extend(got)
        source_stats.append(("사람인 API", len(got), warn))
    if use_worknet and has_worknet:
        got = load_worknet(keywords, pages, excludes)
        jobs.extend(got)
        source_stats.append(("워크넷", len(got), ""))
    if use_public and has_public:
        got, warn = load_public_jobs(keywords, pages, excludes)
        jobs.extend(got)
        source_stats.append(("공공기관", len(got), warn))

# 소스 간 중복 제거 (같은 공고가 여러 소스에 있을 수 있다)
_seen_keys: set[str] = set()
_deduped = []
for _job in jobs:
    if _job["job_key"] in _seen_keys:
        continue
    _seen_keys.add(_job["job_key"])
    _deduped.append(_job)
duplicates_removed = len(jobs) - len(_deduped)
jobs = _deduped

for _name, _count, _warn in source_stats:
    if _warn:
        st.error(f"⚠️ {_name}: {_warn}")

blog_feed = load_blog(blog_ids, blog_limit) if blog_ids else []

if has_nps and jobs:
    jobs = enrich(jobs, 40)

new_keys = db.mark_seen([j["job_key"] for j in jobs] + [b["job_key"] for b in blog_feed])

# 공고 이력을 쌓아 '며칠째 게시 중'을 알 수 있게 한다 (추가 키 불필요)
if jobs:
    try:
        insights.record_sightings(jobs, today)
        jobs = insights.annotate_history(jobs, today)
    except Exception as exc:  # 이력은 부가 기능이라 실패해도 목록은 보여준다
        st.caption(f"공고 이력 기록을 건너뛰었습니다 ({type(exc).__name__}).")
    jobs = insights.annotate_agencies(jobs, agency_threshold)


def passes_filters(job: dict) -> bool:
    if hide_saved and f"{job['company']}::{job['position']}" in saved:
        return False
    if hide_bulk and job.get("bulk_poster"):
        return False
    if hide_stale and job.get("long_listing"):
        return False
    if min_employees:
        employees = (job.get("company_info") or {}).get("employees")
        if employees is None or employees < min_employees:
            return False
    left = days_left(parse_deadline(job.get("deadline", "")), today)
    if only_with_deadline and left is None:
        return False
    if urgent_only and (left is None or left < 0 or left > 7):
        return False
    return True


def sort_key(job: dict):
    """희망 지역 우선 → 마감 임박 순."""
    left = days_left(parse_deadline(job.get("deadline", "")), today)
    location_hit = 0 if (user_location and user_location in job.get("location", "")) else 1
    return (location_hit, 9999 if left is None or left < 0 else left)


grouped = group_by_category(jobs)

col_left, col_right = st.columns([1.15, 1])

# ------------------------------------------
# 좌측 - 검색 결과
# ------------------------------------------
with col_left:
    total_shown = sum(len([j for j in v if passes_filters(j)]) for v in grouped.values())
    st.markdown(f"#### 실시간 검색 채용 리스트 · {total_shown}건")

    if source_stats:
        parts = [f"{name} {count:,}건" for name, count, _ in source_stats]
        line = " · ".join(parts) + f" → 중복 제거 후 {len(jobs):,}건"
        if duplicates_removed:
            line += f" (중복 {duplicates_removed:,}건 제외)"
        st.caption(line)
        empty = [name for name, count, warn in source_stats if count == 0 and not warn]
        if empty:
            st.caption(f"⚠️ 결과가 0건인 소스: {', '.join(empty)} — 검색어가 맞는지 확인하세요.")

    tabs = st.tabs(CATEGORIES)
    for category, tab in zip(CATEGORIES, tabs):
        with tab:
            if not keywords:
                st.info("💡 사이드바에 검색어를 입력하면 공고를 수집합니다.")
                continue

            visible = sorted([j for j in grouped.get(category, []) if passes_filters(j)], key=sort_key)
            if not visible:
                st.info("조건에 부합하는 채용 공고가 없습니다. 필터를 완화해 보세요.")
                continue

            # 카드를 수백 장 한꺼번에 그리면 특히 모바일에서 느려진다.
            shown_key = f"shown_{category}"
            shown = st.session_state.get(shown_key, PAGE_SIZE)
            page = visible[:shown]

            for idx, job in enumerate(page):
                job_saved = f"{job['company']}::{job['position']}" in saved
                left = days_left(parse_deadline(job.get("deadline", "")), today)
                st.markdown(
                    ui.job_card(
                        job,
                        is_new=job["job_key"] in new_keys,
                        is_saved=job_saved,
                        urgent_days=left,
                    ),
                    unsafe_allow_html=True,
                )

                if st.button(
                    "✅ 보관됨" if job_saved else "⭐ 공고 스크랩",
                    key=f"save_{category}_{idx}_{job['job_key']}",
                    disabled=job_saved,
                    use_container_width=True,
                ):
                    db.save_job(job)
                    st.toast(f"{job['company']} 공고를 보관함에 저장했습니다.")
                    st.rerun()
                st.markdown('<div style="margin-bottom:14px;"></div>', unsafe_allow_html=True)

            if len(visible) > shown:
                remaining = len(visible) - shown
                if st.button(
                    f"⬇️ {min(PAGE_SIZE, remaining)}건 더 보기 (남은 {remaining:,}건)",
                    key=f"more_{category}",
                    use_container_width=True,
                ):
                    st.session_state[shown_key] = shown + PAGE_SIZE
                    st.rerun()
            elif len(visible) > PAGE_SIZE:
                st.caption(f"{len(visible):,}건 전부 표시했습니다.")

# ------------------------------------------
# 우측 - 블로그 피드
# ------------------------------------------
with col_right:
    st.markdown(f"#### 전문 블로그 추천 피드 · {len(blog_feed)}건")

    if not blog_feed:
        st.info("블로그 피드를 불러오지 못했습니다. 블로그 ID를 확인하거나 잠시 후 새로고침하세요.")

    for idx, item in enumerate(blog_feed):
        item_saved = f"{item['company']}::{item['position']}" in saved
        st.markdown(
            ui.blog_card(item, is_new=item["job_key"] in new_keys, is_saved=item_saved),
            unsafe_allow_html=True,
        )
        if st.button(
            "✅ 보관됨" if item_saved else "⭐ 스크랩",
            key=f"save_blog_{idx}_{item['job_key']}",
            disabled=item_saved,
            use_container_width=True,
        ):
            db.save_job(item)
            st.toast(f"{item['company']} 공고를 보관함에 저장했습니다.")
            st.rerun()
        st.markdown('<div style="margin-bottom:14px;"></div>', unsafe_allow_html=True)

st.divider()


# ==========================================
# 내 프로필 · AI 기능
# ==========================================
my_profile = profile_mod.load_profile()
ai_ready = ai.is_available()


def _profile_gate() -> bool:
    """AI 기능을 쓸 수 있는 상태인지 확인하고, 아니면 이유를 표시한다."""
    if not ai_ready:
        st.info(
            "이 기능은 Claude API 키가 필요합니다. `.env`(또는 Streamlit Secrets)에 "
            "`ANTHROPIC_API_KEY`를 넣으면 켜집니다. "
            "키는 https://console.anthropic.com 에서 발급합니다."
        )
        return False
    if not my_profile.is_usable():
        missing = ", ".join(my_profile.missing_parts())
        st.warning(
            f"먼저 아래 '내 프로필'을 채워주세요. 부족한 항목: {missing}\n\n"
            "없는 경력을 지어내지 않도록, 프로필에 적힌 사실만 재료로 씁니다."
        )
        return False
    return True


def render_fit_tab(job: dict, prof) -> None:
    state_key = f"fit_{job['id']}"
    if not _profile_gate():
        return

    if st.button("🎯 적합도 분석하기", key=f"fit_btn_{job['id']}"):
        try:
            with st.spinner("공고와 내 프로필을 비교하는 중..."):
                st.session_state[state_key] = ai.analyze_fit(prof, job)
        except Exception as exc:
            st.error(f"분석에 실패했습니다: {type(exc).__name__}")
            st.caption(str(exc)[:300])
            return

    result = st.session_state.get(state_key)
    if result is None:
        st.caption("버튼을 누르면 내 프로필과 이 공고를 비교합니다.")
        return

    head = st.columns([1, 3])
    head[0].metric("적합도", f"{result.score}점")
    head[1].markdown(f"**{result.verdict}**\n\n{result.summary}")

    if result.matches:
        st.markdown("**✅ 맞는 지점**")
        for item in result.matches:
            st.markdown(f"- {item}")
    if result.gaps:
        st.markdown("**⚠️ 부족하거나 확인이 필요한 점**")
        for item in result.gaps:
            st.markdown(f"- {item}")
    if result.actions:
        st.markdown("**📌 지원 전 준비**")
        for item in result.actions:
            st.markdown(f"- {item}")


def render_letter_tab(job: dict, prof) -> None:
    saved_letters = profile_mod.load_cover_letters(job["company"])
    if saved_letters:
        st.caption(f"저장된 자소서 {len(saved_letters)}건")
        for letter in saved_letters:
            with st.expander(f"📄 {letter['question']}", expanded=False):
                st.text_area(
                    "내용", value=letter["answer"], height=200,
                    key=f"saved_letter_{letter['id']}", label_visibility="collapsed",
                )
                st.caption(f"{len(letter['answer'])}자 · 수정 {letter['updated_at'][:10]}")
                if st.button("🗑️ 삭제", key=f"del_letter_{letter['id']}"):
                    profile_mod.delete_cover_letter(letter["id"])
                    st.rerun()
        st.divider()

    if not _profile_gate():
        return

    q_col, len_col = st.columns([2, 1])
    with q_col:
        question = st.selectbox(
            "자소서 문항",
            ai.DEFAULT_QUESTIONS + ["(직접 입력)"],
            key=f"q_sel_{job['id']}",
        )
        if question == "(직접 입력)":
            question = st.text_input(
                "문항을 그대로 입력하세요", key=f"q_custom_{job['id']}",
                placeholder="예: 입사 후 이루고 싶은 목표를 기술하시오",
            )
    with len_col:
        max_chars = st.number_input(
            "분량 (자)", min_value=200, max_value=2000, value=700, step=100,
            key=f"len_{job['id']}",
        )

    draft_key = f"draft_{job['id']}"
    if st.button("✍️ 초안 생성", key=f"draft_btn_{job['id']}", disabled=not question):
        placeholder = st.empty()
        try:
            with st.spinner("초안을 쓰는 중..."):
                text = ai.draft_cover_letter(
                    prof, job, question, max_chars=int(max_chars),
                    on_text=lambda partial: placeholder.markdown(partial),
                )
            placeholder.empty()
            st.session_state[draft_key] = text
        except Exception as exc:
            placeholder.empty()
            st.error(f"생성에 실패했습니다: {type(exc).__name__}")
            st.caption(str(exc)[:300])
            return

    draft = st.session_state.get(draft_key, "")
    if draft:
        edited = st.text_area(
            "초안 (그대로 쓰지 말고 본인 표현으로 다듬으세요)",
            value=draft, height=320, key=f"draft_area_{job['id']}",
        )
        st.caption(f"{len(edited)}자")
        if st.button("💾 이 자소서 저장", key=f"save_letter_{job['id']}"):
            profile_mod.save_cover_letter(
                job["id"], job["company"], question, edited
            )
            st.session_state.pop(draft_key, None)
            st.toast("저장했습니다.")
            st.rerun()


# ------------------------------------------
# 하단 - 보관함 & 지원 현황 트래커
# ------------------------------------------
st.markdown("### 📁 보관함 · 지원 현황 트래커")

filter_col, alert_col, export_col = st.columns([2, 1, 1])
with filter_col:
    status_filter = st.selectbox("상태 필터", ["전체", *APPLICATION_STATUSES])
saved_jobs = db.load_jobs(None if status_filter == "전체" else status_filter)

with alert_col:
    st.markdown('<div style="height:28px;"></div>', unsafe_allow_html=True)
    can_notify = bool(settings.telegram_config() or settings.email_config())
    if st.button("📨 마감 알림 보내기", disabled=not can_notify,
                 help="TELEGRAM_* 또는 SMTP_* 설정이 필요합니다" if not can_notify else None):
        result = notify.run(within_days=3, today=today)
        if result["found"] == 0:
            st.toast("마감 3일 이내인 보관 공고가 없습니다.")
        elif result["to_send"] == 0:
            st.toast("오늘 이미 알림을 보냈습니다.")
        elif result["sent"]:
            st.toast(f"발송 완료: {', '.join(result['channels'])}")
        else:
            st.toast("발송에 실패했습니다. 로그를 확인해 주세요.")

with export_col:
    st.markdown('<div style="height:28px;"></div>', unsafe_allow_html=True)
    if saved_jobs:
        buffer = io.StringIO()
        fields = [
            "company", "position", "status", "applied_at", "deadline",
            "location", "source", "memo", "link",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(saved_jobs)
        st.download_button(
            "⬇️ CSV 내려받기",
            data=buffer.getvalue().encode("utf-8-sig"),
            file_name=f"job_tracker_{today.isoformat()}.csv",
            mime="text/csv",
        )

if not saved_jobs:
    st.info("보관함이 비어 있습니다. 위에서 마음에 드는 공고를 스크랩해 보세요.")
else:
    for job in saved_jobs:
        st.markdown(ui.saved_card(job), unsafe_allow_html=True)
        with st.expander(f"✏️ {job['company']} — 진행 상황 · 적합도 · 자소서", expanded=False):
            tab_status, tab_fit, tab_letter = st.tabs(["진행 상황", "🎯 적합도 분석", "✍️ 자소서"])

            with tab_fit:
                render_fit_tab(job, my_profile)

            with tab_letter:
                render_letter_tab(job, my_profile)

            with tab_status:
                edit_cols = st.columns([1.2, 1, 2])
                with edit_cols[0]:
                    current = job.get("status", APPLICATION_STATUSES[0])
                    index = APPLICATION_STATUSES.index(current) if current in APPLICATION_STATUSES else 0
                    new_status = st.selectbox(
                        "진행 상태", APPLICATION_STATUSES, index=index, key=f"status_{job['id']}"
                    )
                with edit_cols[1]:
                    applied_at = st.text_input(
                        "지원일 (YYYY-MM-DD)", value=job.get("applied_at", ""), key=f"applied_{job['id']}"
                    )
                with edit_cols[2]:
                    memo = st.text_area(
                        "메모 (자소서 소재, 면접 일정 등)",
                        value=job.get("memo", ""),
                        key=f"memo_{job['id']}",
                        height=80,
                    )

                action_cols = st.columns([1, 1, 2])
                with action_cols[0]:
                    if st.button("💾 저장", key=f"update_{job['id']}"):
                        db.update_job(
                            job["id"], status=new_status, applied_at=applied_at.strip(), memo=memo
                        )
                        st.toast("업데이트했습니다.")
                        st.rerun()
                with action_cols[1]:
                    if st.button("🗑️ 삭제", key=f"delete_{job['id']}"):
                        db.delete_job(job["id"])
                        st.rerun()
                with action_cols[2]:
                    if job.get("link"):
                        st.link_button("🌐 공고 열기", url=job["link"])


st.divider()

# ------------------------------------------
# 하단 - 시장 트렌드 (수집한 공고 집계, 추가 키 불필요)
# ------------------------------------------
if jobs:
    stats = insights.summarize(jobs)
    st.markdown("### 📊 지금 시장 (수집한 공고 기준)")

    head = st.columns(4)
    head[0].metric("수집 공고", f"{stats.total:,}")
    head[1].metric("직접고용 추정", f"{stats.direct_hire_estimate:,}",
                   help="다수 공고 회사를 제외한 건수입니다.")
    head[2].metric("다수 공고 회사 건", f"{stats.bulk_poster_jobs:,}")
    head[3].metric(f"{insights.LONG_LISTING_DAYS}일+ 게시", f"{stats.long_listings:,}",
                   help="이력이 쌓인 뒤부터 집계됩니다. 처음 실행하면 0입니다.")

    t_skill, t_loc, t_emp, t_bulk = st.tabs(
        ["🔧 자격증·기술", "📍 지역", "📄 고용형태", "🏷 다수 공고 회사"]
    )

    with t_skill:
        rows = insights.skill_trends(jobs)
        if rows:
            st.caption("공고 문구에 등장한 횟수입니다. 무엇을 준비할지 정하는 데 참고하세요.")
            st.markdown(ui.trend_bars(rows), unsafe_allow_html=True)
        else:
            st.info("집계할 키워드가 없습니다.")

    with t_loc:
        rows = insights.location_trends(jobs)
        if rows:
            st.markdown(ui.trend_bars(rows), unsafe_allow_html=True)
        else:
            st.info("지역 정보가 없습니다.")

    with t_emp:
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("고용형태")
            st.markdown(ui.trend_bars(insights.employment_trends(jobs)), unsafe_allow_html=True)
        with col_b:
            st.caption("경력 조건")
            st.markdown(ui.trend_bars(insights.career_trends(jobs)), unsafe_allow_html=True)

    with t_bulk:
        rows = insights.top_bulk_posters(jobs, agency_threshold)
        if rows:
            st.caption(
                "한 회사가 검색 결과에 여러 건 올린 경우입니다. 채용대행·파견업체인 "
                "경우가 많지만, 실제로 채용 규모가 큰 회사일 수도 있으니 확인 후 판단하세요."
            )
            st.markdown(ui.trend_bars(rows), unsafe_allow_html=True)
        else:
            st.info(f"{agency_threshold}건 이상 올린 회사가 없습니다.")

    st.divider()

# ------------------------------------------
# 하단 - 내 프로필 (자소서·적합도의 재료)
# ------------------------------------------
st.markdown("### 🧑 내 프로필")
st.caption(
    "자소서 초안과 적합도 분석은 여기 적힌 사실만 재료로 씁니다. "
    "구체적으로 쓸수록 결과가 좋아지고, 없는 내용은 지어내지 않습니다."
)

with st.expander(
    "프로필 편집" + ("" if my_profile.is_usable() else "  ⚠️ 아직 비어 있습니다"),
    expanded=not my_profile.is_usable(),
):
    p_col1, p_col2 = st.columns(2)
    with p_col1:
        p_name = st.text_input("이름", value=my_profile.name)
        p_desired = st.text_input(
            "희망 직무", value=my_profile.desired_role, placeholder="예: 생산관리, 설비보전"
        )
        p_education = st.text_input(
            "학력", value=my_profile.education, placeholder="예: OO공업고등학교 기계과 졸업"
        )
        p_certificates = st.text_input(
            "자격증", value=my_profile.certificates, placeholder="예: 지게차운전기능사, 위험물기능사"
        )
    with p_col2:
        p_career = st.text_area(
            "경력 사항",
            value=my_profile.career,
            height=120,
            placeholder="예: OO산업 생산직 2년 (2023.03~2025.02)\n- 조립 라인 오퍼레이터, 3교대 근무",
        )
        p_skills = st.text_input(
            "보유 기술", value=my_profile.skills, placeholder="예: 지게차 운전, 설비 점검, 엑셀"
        )
        p_strengths = st.text_input(
            "본인이 생각하는 강점", value=my_profile.strengths, placeholder="예: 꼼꼼한 기록 관리"
        )

    st.markdown("**경험 에피소드** — 자소서에서 근거로 쓰입니다. 한 개라도 있으면 결과가 크게 달라집니다.")
    episode_count = st.number_input(
        "에피소드 개수", min_value=1, max_value=8,
        value=max(1, len(my_profile.filled_episodes())), step=1,
    )

    new_episodes = []
    for idx in range(int(episode_count)):
        existing = my_profile.episodes[idx] if idx < len(my_profile.episodes) else profile_mod.Episode()
        st.markdown(f"---\n**에피소드 {idx + 1}**")
        e_title = st.text_input(
            "한 줄 제목", value=existing.title, key=f"ep_title_{idx}",
            placeholder="예: 설비 고장 대응으로 라인 정지 시간 단축",
        )
        e_cols = st.columns(3)
        with e_cols[0]:
            e_situation = st.text_area(
                "상황", value=existing.situation, key=f"ep_sit_{idx}", height=100,
                placeholder="어떤 문제/상황이었는지",
            )
        with e_cols[1]:
            e_action = st.text_area(
                "내가 한 행동", value=existing.action, key=f"ep_act_{idx}", height=100,
                placeholder="본인이 직접 한 일",
            )
        with e_cols[2]:
            e_result = st.text_area(
                "결과", value=existing.result, key=f"ep_res_{idx}", height=100,
                placeholder="가능하면 숫자로 (예: 30분 → 10분)",
            )
        new_episodes.append(
            profile_mod.Episode(
                title=e_title, situation=e_situation, action=e_action, result=e_result
            )
        )

    if st.button("💾 프로필 저장", type="primary"):
        profile_mod.save_profile(
            profile_mod.Profile(
                name=p_name,
                career=p_career,
                education=p_education,
                certificates=p_certificates,
                skills=p_skills,
                desired_role=p_desired,
                strengths=p_strengths,
                episodes=new_episodes,
            )
        )
        st.toast("프로필을 저장했습니다.")
        st.rerun()
