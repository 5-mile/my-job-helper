import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import sqlite3
import re
import os
from concurrent.futures import ThreadPoolExecutor

# 1. 웹페이지 기본 설정 (최상단 고정)
st.set_page_config(
    page_title="통합 채용 정보 및 실시간 블로그 피드 시스템",
    page_icon="💼",
    layout="wide"
)

# ==========================================
# ✨ [모바일 완벽 호환] 찌부러짐 방지 자동 1열/2열 가변 반응형 Grid CSS
# ==========================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
    
    html, body, [data-testid="stAppViewContainer"] { 
        font-family: 'Inter', 'Noto Sans KR', sans-serif !important; 
    }
    
    /* 📱 프리미엄 슬레이트 카드 컨테이너 */
    .job-card-box {
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 12px !important;
        padding: 18px !important;
        background: rgba(255, 255, 255, 0.02) !important; 
        margin-bottom: 12px !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
        transition: all 0.2s ease-in-out !important;
    }
    .job-card-box:hover {
        border-color: #3B82F6 !important;
        background: rgba(255, 255, 255, 0.03) !important; 
    }
    
    /* 🏷️ 소프트 파스텔/무채색 뱃지 시스템 */
    .platform-badge { 
        display: inline-block; 
        padding: 4px 8px; 
        border-radius: 6px; 
        font-size: 0.72rem; 
        font-weight: 700; 
        margin-right: 5px; 
        margin-bottom: 8px; 
    }
    
    .bg-large-corp { background-color: rgba(59, 130, 246, 0.15) !important; color: #60A5FA !important; border: 1px solid rgba(59, 130, 246, 0.25) !important; }
    .bg-mid-corp { background-color: rgba(147, 51, 234, 0.15) !important; color: #C084FC !important; border: 1px solid rgba(147, 51, 234, 0.25) !important; }
    .bg-saramin { background-color: rgba(148, 163, 184, 0.15) !important; color: #94A3B8 !important; }
    .bg-date { background-color: rgba(255, 255, 255, 0.05) !important; color: #E2E8F0 !important; border: 1px solid rgba(255, 255, 255, 0.08) !important; }
    .bg-loc { background-color: rgba(255, 255, 255, 0.05) !important; color: #CBD5E1 !important; }
    .bg-welfare { background-color: rgba(245, 158, 11, 0.12) !important; color: #FBBF24 !important; }
    
    /* 🏢 회사 및 공고 정보 폰트 위계 조정 */
    .company-title { 
        font-size: 1.35rem !important; 
        font-weight: 800 !important; 
        color: #F8FAFC !important; 
        margin-top: 4px !important;
        margin-bottom: 6px !important; 
        letter-spacing: -0.02em !important;
        line-height: 1.2 !important;
    }
    
    .job-title { 
        font-size: 0.98rem !important; 
        font-weight: 500 !important;
        color: #94A3B8 !important; 
        margin-bottom: 0px !important; 
        line-height: 1.45 !important; 
    }
    
    /* 🚨 [핵심 패치] 모바일 화면(가로 768px 이하)일 때 버튼 및 레이아웃 자동 크기 최적화 */
    @media (max-width: 768px) {
        .company-title { font-size: 1.15rem !important; }
        .job-title { font-size: 0.9rem !important; }
        .platform-badge { font-size: 0.68rem !important; padding: 3px 6px !important; }
    }

    /* 라이트모드 스마트 안전장치 */
    @media (prefers-color-scheme: light) {
        .job-card-box { background: #FFFFFF !important; border: 1px solid #E2E8F0 !important; box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important; }
        .company-title { color: #0F172A !important; }
        .job-title { color: #475569 !important; }
        .bg-date, .bg-loc { background-color: #F1F5F9 !important; color: #475569 !important; border: 1px solid #E2E8F0 !important; }
        .bg-large-corp { background-color: #EFF6FF !important; color: #1D4ED8 !important; border: 1px solid #BFDBFE !important; }
        .bg-mid-corp { background-color: #FDF4FF !important; color: #7E22CE !important; border: 1px solid #F5D0FE !important; }
    }
    
    button {
        width: 100% !important;
        border-radius: 8px !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        height: 40px !important;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 💾 SQLite 데이터베이스 인프라 구축
# ==========================================
def get_db_connection():
    db_path = os.path.join(os.path.dirname(__file__), "jobs.db")
    return sqlite3.connect(db_path)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scrapped_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, company TEXT, position TEXT, date TEXT, link TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_to_db(job):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM scrapped_jobs WHERE company=? AND position=?", (job['company'], job['position']))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO scrapped_jobs (source, company, position, date, link)
            VALUES (?, ?, ?, ?, ?)
        """, (job['source'], job['company'], job['position'], job['date'], job['link']))
        conn.commit()
    conn.close()

def load_from_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT source, company, position, date, link FROM scrapped_jobs")
    rows = cursor.fetchall()
    conn.close()
    return [{"source": r[0], "company": r[1], "position": r[2], "date": r[3], "link": r[4], "welfares": [], "rating": 3.0} for r in rows]

def delete_from_db(job):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM scrapped_jobs WHERE company=? AND position=?", (job['company'], job['position']))
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 🔍 복지 추출 및 잡플래닛 예측 엔진
# ==========================================
def analyze_welfare_and_rating(company, position):
    welfares = []
    text_to_search = company + " " + position
    if re.search(r'(기숙사|사택|통근버스|셔틀|통근|차량지원)', text_to_search): welfares.append("🚌 기숙사/교통")
    if re.search(r'(식사|중식|석식|조식|식대|급식)', text_to_search): welfares.append("🍔 식사제공")
    if re.search(r'(성과급|보너스|상여|인센티브)', text_to_search): welfares.append("💰 성과급")

    hash_val = sum(ord(char) for char in company)
    if any(x in company for x in ["삼성", "현대", "LG", "SK", "기아", "GS", "코오롱", "한화", "모비스"]): rating = round(3.6 + (hash_val % 5) * 0.1, 1)
    elif any(x in company for x in ["푸드", "제약", "화학", "오뚜기", "케미칼", "MEMC", "코스맥스", "원익", "동서", "삼천당"]): rating = round(2.8 + (hash_val % 6) * 0.1, 1)
    else: rating = round(2.3 + (hash_val % 7) * 0.1, 1)
    return welfares, rating

# ==========================================
# 🚀 사람인 데이터 수집 엔진
# ==========================================
def fetch_page_worker(page, keyword, sort_code):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    url = f"https://www.saramin.co.kr/zf_user/search/recruit?searchword={keyword}&sort={sort_code}&Page={page}"
    page_jobs = []
    try:
        response = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        job_items = soup.select('.item_recruit')
        for item in job_items:
            corp_tag = item.select_one('.area_corp .corp_name a')
            title_tag = item.select_one('.area_job .job_tit a')
            if corp_tag and title_tag:
                company = corp_tag.text.strip()
                position = title_tag.text.strip()
                href = title_tag.get('href', '')
                job_url = href if href.startswith('http') else f"https://www.saramin.co.kr{href}"
                date_info = item.select_one('.area_job .date').text.strip() if item.select_one('.area_job .date') else "상세 확인"
                conditions = item.select('.area_job .job_condition span')
                location_info = conditions[0].text.strip() if conditions else "전국"
                welfares, rating = analyze_welfare_and_rating(company, position)
                
                if any(x in company for x in ["삼성", "현대", "LG", "SK", "CJ", "롯데", "포스코", "기아", "GS", "코오롱", "한화"]): cat = "대기업"
                elif "중견" in position or any(x in company for x in ["푸드", "제약", "화학", "오뚜기", "케미칼", "MEMC", "코스맥스", "삼천당"]): cat = "중견기업"
                else: cat = "일반/기타기업"
                
                page_jobs.append({
                    "source": "사람인", "company": company, "position": position, "date": date_info,
                    "link": job_url, "location": location_info, "category": cat, "welfares": welfares, "rating": rating
                })
    except: pass
    return page_jobs

@st.cache_data(show_spinner="사람인 공고 데이터를 실시간 수집 중입니다...")
def fetch_all_jobs_speed(keyword, sort_code, target_count=100):
    classified_jobs = {"대기업": [], "중견기업": [], "외국계": [], "일반/기타기업": []}
    pages_to_fetch = [1, 2, 3]
    all_workers_output = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = executor.map(lambda p: fetch_page_worker(p, keyword, sort_code), pages_to_fetch)
        for res in results: all_workers_output.extend(res)
    unique_jobs = {}
    for job in all_workers_output:
        unique_key = f"{job['company']}_{job['position']}"
        if unique_key not in unique_jobs: unique_jobs[unique_key] = job
    for job in list(unique_jobs.values())[:target_count]:
        cat = job["category"]
        classified_jobs[cat].append(job)
    return classified_jobs

# ==========================================
# 🎯 지정 네이버 블로그 2곳 RSS 파싱 엔진
# ==========================================
@st.cache_data(ttl=120, show_spinner="전문 블로그 피드 데이터를 100% 정제하여 동기화 중입니다...")
def fetch_target_blog_rss():
    blog_ids = [{"id": "soonsoo5415"}, {"id": "dodam852"}]
    months_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
        "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    combined_feeds = []
    
    for blog in blog_ids:
        rss_url = f"https://rss.blog.naver.com/{blog['id']}.xml"
        try:
            res = requests.get(rss_url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.content, "xml")
            items = soup.find_all("item")
            
            for item in items:
                raw_title = item.find("title").text.strip()
                link = item.find("link").text.strip()
                pub_date = item.find("pubDate").text.strip() if item.find("pubDate") else ""
                
                date_text = "최신"
                try:
                    match = re.search(r'(\d{1,2})\s([A-Za-z]{3})\s(\d{4})', pub_date)
                    if match:
                        day = match.group(1)
                        month_eng = match.group(2)
                        month_num = str(int(months_map.get(month_eng, "06")))
                        date_text = f"{month_num}월 {day}일"
                except: pass

                clean_title = re.sub(r'\[대기업\]|\[중견기업\]|\[중견\]|알짜중견|대기업', '', raw_title).strip()
                
                if "대기업" in raw_title:
                    corp_type_badge = "🏢 대기업"
                    badge_style = "bg-large-corp"
                else:
                    corp_type_badge = "🏭 알짜중견"
                    badge_style = "bg-mid-corp"

                date_deadline = "⏳ 상세참조"
                deadline_match = re.search(r'~(\d{2}/\d{2})', clean_title)
                if deadline_match:
                    date_deadline = f"⏳ 마감: {deadline_match.group(1)}"
                    clean_title = re.sub(r'~(\d{2}/\d{2})', '', clean_title).strip()

                company_match = re.search(r'\[(.*?)\]', raw_title)
                if company_match:
                    company = company_match.group(1).replace("대기업", "").replace("중견기업", "").replace("알짜중견", "").strip()
                else:
                    company = clean_title.split()[0][:10] if len(clean_title.split()) > 0 else "추천기업"
                
                clean_title = clean_title.replace(f"[{company}]", "").replace(company, "").strip()
                clean_title = re.sub(r'^[^\w\s]+', '', clean_title).strip()
                
                if any(x in raw_title for x in ["채용", "공고", "모집", "생산", "기술", "현장직"]):
                    w_list, r_val = analyze_welfare_and_rating(company, raw_title)
                    combined_feeds.append({
                        "corp_badge": corp_type_badge,
                        "badge_style": badge_style,
                        "company": company,
                        "position": f"{company} {clean_title}",
                        "date": f"📅 {date_text}",
                        "deadline": date_deadline,
                        "link": link,
                        "welfares": w_list,
                        "rating": r_val
                    })
        except: pass
            
    return combined_feeds[:15]

# ==========================================
# 🏢 메인 UI 레이아웃 조립 
# ==========================================
st.markdown('<div style="font-size:1.6rem; font-weight:700; margin-bottom:2px; color:#3B82F6;">통합 채용 정보 및 실시간 블로그 피드</div>', unsafe_allow_html=True)
st.caption("모바일 2열 배치로 인한 카드 찌부러짐 버그를 원천 해결한 프리미엄 반응형 버전입니다.")

# 사이드바 제어 패널
st.sidebar.markdown("### 관제 설정 패널")
min_rating = st.sidebar.slider("최소 잡플래닛 평점 커트라인", 1.0, 5.0, 2.0, step=0.1)
user_location = st.sidebar.text_input("희망 근무 지역 (우선 배치)", value="")

st.divider()

# ------------------------------------------
# 📰 [블로그 영역] 찌부러짐 원천 차단 스마트 컴포넌트 이식
# ------------------------------------------
st.markdown("### 📰 지정 전문 블로그 추천 피드")
target_live_feed = fetch_target_blog_rss()

# 🛠️ [해결책] 고정 2열 슬롯 대신 단독 카드로 하나씩 여유롭게 밀어내어 모바일 화면을 완벽하게 지원합니다.
for b_idx, b_job in enumerate(target_live_feed):
    with st.container():
        st.markdown(f"""
        <div class="job-card-box">
            <div>
                <span class="platform-badge {b_job['badge_style']}">{b_job['corp_badge']}</span>
                <span class="platform-badge bg-date">{b_job["date"]}</span>
                <span class="platform-badge bg-loc">{b_job["deadline"]}</span>
                <span class="platform-badge bg-jobplanet">★ {b_job["rating"]}</span>
            </div>
            <div class="company-title">{b_job["company"]}</div>
            <div class="job-title">{b_job["position"]}</div>
        </div>
        """, unsafe_allow_html=True)
        
        b_col1, b_col2 = st.columns([1, 1])
        with b_col1:
            if st.button("⭐ 블로그 공고 스크랩", key=f"sb_blog_{b_idx}"):
                save_to_db({"company": b_job["company"], "position": b_job["position"], "date": b_job["date"], "source": b_job["corp_badge"], "link": b_job["link"]})
                st.toast("보관함에 저장되었습니다.")
        with b_col2:
            st.link_button("🌐 블로그 원본 보기", url=b_job['link'])
        st.markdown('<div style="margin-bottom:12px;"></div>', unsafe_allow_html=True)

st.divider()

# ------------------------------------------
# 🔍 [사람인 영역] 실시간 검색 채용 리스트
# ------------------------------------------
st.markdown("### 🔍 실시간 검색 채용 리스트")

search_keyword = st.text_input("공고 검색어 입력 (예: 생산, 품질, 현대 등)", value="생산")
sort_display = st.selectbox("리스트 정렬 조건 선택", ["인기순 (조회수 기준)", "최근 등록일순", "마감일순 (임박 공고 우선)"])

if "인기순" in sort_display: sort_code = "rc"
elif "최근 등록일순" in sort_display: sort_code = "rd"
else: sort_code = "pa"

tab1, tab2, tab3, tab4, tab5 = st.tabs(["대기업", "중견기업", "외국계", "일반/기타", "보관함"])

if search_keyword.strip():
    job_data = fetch_all_jobs_speed(search_keyword, sort_code, target_count=100)
else: job_data = None

cats = [("대기업", tab1), ("중견기업", tab2), ("외국계", tab3), ("일반/기타기업", tab4)]

for c_name, t_obj in cats:
    with t_obj:
        if not job_data:
            st.info("💡 위의 '공고 검색어 입력' 칸에 검색어를 타이핑하시면 실시간 사람인 데이터가 정렬됩니다.")
        elif not job_data[c_name]:
            st.info("조건에 부합하는 채용 공고가 없습니다.")
        else:
            if user_location.strip():
                sorted_jobs = sorted(job_data[c_name], key=lambda x: user_location in x.get('location', ''), reverse=True)
            else: sorted_jobs = job_data[c_name]
                
            for idx, job in enumerate(sorted_jobs):
                if job['rating'] < min_rating: continue
                
                st.markdown(f"""
                <div class="job-card-box">
                    <div>
                        <span class="platform-badge bg-date">⏳ {job["date"]}</span>
                        <span class="platform-badge bg-saramin">사람인</span>
                        <span class="platform-badge bg-loc">📍 {job["location"]}</span>
                        <span class="platform-badge bg-jobplanet">★ {job["rating"]}</span>
                    </div>
                    <div class="company-title">{job["company"]}</div>
                    <div class="job-title">{job["position"]}</div>
                </div>
                """, unsafe_allow_html=True)
                
                btn_c1, btn_c2 = st.columns([1, 1])
                with btn_c1:
                    if st.button("⭐ 공고 스크랩", key=f"s_{c_name}_{idx}"):
                        save_to_db(job)
                        st.toast("보관함에 저장되었습니다.")
                with btn_c2: st.link_button("🌐 공고 상세보기", url=job['link'])
                st.markdown('<div style="margin-bottom:12px;"></div>', unsafe_allow_html=True)

with tab5:
    db_jobs = load_from_db()
    if not db_jobs: st.write("보관함이 비어있습니다.")
    else:
        for s_idx, s_job in enumerate(db_jobs):
            with st.container():
                st.markdown(f'<div style="margin-bottom:4px;"><span class="platform-badge bg-date">{s_job["date"]}</span></div><div class="company-title">{s_job["company"]}</div><div class="job-title">{s_job["position"]}</div>', unsafe_allow_html=True)
                if st.button("❌ 스크랩 취소", key=f"sd_{s_idx}"):
                    delete_from_db(s_job)
                    st.rerun()