# CamPost Pipeline

대학교 학과 공지를 수집·가공해 제공하는 **CamPost** 서비스의 데이터 파이프라인(크롤러) 저장소입니다.
학과 홈페이지를 크롤링해 첨부파일 텍스트 추출·핵심 정보 추출·미리보기 변환을 거쳐 Raw JSON으로 저장하고, Cloudflare R2 업로드 및 Backend 적재까지 담당합니다.

> **기본 브랜치는 `dev`입니다.** 모든 작업은 `dev`에서 분기하고 `dev`로 병합합니다. `dev` 직접 push는 금지입니다.

> **CamPost는 3개 저장소로 구성됩니다.**
>
> | 저장소               | 역할                          | 스택                  |
> | -------------------- | ----------------------------- | --------------------- |
> | **CamPost-frontend** | 사용자 화면                   | React · Vite          |
> | **CamPost-backend**  | REST API · 인증 · 데이터 적재 | Spring Boot · Java    |
> | **CamPost-pipeline** | 공지 크롤링 · 가공 (현재 저장소) | Python · Playwright |
>
> Pipeline은 DB에 직접 쓰지 않습니다. Raw JSON 생성 + R2 업로드 + Backend Importer로 HTTP 전송까지가 책임 범위입니다.

---

## 1. 기술 스택

| 구분          | 사용 기술                                          |
| ------------- | -------------------------------------------------- |
| 언어          | Python 3.12                                        |
| 크롤링        | Playwright · BeautifulSoup4                        |
| 문서 파싱     | pdfplumber(PDF) · pyhwp/olefile(HWP) · zipfile(HWPX/DOCX) |
| HWP 미리보기  | RHWP(HWP→SVG) + Chrome Headless(SVG→PDF)           |
| 핵심정보 추출 | 정규식(regex) + Gemini AI 보정 (`google-genai`)    |
| 스케줄러      | APScheduler (`--loop` 모드)                        |
| 스토리지      | Cloudflare R2 (`boto3`)                            |
| 품질 도구     | ruff · ruff-format · pre-commit · unittest         |
| 운영          | GitHub Actions cron (6시간 주기 자동 수집)         |

---

## 2. 시작하기 (신규 팀원용)

### 2-1. 사전 준비

- Python 3.12

### 2-2. 설치 및 실행

```bash
# 1. 저장소 클론 후 dev 브랜치로 이동
git clone <repo-url>
cd CamPost-pipeline
git switch dev

# 2. 가상환경 생성 및 활성화
python -m venv .venv
source .venv/Scripts/activate     # Windows (Git Bash)
# source .venv/bin/activate       # macOS / Linux

# 3. 의존성 설치
pip install -r requirements.txt
playwright install chromium       # 크롤링용 브라우저

# 4. (협업용) 개발 도구 + pre-commit 훅 설치
pip install -e .[dev]
pre-commit install                # ⚠️ 최초 1회 필수 (수동 설치)

# 5. 환경 변수 설정
cp .env.example .env              # .env 값 채우기 (GEMINI_API_KEY, R2_* 등)

# 6. 실행
bash scripts/run-local.sh         # 또는: python main.py
```

> ⚠️ **`pre-commit install`은 자동이 아닙니다.** frontend의 husky와 달리 각자 한 번 실행해야
> 커밋 시 ruff lint/format이 자동 적용됩니다. 누락하면 검증 없이 커밋되어 코드가 드리프트할 수 있습니다.

### 2-3. 주요 환경 변수 (`.env`)

| 변수                              | 설명                                       |
| --------------------------------- | ------------------------------------------ |
| `GEMINI_API_KEY` · `GEMINI_MODEL` | Gemini AI 추출 (미설정 시 regex만 사용)    |
| `R2_ACCOUNT_ID` 등 `R2_*`         | Cloudflare R2 업로드 설정                  |
| `PDF_CONVERSION_ENABLED` · `PDF_PREVIEW_EXTS` | HWP/HWPX PDF 미리보기 변환 설정 |
| `RHWP_BIN` · `CHROME_BIN`         | RHWP·Chrome 실행 파일 경로 (미리보기 변환) |
| `HEADLESS` · `CRAWL_INTERVAL_MINUTES` | 크롤 동작 옵션                         |
| `POSTGRES_*` · `DB_*`             | crawl_jobs/parse_logs 모니터링 기록 (선택) |

---

## 3. 프로젝트 구조

```text
CamPost-pipeline/
├─ .github/
│  ├─ ISSUE_TEMPLATE/feature_request.md
│  ├─ workflows/
│  │  ├─ ci.yml                  # CI: ruff lint·format + unittest + 커버리지 (PR/푸시)
│  │  └─ crawl.yml               # 자동 운영: 6시간 주기 크롤링 (cron + 수동)
│  └─ pull_request_template.md
├─ crawler/                      # 크롤러 코어 패키지
│  ├─ config.py                  #   환경변수 · 상수 · 수집 소스 정의
│  ├─ scraper.py                 #   목록/상세 크롤링 (Playwright)
│  ├─ file_handler.py            #   첨부 다운로드 + 텍스트 추출 + HWP→PDF 변환
│  ├─ extractor.py               #   핵심정보 추출 (regex + Gemini 보정)
│  ├─ content.py                 #   프론트 렌더용 content_html/assets 생성
│  ├─ storage.py                 #   raw JSON · seen_hashes 저장 (중복 필터)
│  ├─ db.py                      #   crawl_jobs / parse_logs 메타데이터 기록
│  ├─ r2_uploader.py / r2_storage.py  #   Cloudflare R2 업로드
│  ├─ backend_importer.py        #   Backend Importer로 raw JSON HTTP 전송
│  ├─ reprocess.py / quality.py  #   재처리 · 본문/첨부 품질 검사
│  └─ __main__.py
├─ scripts/
│  ├─ run-local.sh               # 로컬 1회 실행
│  └─ check-local.sh             # 커밋 전 검증 (pre-commit + unittest)
├─ tests/                        # 단위 테스트 (test_*.py)
├─ main.py                       # 진입점 (1회 실행 / --loop 반복 실행)
├─ requirements.txt
├─ pyproject.toml                # ruff 설정 · 패키지 메타
├─ .pre-commit-config.yaml
└─ README.md
```

### 수집 흐름

```text
scraper(목록)
  → storage(해시 기반 중복 필터)
  → scraper(상세 본문 + 첨부)
  → file_handler(텍스트 추출 + HWP→PDF 미리보기 변환)
  → extractor(핵심정보 추출: regex + Gemini)
  → content(렌더용 content_html 생성)
  → storage(data/raw/{article_id}.json 저장)
  → R2 업로드 + Backend Importer HTTP 전송
  → db(crawl_jobs / parse_logs 기록)
```

---

## 4. 협업 워크플로우

> 모든 변경은 **이슈 → 브랜치 → 커밋 → PR → 리뷰 → `dev` 머지**의 흐름을 따릅니다.

### 4-1. 브랜치 전략

- **`dev` = 기본(default) 브랜치**, 개발 통합용 — **직접 push 금지**
- 기능 브랜치는 `dev`에서 분기 후 PR로 `dev`에 병합
- 머지 방식: **Merge commit** (PR 단위 이력·개별 커밋 보존)

```bash
git switch dev
git pull origin dev
git switch -c feat/12-crawler-source   # 타입/이슈번호-기능명
```

### 4-2. 작업 순서 (Step by Step)

1. **이슈 발행** — 하나의 이슈 = 하나의 기능. 템플릿(`.github/ISSUE_TEMPLATE`) 사용, Assignee·Label·체크리스트 작성
2. **로컬 최신화** — `git switch dev && git pull origin dev`
3. **브랜치 생성** — `타입/이슈번호-기능명`
4. **개발 & 검증** — 커밋 전 `bash scripts/check-local.sh`(ruff + unittest) 실행 권장
5. **푸시 & PR 생성** — PR 템플릿 작성, `Closes #이슈번호` 연결
6. **CI 자동 검증** — ruff lint·format + 단위 테스트 통과 확인
7. **코드 리뷰** — **1명 이상 Approve 필수**
8. **`dev` 머지** — 다음 크롤링 실행부터 최신 코드 반영

### 4-3. 네이밍 컨벤션

| 항목        | 규칙                         | 예시                          |
| ----------- | ---------------------------- | ----------------------------- |
| 브랜치      | `타입/이슈번호-기능명`       | `feat/35-r2-upload`           |
| 커밋 메시지 | `타입: 설명 (#이슈번호)`     | `feat: R2 업로드 연동 (#35)`  |
| PR 제목     | `타입(#이슈번호): 핵심 내용` | `Feat(#41): 스케줄 크롤러`    |

**사용 타입**: `feat`(기능) · `fix`(버그) · `refactor`(구조 개선) · `style`(스타일) · `chore`(설정) · `docs`(문서)

### 4-4. 코드 스타일

- **ruff + ruff-format** (line-length 100, Python 3.12, double quotes)
- pre-commit 훅이 커밋 시 ruff lint(`--fix`)·format을 자동 적용 (`pre-commit install` 필요)
- ⚠️ pre-commit은 로컬 1차 방어선일 뿐, **최종 검증은 CI가 강제**합니다

---

## 5. CI / 자동 운영

### 5-1. CI — `.github/workflows/ci.yml`

PR 생성 및 `dev` 푸시 시 자동 실행됩니다.

| 단계         | 명령                                    |
| ------------ | --------------------------------------- |
| Ruff lint    | `ruff check .`                          |
| Ruff format  | `ruff format --check .`                 |
| Unit tests   | `unittest` + `coverage` (커버리지 측정) |

- 공급망 보안: 액션을 **커밋 SHA로 고정**, `persist-credentials: false`

### 5-2. 자동 운영 — `.github/workflows/crawl.yml`

파이프라인은 상시 서버가 아니라 **GitHub Actions cron으로 주기 실행**됩니다. (Render Worker 대체)

- **6시간마다 자동 실행** (`cron: "0 */6 * * *"`) + 수동 실행 버튼(`workflow_dispatch`)
- 실행 시: RHWP·한글 폰트·Playwright 설치 → `python main.py` 1회 실행
- 결과를 **R2 업로드** + **Backend(`/api/v1/importer/raw`)로 HTTP push**
- 중복 방지 해시(seen_hashes)는 `actions/cache`로 실행 간 보존
- `dev` 머지 시 별도 배포 없이 **다음 크롤링 실행부터 최신 코드 반영**

---

## 6. 실행 명령어

```bash
# 실행
python main.py                 # 1회 실행
python main.py --loop          # APScheduler 반복 실행
bash scripts/run-local.sh      # 로컬 1회 실행 스크립트

# 검증
bash scripts/check-local.sh    # 커밋 전 검증 (pre-commit + unittest)
python -m unittest discover -s tests -p "test_*.py"   # 전체 테스트
ruff check . && ruff format --check .                 # lint + format
```

---

## 7. 협업 원칙 요약

- 작은 단위의 **이슈 / 브랜치 / PR**로 나눠 작업합니다.
- 규칙 기반 네이밍과 템플릿으로 커뮤니케이션 비용을 줄입니다.
- **`dev` 직접 push 금지** — 모든 변경은 PR + 1명 이상 리뷰를 거칩니다.
- 코드 품질(ruff/테스트/CI)과 자동 운영(crawl.yml cron)으로 일관성과 안정성을 유지합니다.
