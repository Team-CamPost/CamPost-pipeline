# CamPost Pipeline

CamPost Pipeline 저장소의 협업 워크플로우와 실행 규칙을 정리한 문서입니다.
신규 팀원이 합류해도 이 문서만으로 이슈 발행, 브랜치 작업, 실행/검증 루틴을 바로 따라갈 수 있도록 구성했습니다.

## 1. 저장소 역할

CamPost Pipeline은 학과 공지를 수집/가공해 Raw JSON으로 저장하는 Python 수집기입니다.
최종 공지 정규화 및 API 제공은 CamPost Backend Importer가 담당합니다.

핵심 역할:

- Playwright 기반 목록/상세 크롤링
- 첨부파일 다운로드 및 텍스트 추출(PDF/HWP/HWPX)
- 본문+첨부 텍스트에서 핵심 정보(deadline/target/apply_method) 추출
- 공지 1건당 JSON 1개를 data/raw에 저장
- crawl_jobs, parse_logs 메타데이터 기록

## 2. 개발 전 작업

### 2-1. Issue 발행

- 이슈 하나(브랜치 하나)에서는 하나의 기능만 개발합니다.
- 이슈 제목 규칙:
  - [이슈종류] 이슈 제목
  - 예시: [Feat] example API 구현, [Fix] dev 브랜치 충돌 해결
- 이슈 템플릿:
  - .github/ISSUE_TEMPLATE/feature_request.md
- 이슈 작성 시 필수:
  - Assignees 지정
  - Labels 지정
  - 작업 체크리스트 작성

### 2-2. 로컬 최신화

개발 시작 전 반드시 최신 변경 사항을 반영합니다.

```bash
git fetch
git pull
```

## 3. 브랜치 전략

### 3-1. Git Flow 기반 운영

- dev 브랜치: default 브랜치, 개발 통합용
- 기능 브랜치: dev에서 분기 후 dev로 병합

### 3-2. 브랜치 네이밍 규칙

- 규칙: 타입/이슈번호-기능명
- 예시:
  - feat/12-init-project
  - fix/3-add-login
  - refactor/22-cart-page
  - docs/9-readme

사용 타입:

| 타입 | 설명 |
| --- | --- |
| chore | 프로젝트 설정 |
| docs | 문서 수정 |
| feat | 기능 개발 |
| fix | 버그 수정 |
| refactor | 구조 개선 |
| style | 스타일 수정 |

## 4. 개발 후 Commit & Push

### 4-1. 코드 정리 규칙

- 커밋 전 로컬 검증 권장:

```bash
bash scripts/check-local.sh
```

- Python 코드는 pre-commit, ruff, ruff-format 기준을 준수합니다.
- dev 브랜치 직접 push는 금지합니다.

### 4-2. 커밋 메시지 규칙

- 규칙: 타입: 커밋 설명 (#이슈번호)
- 예시:

```bash
git commit -m "feat: 로그인 구현 (#9)"
git commit -m "fix: 카드 페이지 수정 (#10)"
git commit -m "refactor: 아이콘 리팩토링 (#13)"
```

- 커밋 Body에는 변경 이유와 테스트 결과를 상세히 작성합니다.

## 5. PR 생성 및 Merge

### 5-1. PR 제목/본문 규칙

- PR 제목 규칙: 타입(#이슈번호): 핵심 PR 내용
- 예시:
  - Feat(#9): 로그인 구현
  - Fix(#10): 카드 페이지 수정
  - Refactor(#13): 아이콘 리팩토링
- PR 템플릿:
  - .github/pull_request_template.md

### 5-2. 리뷰 및 머지 규칙

- PR 작성 후 Reviewer, Assignee, Label을 지정합니다.
- 테스트 결과(로그/스크린샷)를 PR에 첨부합니다.
- dev 브랜치 머지는 1명 이상의 Approve 이후 진행합니다.

## 6. 표준 개발 워크플로우

아래 순서로 팀 협업을 진행합니다.

1. Issue 발행
2. 브랜치 생성 (타입/이슈번호-기능명)
3. 기능 개발 및 테스트
4. Commit & Push
5. PR 생성 (템플릿 작성 + 테스트 결과 첨부)
6. 코드 리뷰 반영
7. Approve 후 dev 머지

## 7. 파이프라인 실행/검증 가이드

### 7-1. 환경변수 준비

.env.example을 .env로 복사한 뒤 값을 채워 주세요.

```bash
cp .env.example .env
```

Windows PowerShell/CMD:

```bash
copy .env.example .env
```

필수/중요 값:

- POSTGRES_DB
- POSTGRES_USER
- POSTGRES_PASSWORD
- DB_HOST (compose 기본: db)
- DB_PORT (기본: 5432)
- CRAWL_INTERVAL_MINUTES
- HEADLESS

선택 값:

- OUTPUT_DIR (로컬 기본 ./data, 컨테이너 기본 /data)

### 7-2. 로컬 개발 루프 (권장)

최초 1회:

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
playwright install chromium
```

로컬 1회 실행:

```bash
bash scripts/run-local.sh
```

로컬 검증:

```bash
bash scripts/check-local.sh
```

직접 실행도 가능:

```bash
python main.py
python main.py --loop
```

### 7-3. 통합 환경 검증 (Backend Compose)

실제 개발에서는 보통 backend compose에서 db/backend/pipeline을 함께 실행합니다.

```bash
cd ../CamPost-backend
docker compose up -d db backend pipeline
docker compose logs --no-color --tail=120 pipeline
```

PR 전 최종 통합 검증은 backend 저장소의 스모크 스크립트를 사용합니다.

```bash
cd ../CamPost-backend
bash scripts/compose-smoke.sh
```

## 8. 프로젝트 폴더 구조

아래는 pipeline 저장소의 핵심 구조입니다.

```text
CamPost-pipeline/
├─ .github/
│  ├─ ISSUE_TEMPLATE/
│  │  └─ feature_request.md
│  └─ pull_request_template.md
├─ crawler/
│  ├─ __init__.py          # run_all, scheduler 엔트리
│  ├─ config.py            # 환경변수/상수
│  ├─ scraper.py           # 목록/상세 크롤링
│  ├─ file_handler.py      # 첨부 다운로드/텍스트 추출
│  ├─ extractor.py         # 핵심 정보 추출(regex + AI)
│  ├─ storage.py           # raw/seen_hashes 저장
│  └─ db.py                # crawl_jobs/parse_logs 기록
├─ scripts/
│  ├─ run-local.sh
│  ├─ check-local.sh
│  └─ migrate_article_ids.py
├─ tests/
│  ├─ test_extractor.py
│  └─ test_storage.py
├─ data/
├─ .env.example
├─ .pre-commit-config.yaml
├─ pyproject.toml
├─ requirements.txt
├─ Dockerfile
├─ main.py
└─ README.md
```

## 9. 수집 흐름 요약

1. 소스별 목록 조회
2. 해시 기반 중복 필터링
3. 상세 본문/첨부 수집
4. 핵심 정보 추출(regex + Gemini 보정)
5. data/raw/{article_id}.json 저장
6. crawl_jobs/parse_logs 기록

## 10. 품질 관리 기준

- pre-commit 훅으로 기본 파일/파이썬 포맷 자동 검사
- ruff + ruff-format으로 코드 스타일 일관성 유지
- 유닛 테스트(test_extractor, test_storage)로 핵심 로직 검증

## 11. 협업 원칙 요약

- 작은 단위 이슈/브랜치/PR로 작업합니다.
- 템플릿 기반 문서화를 통해 리뷰 비용을 줄입니다.
- 로컬 개발 루프(run-local/check-local)와 통합 검증(compose-smoke)을 분리해 안정성을 확보합니다.
