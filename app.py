"""통합 채용 정보 대시보드 - Streamlit 진입점."""

from __future__ import annotations

import csv
import io
from datetime import date

import streamlit as st

from jobhelper import company_info, db, notify, settings, storage, ui
from jobhelper.config import (
    APPLICATION_STATUSES,
    CATEGORIES,
    DEFAULT_BLOG_IDS,
    SARAMIN_SORT_OPTIONS,
)
from jobhelper.dates import days_left, parse_deadline
from jobhelper.scrapers.naver_blog import fetch_blog_feed
from jobhelper.scrapers.saramin import fetch_saramin_jobs_detailed, group_by_category
from jobhelper.scrapers.worknet import fetch_worknet_jobs

st.set_page_config(
    page_title="통합 채용 정보 및 지원 현황 관리",
    page_icon="💼",
    layout="wide",
)
st.markdown(ui.CSS, unsafe_allow_html=True)

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
def load_saramin(keywords: tuple[str, ...], sort_code: str, pages: int, exclude: tuple[str, ...]):
    jobs, diagnostics = fetch_saramin_jobs_detailed(list(keywords), sort_code, pages, list(exclude))
    return jobs, diagnostics.warning


@st.cache_data(ttl=600, show_spinner="워크넷 공고를 수집하는 중입니다...")
def load_worknet(keywords: tuple[str, ...], pages: int, exclude: tuple[str, ...]):
    return fetch_worknet_jobs(list(keywords), pages=pages, exclude=list(exclude))


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
sort_code = SARAMIN_SORT_OPTIONS[sort_display]
pages = st.sidebar.slider("검색어당 수집 페이지 수", 1, 5, 3)

has_worknet = bool(settings.worknet_auth_key())
use_worknet = st.sidebar.checkbox(
    "워크넷 공고 함께 보기",
    value=has_worknet,
    disabled=not has_worknet,
    help="WORKNET_AUTH_KEY를 .env에 넣으면 켜집니다" if not has_worknet else None,
)

st.sidebar.markdown("### 🎚️ 필터")
user_location = st.sidebar.text_input("희망 근무 지역 (우선 배치)", value="")
only_with_deadline = st.sidebar.checkbox("마감일이 있는 공고만 보기", value=False)
urgent_only = st.sidebar.checkbox("마감 7일 이내만 보기", value=False)
hide_saved = st.sidebar.checkbox("이미 보관한 공고 숨기기", value=False)

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
summary_cols = st.columns(len(APPLICATION_STATUSES))
for col, status in zip(summary_cols, APPLICATION_STATUSES):
    col.metric(status, counts.get(status, 0))

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
fetch_warning = ""
if keywords:
    saramin_jobs, fetch_warning = load_saramin(keywords, sort_code, pages, excludes)
    jobs.extend(saramin_jobs)
    if use_worknet and has_worknet:
        jobs.extend(load_worknet(keywords, pages, excludes))

if fetch_warning:
    st.error(f"⚠️ {fetch_warning}")

blog_feed = load_blog(blog_ids, blog_limit) if blog_ids else []

if has_nps and jobs:
    jobs = enrich(jobs, 40)

new_keys = db.mark_seen([j["job_key"] for j in jobs] + [b["job_key"] for b in blog_feed])


def passes_filters(job: dict) -> bool:
    if hide_saved and f"{job['company']}::{job['position']}" in saved:
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
    sources = "사람인 + 워크넷" if (use_worknet and has_worknet) else "사람인"
    st.markdown(f"#### 실시간 검색 채용 리스트 · {total_shown}건 ({sources})")

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

            for idx, job in enumerate(visible):
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

                btn_left, btn_right = st.columns(2)
                with btn_left:
                    if st.button(
                        "✅ 보관됨" if job_saved else "⭐ 공고 스크랩",
                        key=f"save_{category}_{idx}_{job['job_key']}",
                        disabled=job_saved,
                    ):
                        db.save_job(job)
                        st.toast(f"{job['company']} 공고를 보관함에 저장했습니다.")
                        st.rerun()
                with btn_right:
                    st.link_button("🌐 공고 상세보기", url=job["link"])
                st.markdown('<div style="margin-bottom:14px;"></div>', unsafe_allow_html=True)

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
        b_left, b_right = st.columns(2)
        with b_left:
            if st.button(
                "✅ 보관됨" if item_saved else "⭐ 스크랩",
                key=f"save_blog_{idx}_{item['job_key']}",
                disabled=item_saved,
            ):
                db.save_job(item)
                st.toast(f"{item['company']} 공고를 보관함에 저장했습니다.")
                st.rerun()
        with b_right:
            st.link_button("🌐 원문 보기", url=item["link"])
        st.markdown('<div style="margin-bottom:14px;"></div>', unsafe_allow_html=True)

st.divider()

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
        with st.expander(f"✏️ {job['company']} 진행 상황 편집", expanded=False):
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
