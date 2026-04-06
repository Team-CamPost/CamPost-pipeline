# CamPost Pipeline

CamPost Pipeline은 학과 공지사항을 수집해 Raw JSON으로 저장하고,
크롤링/파싱 로그를 기록하는 Python 기반 수집기입니다.
최종 공지 정규화 및 제공은 CamPost Backend Importer가 담당합니다.

## 이 저장소가 하는 일

- Playwright로 학과 공지 목록/상세를 수집합니다.
- 첨부파일을 다운로드하고(PDF/HWP/HWPX 가능 시) 텍스트를 추출합니다.
- 본문 + 첨부 텍스트에서 핵심 정보를 규칙 기반으로 추출합니다.
- 공지 1건당 JSON 1개를 data/raw에 저장합니다.
- DB의 crawl_jobs, parse_logs에 수집 메타데이터를 기록합니다.

## 디렉터리 구조

```text
CamPost-pipeline/
	crawler/
		__init__.py      # 파이프라인 실행 로직(run_all, scheduler)
		config.py        # 환경변수/설정 상수
		scraper.py       # 목록/상세 수집
		file_handler.py  # 첨부 다운로드 및 텍스트 추출
		extractor.py     # 규칙 기반 핵심정보 추출
		storage.py       # raw/hash 파일 저장
		db.py            # crawl_jobs / parse_logs 기록 전용 DB 접근
	scripts/
		migrate_article_ids.py  # 1회성 마이그레이션 유틸
	tests/
		test_extractor.py
		test_storage.py
	main.py            # 루트 실행 래퍼
	requirements.txt
	Dockerfile
	.env.example
```

## 실행 흐름

1. (선택) 스케줄러가 run_all을 주기적으로 실행합니다.
2. 각 source별로 공지 목록을 가져옵니다.
3. 해시 필터링으로 신규 공지만 선별합니다.
4. 상세 페이지와 첨부파일을 처리합니다.
5. 추출된 필드를 공지 payload에 반영합니다.
6. data/raw/{article_id}.json 형태로 저장합니다.
7. crawl_jobs, parse_logs를 DB에 업데이트합니다.

## 환경변수

.env.example을 .env로 복사한 뒤 값을 채워 주세요.

필수/중요 값:

- POSTGRES_DB: DB 이름
- POSTGRES_USER: DB 사용자
- POSTGRES_PASSWORD: DB 비밀번호
- DB_HOST: DB 호스트 (compose 기본값: db)
- DB_PORT: DB 포트 (기본값: 5432)
- CRAWL_INTERVAL_MINUTES: 스케줄러 주기(분)
- HEADLESS: 브라우저 UI 표시 여부(true/false)

선택 값:

- OUTPUT_DIR: raw/file 출력 루트 (로컬 기본 ./data, 컨테이너에서는 보통 /data)

## 로컬 실행

```bash
cd CamPost-pipeline
cp .env.example .env
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
playwright install chromium
```

1회 실행:

```bash
python main.py
```

반복 실행(--loop):

```bash
python main.py --loop
```

## Docker 실행 (Pipeline 단독)

```bash
cd CamPost-pipeline
docker build -t campost-pipeline .
docker run --rm --env-file .env -v "${PWD}/data:/data" campost-pipeline
```

## 통합 Docker 실행 (Backend Compose 사용)

실제 개발에서는 db/backend/pipeline이 네트워크와 볼륨을 공유하므로
보통 backend의 compose에서 함께 실행합니다.

```bash
cd CamPost-backend
docker compose up -d db backend pipeline
docker compose logs --no-color --tail=120 pipeline
```

## 1회성 유틸 스크립트

article_id 마이그레이션 스크립트는 scripts 폴더로 이동되었습니다.

```bash
cd CamPost-pipeline
python scripts/migrate_article_ids.py
```

## 유닛 테스트

extractor와 storage를 대상으로 기본 유닛 테스트를 제공합니다.

```bash
cd CamPost-pipeline
python -m unittest discover -s tests -p "test_*.py"
```

## pre-commit / 포맷팅 설정

이 저장소는 pre-commit 훅으로 Python 포맷/린트를 자동화할 수 있습니다.

적용 방법:

```bash
cd CamPost-pipeline
pip install -e .[dev]
pre-commit install
```

수동으로 전체 파일 검사/수정:

```bash
pre-commit run --all-files
```

포함된 훅:

- 기본 파일 훅: yaml 검사, EOF 정리, trailing whitespace 제거
- Python 훅: ruff(자동 수정), ruff-format(포맷)
