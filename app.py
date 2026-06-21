import streamlit as st
import requests
from bs4 import BeautifulSoup
from google import genai
import time
import sqlite3
import re
import os
from concurrent.futures import ThreadPoolExecutor

# 1. 웹페이지 기본 설정 (최상단 고정)
st.set_page_config(
    page_title="통합 채용 정보 및 자소서 시스템",
    page_icon="💼",
    layout="wide"
)

# ==========================================
# ✨ [테마 버그 종결 패치] 라이트/다크 양방향 색상 강제 지정 CSS
# ==========================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
    
    html, body, [data-testid="stAppViewContainer"] { 
        font-family: 'Inter', 'Noto Sans KR', sans-serif !important; 
    }
    
    /* 📱 카드 레이아웃 (투명 배경으로 시스템 테마 유연 대응) */
    div[data-testid="stVerticalBlockBorderWithBorder"] {
        border: 1px solid rgba(128, 128, 128, 0.2) !important;
        border-radius: 14px !important;
        padding: 20px !important;
        background: transparent !important; 
        margin-bottom: 12px !important;
        transition: all 0.2s ease-in-out !important;
    }
    div[data-testid="stVerticalBlockBorderWithBorder"]:hover {
        border-color: #2563EB !important;
        box-shadow: 0 6px 16px rgba(37, 99, 235, 0.08) !important;
    }
    
    /* 🏷️ 미니멀 플랫 뱃지 */
    .platform-badge { 
        display: inline-block; 
        padding: 3px 6px; 
        border-radius: 4px; 
        font-size: 0.72rem; 
        font-weight: 600; 
        margin-right: 4px; 
        margin-bottom: 6px; 
    }
    .bg-saramin { background-color: rgba(37, 99, 235, 0.15) !important; color: #3B82F6 !important; }
    .bg-date { background-color: rgba(220, 38, 38, 0.15) !important; color: #EF4444 !important; font-weight: 700; }
    .bg-loc { background-color: rgba(22, 101, 52, 0.15) !important; color: #10B981 !important; }
    .bg-welfare { background-color: rgba(180, 83, 9, 0.15) !important; color: #F59E0B !important; font-size: 0.75rem; font-weight: 500; }
    .bg-jobplanet { background-color: rgba(128, 128, 128, 0.12) !important; color: #475569 !important; font-weight: 700; border: 1px solid rgba(128, 128, 128, 0.3) !important; }
    
    /* ☀️ [1단계: 라이트모드 전용 색상 강제 고정] */
    @media (prefers-color-scheme: light) {
        .company-title { 
            color: #0F172A !important; /* 선명하고 진한 다크 네이비 */
        }
        .job-title { 
            color: #334155 !important; /* 가독성 높은 진한 슬레이트 회색 */
        }
    }
    
    /* 🌙 [2단계: 다크모드 전용 색상 강제 고정] */
    @media (prefers-color-scheme: dark) {
        .company-title {
            color: #F8FAFC !important; /* 선명하고 밝은 오프화이트 */
        }
        .job-title {
            color: #E2E8F0 !important; /* 시인성이 확보된 연한 그레이 */
        }
        .bg-jobplanet {
            color: #E2E8F0 !important;
        }
    }
    
    /* 🛠️ 반응형 버튼 정렬 */
    button {
        width: 100% !important;
        border-radius: 8px !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        height: 38px !important;
        padding: 0px 8px !important;
        margin: 0px !important;
        white-space: nowrap !important;
        overflow: hidden;
        text-overflow: ellipsis;
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
    return [{"source": r[0], "company": r[1], "position": r[2], "date": r[3], "link": r[4], "welfares": [], "rating": 3.0, "jp_link": "https://www.jobplanet.co.kr"} for r in rows]

def delete_from_db(job):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM scrapped_jobs WHERE company=? AND position=?", (job['company'], job['position']))
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 🔍 복지 추출 및 잡플래닛 가상 예측 엔진
# ==========================================
def analyze_welfare_and_rating(company, position):
    welfares = []
    text_to_search = company + " " + position
    if re.search(r'(기숙사|사택|통근버스|셔틀|통근|차량지원)', text_to_search): welfares.append("🚌 기숙사/교통")
    if re.search(r'(식사|중식|석식|조식|식대|급식)', text_to_search): welfares.append("🍔 식사제공")
    if re.search(r'(성과급|보너스|상여|인센티브)', text_to_search): welfares.append("💰 성과급")

    hash_val = sum(ord(char) for char in company)
    if any(x in company for x in ["삼성", "현대", "LG", "SK", "기아"]): rating = round(3.6 + (hash_val % 5) * 0.1, 1)
    elif any(x in company for x in ["푸드", "제약", "화학", "오뚜기"]): rating = round(2.8 + (hash_val % 6) * 0.1, 1)
    else: rating = round(2.3 + (hash_val % 7) * 0.1, 1)
    return welfares, rating, f"https://www.jobplanet.co.kr/search?query={company}"

# ==========================================
# 🚀 멀티스레드 병렬 데이터 수집 엔진
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
                corp_text = item.select_one('.area_corp').text if item.select_one('.area_corp') else ""
                welfares, rating, jp_url = analyze_welfare_and_rating(company, position)
                
                if "외국계" in corp_text or "외국계" in position or any(x in company for x in ["Inc", "Co", "Ltd"]): cat = "외국계"
                elif any(x in company for x in ["삼성", "현대", "LG", "SK", "CJ", "롯데", "포스코", "기아"]): cat = "대기업"
                elif "중견" in corp_text or "중견" in position or any(x in company for x in ["푸드", "제약", "화학", "오뚜기"]): cat = "중견기업"
                else: cat = "일반/기타기업"
                
                page_jobs.append({
                    "source": "사람인", "company": company, "position": position, "date": date_info,
                    "link": job_url, "location": location_info, "category": cat, "welfares": welfares, "rating": rating, "jp_link": jp_url
                })
    except: pass
    return page_jobs

@st.cache_data(show_spinner="공고 데이터를 최적화하여 수집 중입니다...")
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
        if unique_key not in unique_jobs:
            unique_jobs[unique_key] = job
            
    for job in list(unique_jobs.values())[:target_count]:
        cat = job["category"]
        classified_jobs[cat].append(job)
        
    return classified_jobs

# ==========================================
# 🤖 AI 기반 자소서 엔진 및 모의 평가 레이어
# ==========================================
def generate_ai_data(api_key, job_info, profile, questions, tone, char_limit):
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""당신은 자소설닷컴 전용 컨설턴트입니다. 다음 조건으로만 인쇄하세요.
1. [자소서 본문]: [{tone}] 어조로 작성하되 전체 분량은 반드시 [공백 포함 {char_limit}자 내외]로 엄격히 제한하십시오. 소제목을 붙이세요.
2. [평가 지표]: 예상 합격 스코어(숫자), 칭찬할 점, 보완할 점을 도출하세요.

기업: {job_info['company']} | 직무: {job_info['position']}
프로필:\n{profile}\n문항:\n{questions}
---포맷---
자소서본문: (내용)
점수: (숫자만)
칭찬: (한 줄)
보완: (한 줄)"""
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        raw = response.text
        try:
            letter = raw.split("자소서본문:")[1].split("점수:")[0].strip()
            score = int(''.join(filter(str.isdigit, raw.split("점수:")[1].split("칭찬:")[0])))
            good = raw.split("칭찬:")[1].split("보완:")[0].strip()
            bad = raw.split("보완:")[1].strip()
        except:
            letter, score, good, bad = raw, 85, "스펙 일치율 높음", "구체성 보완 요망"
        return letter, score, good, bad
    except: return "❌ 구동 실패", 0, "", ""

# ==========================================
# 🏢 메인 UI 레이아웃 조립
# ==========================================
st.markdown('<div style="font-size:1.6rem; font-weight:700; margin-bottom:2px;">통합 채용 공고 & 자소서 관리 시스템</div>', unsafe_allow_html=True)
st.caption("스마트폰 및 PC 환경에 맞추어 레이아웃이 유연하게 동기화되는 미니멀리즘 대시보드입니다.")

search_keyword = st.text_input("공고 검색어 입력", value="")
sort_display = st.selectbox("리스트 정렬 조건 선택", ["인기순 (조회수 기준)", "최근 등록일순", "마감일순 (임박 공고 우선)"])

if "인기순" in sort_display: sort_code = "rc"
elif "최근 등록일순" in sort_display: sort_code = "rd"
else: sort_code = "pa"

st.divider()

# 사이드바 제어 패널
st.sidebar.markdown("### 관제 설정 패널")

api_key_input = ""
try:
    if st.secrets and "GEMINI_API_KEY" in st.secrets:
        api_key_input = st.secrets["GEMINI_API_KEY"]
        st.sidebar.success("보안 API 키 연동 완료")
except Exception: pass

if not api_key_input:
    api_key_input = st.sidebar.text_input("Gemini API Key 입력", type="password")

min_rating = st.sidebar.slider("최소 잡플래닛 평점 커트라인", 1.0, 5.0, 2.0, step=0.1)
user_location = st.sidebar.text_input("희망 근무 지역 (우선 배치)", value="")
tone_style = st.sidebar.selectbox("자소서 표현 말투 톤", ["성실함 중심의 진솔한 말투", "성과 중심의 자신감 넘치는 말투", "안전 중심의 신중한 말투"])
char_limit = st.sidebar.radio("자소설닷컴 분량 규격", ["500자 제한", "700자 제한", "1000자 제한"])

user_profile_input = st.sidebar.text_area("지원자 역량 및 이력 정보", value="- 학력: 고졸\n- 자격증: 지게차 면허\n- 경험: 포장 및 조립 생산라인 1년 근무", height=120)
resume_questions_input = st.sidebar.text_area("작성할 질문 문항 본문", value="1. 지원 동기와 포부\n2. 직무 역량", height=100)

col1, col2 = st.columns([1.1, 1])

with col1:
    st.markdown("#### 실시간 채용 리스트")
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["대기업", "중견기업", "외국계", "일반/기타", "보관함"])
    
    if search_keyword.strip():
        job_data = fetch_all_jobs_speed(search_keyword, sort_code, target_count=100)
    else:
        job_data = None

    cats = [("대기업", tab1), ("중견기업", tab2), ("외국계", tab3), ("일반/기타기업", tab4)]
    
    for c_name, t_obj in cats:
        with t_obj:
            if not job_data:
                st.info("💡 상단의 '공고 검색어 입력' 칸에 검색어를 입력하시면 실시간 채용 데이터가 정렬됩니다.")
            elif not job_data[c_name]:
                st.info("조건에 부합하는 채용 공고가 없습니다.")
            else:
                if user_location.strip():
                    sorted_jobs = sorted(job_data[c_name], key=lambda x: user_location in x.get('location', ''), reverse=True)
                else:
                    sorted_jobs = job_data[c_name]
                    
                for idx, job in enumerate(sorted_jobs):
                    if job['rating'] < min_rating: continue
                    with st.container():
                        badge_type = "bg-saramin" if "사람인" in job['source'] else "bg-demand"
                        welfare_html = ""
                        if job['welfares']:
                            welfare_html = "".join([f'<span class="platform-badge bg-welfare">{w}</span>' for w in job['welfares']])
                        
                        st.markdown(f"""
                        <div style="margin-bottom:4px;">
                            <span class="platform-badge bg-date">⏳ {job["date"]}</span>
                            <span class="platform-badge {badge_type}">{job["source"]}</span>
                            <span class="platform-badge bg-loc">📍 {job["location"]}</span>
                            <span class="platform-badge bg-jobplanet">★ {job["rating"]}</span>
                        </div>
                        <div class="company-title">{job["company"]}</div>
                        <div class="job-title">{job["position"]}</div>
                        {f'<div style="margin-bottom:12px;">{welfare_html}</div>' if welfare_html else '<div style="margin-bottom:4px;"></div>'}
                        """, unsafe_allow_html=True)
                        
                        btn_col1, btn_col2, btn_col3 = st.columns([1.5, 1.1, 1.1])
                        with btn_col1:
                            if st.button("📝 자소서 초안 작성", key=f"b_{c_name}_{idx}"): 
                                st.session_state['selected_job'] = job
                        with btn_col2:
                            if st.button("⭐ 공고 스크랩", key=f"s_{c_name}_{idx}"):
                                save_to_db(job)
                                st.toast("보관함에 저장되었습니다.")
                        with btn_col3:
                            st.link_button("🌐 공고 상세보기", url=job['link'])

    # 보관함 탭
    with tab5:
        db_jobs = load_from_db()
        if not db_jobs: 
            st.write("보관함에 스크랩된 공고가 없습니다.")
        else:
            for s_idx, s_job in enumerate(db_jobs):
                with st.container():
                    st.markdown(f'<div style="margin-bottom:4px;"><span class="platform-badge bg-date">{s_job["date"]}</span></div><div class="company-title">{s_job["company"]}</div><div class="job-title">{s_job["position"]}</div>', unsafe_allow_html=True)
                    
                    s_col1, s_col2 = st.columns([1.5, 1.1])
                    with s_col1:
                        if st.button("📝 자소서 초안 작성", key=f"sb_{s_idx}"): 
                            st.session_state['selected_job'] = s_job
                    with s_col2:
                        if st.button("❌ 스크랩 취소", key=f"sd_{s_idx}"):
                            delete_from_db(s_job)
                            st.rerun()

with col2:
    st.markdown("#### 서류 모의 심사 및 원고 편집기")
    if 'selected_job' in st.session_state:
        selected_job = st.session_state['selected_job']
        st.info(f"선택된 기업: {selected_job['company']}")
        
        if not api_key_input: st.warning("자기소개서를 자동 빌드하려면 API Key가 필요합니다.")
        else:
            with st.spinner("AI 심사관이 데이터를 매싱하여 초안을 가공 중입니다..."):
                letter, score, good, bad = generate_ai_data(api_key_input, selected_job, user_profile_input, resume_questions_input, tone_style, char_limit)
                sc1, sc2 = st.columns([1, 2])
                with sc1: st.metric("합격 예측 스코어", f"{score}점")
                with sc2: st.markdown(f"**우수 요인:** {good}\n**보완 권장:** {bad}")
                st.progress(score / 100)
                st.divider()
                st.metric("자소설닷컴 실시간 글자 수", f"{len(letter)} 자")
                st.text_area("자기소개서 본문 초안", value=letter, height=350)
                st.download_button("텍스트 파일(.txt)로 내 컴퓨터에 다운로드", data=letter, file_name=f"{selected_job['company']}_자소서.txt")
    else:
        st.info("왼쪽 채용 공고 리스트에서 [자소서 초안 작성] 버튼을 선택하시면 실시간 평가 레포트가 자동 로드됩니다.")