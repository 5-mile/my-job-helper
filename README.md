# my-job-helper

사람인·워크넷 채용 검색과 채용 전문 네이버 블로그 피드를 한 화면에 모으고,
스크랩한 공고의 **지원 진행 상황 추적**과 **마감 임박 알림**까지 해주는 Streamlit 대시보드입니다.

## 주요 기능

| 기능 | 설명 |
| --- | --- |
| 다중 키워드 검색 | `생산, 품질, 설비`처럼 쉼표로 여러 검색어를 한 번에 수집 |
| 제외 키워드 | 회사명·공고명·직무에 특정 단어가 들어간 공고를 숨김 |
| 두 개의 채용 소스 | 사람인(스크래핑) + 워크넷 오픈API |
| 기업 분류 탭 | 대기업 / 중견기업 / 외국계 / 일반·기타 |
| 회사 규모·보수 | 국민연금 사업장 데이터로 직원 수와 월평균 보수 추정 |
| D-Day 계산 | 마감일을 파싱해 `D-10`, `D-DAY`, `상시채용`으로 표시하고 임박 공고를 강조 |
| 마감 임박 알림 | 보관 공고 중 마감 D-3 이내를 텔레그램/이메일로 발송 |
| NEW 뱃지 | 지난 실행 이후 새로 올라온 공고를 표시 |
| 지원 현황 트래커 | 관심 → 지원 예정 → 지원 완료 → 서류 합격 → 면접 진행 → 최종 합격/불합격 |
| 메모 · 지원일 | 공고별로 자소서 소재, 면접 일정 등을 기록 |
| CSV 내보내기 | 보관함 전체를 엑셀에서 열 수 있는 CSV로 저장 |
| 수집 상태 감지 | 사람인 페이지 구조가 바뀌어 0건이 되면 경고 배너로 알림 |
| 보관함 영구 저장 | 로컬은 SQLite, 클라우드 배포 시 PostgreSQL로 데이터 유지 |

## 실행 방법

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저가 자동으로 열리지 않으면 <http://localhost:8501> 로 접속하세요.

**API 키 없이도 바로 동작합니다.** 사람인 검색, 블로그 피드, 지원 트래커, CSV는 설정 없이
쓸 수 있고, 아래 선택 기능만 키가 있을 때 켜집니다.

## 선택 기능 설정

`.env.example`을 `.env`로 복사한 뒤 필요한 값만 채우세요. `.env`는 git에 올라가지 않습니다.

```bash
cp .env.example .env
```

### 1. 보관함 영구 저장 — `DATABASE_URL`

**로컬에서만 쓰신다면 설정할 필요가 없습니다.** 기본값인 SQLite(`jobs.db`)로 잘 동작합니다.

Streamlit Cloud에 배포했다면 이야기가 다릅니다. 클라우드는 파일시스템이 휘발성이라
앱이 재시작하거나 절전에서 깨어날 때마다 `jobs.db`가 사라집니다. 지원 현황과 메모를
유지하려면 외부 PostgreSQL이 필요합니다.

Supabase 무료 플랜 기준:

1. <https://supabase.com> 에서 프로젝트 생성
2. **Project Settings → Database → Connection string → URI** 복사
3. `[YOUR-PASSWORD]` 자리를 실제 비밀번호로 교체
4. 로컬 `.env`에 `DATABASE_URL=postgresql://...` 로 한 줄 넣습니다.
5. 나머지는 아래 한 번의 명령이 처리합니다:

```bash
python setup_cloud.py
```

연결 확인 → 스키마 생성 → 기존 보관함 이전 → Streamlit Secrets에 붙여넣을
파일 생성까지 순서대로 진행합니다. `.env`에 URL이 없으면 어디서 받아 어떻게
넣는지 안내하고 멈춥니다.

Secrets 내용은 **화면에 출력하지 않고 파일로 저장합니다**
(`streamlit_secrets_붙여넣기용.toml`, git에서 제외됨). 터미널 기록이나
스크린샷에 비밀번호가 남지 않게 하기 위해서입니다. 화면에 바로 보려면
`--show`를 붙이세요.

설정이 끝나면 앱 사이드바 "💾 저장소"에
`PostgreSQL · 보관함이 영구 저장됩니다`가 표시됩니다.

개별 단계를 따로 실행하고 싶다면 `check_db.py`(연결 점검)와
`migrate_db.py`(이전)를 쓰면 됩니다.

### 2. 회사 규모·보수 정보 — `NPS_SERVICE_KEY`

1. [공공데이터포털](https://www.data.go.kr)에서 **국민연금공단_국민연금 가입 사업장 내역** 활용신청
2. 마이페이지 → 인증키의 **일반 인증키(Decoding)** 값을 `.env`에 입력

켜면 공고 카드에 `👥 1,234명` `💰 월평균 380만` 뱃지가 붙고, 사이드바에서 최소 직원 수로
필터링할 수 있습니다.

> **이 숫자의 한계** — 국민연금 가입자 기준입니다. 사업장(공장·지점)별로 분리 신고되어
> 전사 인원과 다를 수 있고, 월평균 보수는 고지금액에서 역산한 추정치라
> 고연봉 회사는 기준소득월액 상한(월 637만원) 때문에 실제보다 낮게 나옵니다.
> 상한에 걸린 경우 `380만+` 처럼 `+`를 붙여 표시합니다.

### 3. 워크넷 공고 — `WORKNET_AUTH_KEY`

[워크넷 오픈API](https://openapi.work.go.kr)에서 채용정보 API 인증키를 발급받아 입력하면,
사이드바의 "워크넷 공고 함께 보기"가 활성화됩니다. 공식 API라 사람인 스크래핑과 달리
사이트 개편에 영향을 받지 않습니다.

### 4. 마감 임박 알림 — 텔레그램 또는 이메일

**텔레그램** (권장, 설정이 더 간단합니다)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)에게 `/newbot` → 봇 토큰 받기
2. 만든 봇에게 아무 메시지나 보내기
3. `https://api.telegram.org/bot<토큰>/getUpdates` 에서 `chat.id` 확인
4. `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 입력

**이메일** — Gmail은 2단계 인증을 켠 뒤 [앱 비밀번호](https://myaccount.google.com/apppasswords)를
발급받아 `SMTP_PASSWORD`에 넣으세요. 계정 비밀번호가 아닙니다.

설정 후 앱 하단의 **📨 마감 알림 보내기** 버튼으로 즉시 보내거나, 아래처럼 자동 실행하세요.

## 알림 자동 실행

앱을 열지 않아도 매일 알림을 받으려면 `notify.py`를 스케줄러에 등록합니다.

```bash
python notify.py --dry-run    # 먼저 내용만 확인
python notify.py              # 실제 발송 (마감 3일 이내)
python notify.py --days 5     # 5일 이내로 넓히기
```

**윈도우 작업 스케줄러** — 작업 만들기 → 트리거: 매일 오전 9시 → 동작: 프로그램 시작

- 프로그램: `python` (또는 `python.exe` 전체 경로)
- 인수: `notify.py`
- 시작 위치: 이 프로젝트 폴더 경로

같은 공고를 하루에 두 번 보내지 않도록 발송 기록이 남습니다.

## 보관함 이전

로컬 SQLite에 쌓아둔 보관함을 외부 DB로 옮깁니다. `DATABASE_URL`을 설정한 뒤
한 번만 실행하면 됩니다.

```bash
python migrate_db.py --dry-run          # 옮겨질 내용만 확인 (아무것도 쓰지 않음)
python migrate_db.py                    # jobs.db -> DATABASE_URL
python migrate_db.py --source 다른.db    # 다른 파일에서 옮기기
python migrate_db.py --overwrite        # 대상에 이미 있는 공고도 덮어쓰기
```

- **원본은 수정하지 않습니다.** 읽기만 하므로 실패해도 `jobs.db`는 그대로입니다.
- **여러 번 실행해도 안전합니다.** 기본 동작이 '대상에 없는 것만 추가'라서
  중복이 생기지 않습니다. 대상에서 수정한 메모도 덮어쓰지 않습니다
  (원본 내용으로 되돌리려면 `--overwrite`).
- **구버전 스키마도 읽습니다.** 컬럼이 적은 예전 `jobs.db`는 있는 컬럼만 옮기고
  나머지는 기본값이 됩니다.

옮기는 대상은 보관함 공고(메모·진행 상태·지원일 포함), '이미 본 공고' 기록,
회사 정보 캐시입니다.

> 알림 발송 기록(`alert_log`)은 옮기지 않습니다. 이 표는 공고 id를 가리키는데
> 이전 과정에서 id가 새로 부여되기 때문입니다. 영향은 이전 당일에 마감 알림이
> 한 번 더 갈 수 있다는 것뿐입니다.

## 테스트

```bash
pytest tests -q
```

네트워크 없이 도는 86개 테스트입니다. PostgreSQL 분기는 생성된 SQL을
sqlglot으로 문법 검증하고, SQLite 위에서 실제로 실행해 동작을 확인합니다
(Postgres 서버 없이도 CI에서 돕니다). GitHub Actions로 push마다 자동 실행됩니다
(`.github/workflows/tests.yml`).

## 프로젝트 구조

```
app.py                       Streamlit UI (진입점)
notify.py                    마감 알림 실행 스크립트 (스케줄러용)
setup_cloud.py               클라우드 저장소 설정 한 번에 실행 (권장)
check_db.py                  저장소 연결 점검 스크립트
migrate_db.py                보관함 이전 스크립트 (SQLite -> PostgreSQL)
jobhelper/
  config.py                  기업 분류 사전, 상태 목록, 상수
  settings.py                API 키·알림 설정 로딩 (환경변수 > .env > st.secrets)
  storage.py                 SQLite/PostgreSQL 공통 저장소 계층
  classify.py                대기업/중견/외국계 분류, 복지 키워드
  company_info.py            국민연금 사업장 데이터 조회 및 캐시
  dates.py                   마감일 파싱과 D-Day 계산
  db.py                      보관함 + 지원 현황 (구버전 DB 자동 마이그레이션)
  migrate.py                 보관함 이전 로직
  notify.py                  마감 임박 공고 탐색과 텔레그램/이메일 발송
  ui.py                      CSS와 카드 렌더링 (HTML 이스케이프 포함)
  scrapers/
    saramin.py               사람인 검색 (셀렉터 폴백 + 수집 진단)
    naver_blog.py            네이버 블로그 RSS
    worknet.py               워크넷 오픈API
tests/                       테스트 86개
```

## 데이터 저장 위치

`DATABASE_URL`이 없으면 프로젝트 루트의 `jobs.db`(SQLite)에 저장됩니다.
`JOB_HELPER_DB` 환경 변수로 경로를 바꿀 수 있습니다.

기존 버전에서 쓰던 `jobs.db`가 있어도 **데이터를 잃지 않고** 새 컬럼만 추가되어 그대로 열립니다.

## Streamlit Cloud 배포 시 주의사항

**1. 코드를 푸시한 뒤에는 앱을 재시작(Reboot)하세요.**
Streamlit Cloud는 새 커밋을 받아도 이미 메모리에 올라간 파이썬 모듈을 재사용하는
경우가 있습니다. 이때 새 `app.py`가 옛 모듈을 호출하면서
`ImportError: cannot import name ...` 이 납니다.
**Manage app → ⋮ → Reboot app** 으로 프로세스를 새로 띄우면 해결됩니다.

**2. `DATABASE_URL`을 설정하세요.** 없으면 재시작 때마다 보관함이 비워집니다 (위 참고).

**3. 사람인 수집이 0건일 수 있습니다.** 클라우드 데이터센터 IP가 차단되는 경우가
있습니다. 화면 상단에 경고 배너가 뜨면 이 경우이며, 공식 API인 워크넷
(`WORKNET_AUTH_KEY`)을 켜는 것이 해법입니다.

**4. 비밀 값은 `.env`가 아니라 Secrets에 넣으세요.** `.env`는 git에 올라가지 않으므로
클라우드에는 존재하지 않습니다. **Manage app → Settings → Secrets** 에 TOML 형식으로
입력합니다.

## 참고 사항

- 예전 버전에 있던 **★ 잡플래닛 추정 평점은 제거했습니다.** 회사명 해시로 만든 값이라
  실제 평판과 무관했고, 필터 기준으로 쓰이면서 멀쩡한 공고를 걸러낼 위험이 있었습니다.
  대신 국민연금 실데이터(직원 수·월평균 보수)를 씁니다.
- 사람인은 공식 API가 아니라 HTML을 읽습니다. 구조가 바뀌면 대체 셀렉터로 한 번 더
  시도하고, 그래도 0건이면 화면 상단에 경고를 띄웁니다.
- 사람인 공고 **상세 페이지**의 급여·복리후생은 자바스크립트로 렌더링되어
  일반 HTTP 요청으로는 읽을 수 없습니다. 급여 정보는 워크넷 공고에서만 표시됩니다.
